"""凭据存储: 直接写在 ``data/settings.yaml`` 里(用户约定: 四平台密码都归这个文件).

统一入口仍是 ``get/set/delete(key)``, 上层代码不用改; 内部把已知的键
映射到 settings.yaml 的结构化字段, 未知键落到 ``secrets_extra``::

    accounts:
      edupage:   {username, subdomain, password}
      managebac: {base_url, email, password}
      mail:      {email, imap_host, smtp_host, password, authcode}
      xinlv:     {username, token}
    secrets_extra: { "<原始键>": "<值>" }

键的映射规则(与旧版一致):
    edupage:{subdomain}:{username} → accounts.edupage.password
    managebac:{base_url}           → accounts.managebac.password
    mail:{email}                   → accounts.mail.password
    mail_authcode:{email}          → accounts.mail.authcode
    xinlv:token                    → accounts.xinlv.token

旧数据: ``data/secrets.json``(XOR 流加密, 密钥在同目录 ``.secret_key``)
与更旧的 keyring 条目会在首次读取时一次性迁移进 settings.yaml,
旧文件由 filestore 挪进 ``data/_migrated_backup/``(保留不删)。
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from . import filestore as fs
from . import paths

_LEGACY_SECRETS = "secrets.json"
_LEGACY_KEYFILE = ".secret_key"

_MIGRATED = {"done": False}


# ---------------------------------------------------------------- 键映射
def _map_key(doc: dict, key: str) -> tuple[str, str] | None:
    """凭据键 → (accounts 子段, 字段名); 认不出来返回 None."""
    accounts = doc.setdefault("accounts", {})
    if key.startswith("edupage:"):
        accounts.setdefault("edupage", {})
        return "edupage", "password"
    if key.startswith("managebac:"):
        accounts.setdefault("managebac", {})
        return "managebac", "password"
    if key.startswith("mail_authcode:"):
        accounts.setdefault("mail", {})
        return "mail", "authcode"
    if key.startswith("mail:"):
        accounts.setdefault("mail", {})
        return "mail", "password"
    if key == "xinlv:token":
        accounts.setdefault("xinlv", {})
        return "xinlv", "token"
    return None


def _read(doc: dict, key: str) -> str | None:
    mapped = _map_key(doc, key)
    if mapped is None:
        return (doc.get("secrets_extra") or {}).get(key)
    section, field = mapped
    return ((doc.get("accounts") or {}).get(section) or {}).get(field)


def _write(doc: dict, key: str, value: str | None) -> None:
    mapped = _map_key(doc, key)
    if mapped is None:
        extra = doc.setdefault("secrets_extra", {})
        if value:
            extra[key] = value
        else:
            extra.pop(key, None)
        return
    section, field = mapped
    doc.setdefault("accounts", {}).setdefault(section, {})
    if value:
        doc["accounts"][section][field] = value
        # 顺手补上人类可读的账号信息(便于 PHL 直接读同一份配置)
        if section == "edupage" and key.count(":") == 2:
            _, sub, user = key.split(":", 2)
            doc["accounts"][section].setdefault("subdomain", sub)
            doc["accounts"][section].setdefault("username", user)
        elif section == "mail" and key.startswith("mail:"):
            doc["accounts"][section].setdefault("email", key.split(":", 1)[1])
        elif section == "managebac":
            doc["accounts"][section].setdefault("base_url", key.split(":", 1)[1])
    else:
        doc["accounts"][section].pop(field, None)


# ---------------------------------------------------------------- 公开接口
def get(key: str) -> str | None:
    _migrate_once()
    return _read(fs.load_settings(), key)


def set(key: str, value: str) -> bool:  # noqa: A001  (与旧接口同名)
    _migrate_once()
    if not value:
        delete(key)
        return True

    def mutate(doc):
        _write(doc, key, value)

    fs.update_settings(mutate)
    return True


def delete(key: str) -> None:
    _migrate_once()

    def mutate(doc):
        _write(doc, key, None)

    fs.update_settings(mutate)


# ---------------------------------------------------------------- 旧数据迁移
def _legacy_xor(data: bytes, key: bytes) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < len(data):
        block = hashlib.sha256(key + counter.to_bytes(4, "big")).digest()
        chunk = data[counter * 32:(counter + 1) * 32]
        out.extend(bytes(a ^ b for a, b in zip(chunk, block)))
        counter += 1
    return bytes(out[:len(data)])


def _read_legacy_secrets() -> dict[str, str]:
    """解密旧版 ``data/secrets.json``(XOR 流 + .secret_key)."""
    root = paths.data_dir()
    f = root / _LEGACY_SECRETS
    kf = root / _LEGACY_KEYFILE
    if not (f.exists() and kf.exists()):
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        key = kf.read_bytes()
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        try:
            out[k] = _legacy_xor(base64.b64decode(v), key).decode("utf-8")
        except Exception:  # noqa: BLE001
            continue
    return out


def _read_keyring(key: str) -> str | None:
    """更旧版本把凭据放在 keyring(Windows 凭据管理器)里, 尽力捞一次."""
    try:
        import keyring

        for service in ("hellopinghe", "schoolhub"):
            try:
                val = keyring.get_password(service, key)
                if val:
                    return val
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return None


def _migrate_once() -> None:
    """把旧凭据搬进 settings.yaml(每次进程只跑一次, 幂等)."""
    if _MIGRATED["done"]:
        return
    _MIGRATED["done"] = True
    try:
        legacy = _read_legacy_secrets()
        settings = fs.load_settings()
        missing = [k for k in legacy if not _read(settings, k)]
        # 旧 keyring 里的四条平台凭据也捞一下
        for key in ("xinlv:token",):
            if key not in missing and not _read(settings, key):
                val = _read_keyring(key)
                if val:
                    legacy[key] = val
                    missing.append(key)
        if not missing:
            return

        def mutate(doc):
            for k in missing:
                if legacy.get(k):
                    _write(doc, k, legacy[k])

        fs.update_settings(mutate)
    except Exception:  # noqa: BLE001
        pass
