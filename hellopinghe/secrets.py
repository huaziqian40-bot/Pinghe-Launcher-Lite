"""密钥存储: 账号密码/授权码等敏感值.

全平台统一方案: 加密后存数据目录 secrets.json, 文件权限 0600。
- 加密: XOR 流密码(SHA-256 计数器模式) + 随机密钥(.secret_key)
- 不依赖 keyring/钥匙串 → macOS 不会弹安全确认框
- 数据随安装目录走(便携)
- .secret_key 和 secrets.json 都 chmod 600(仅所有者可读写)

威胁模型: 防止局域网/同机其他普通用户读取; 不防 root/物理接触。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from . import paths

_KEYFILE = "secrets.json"
_KEYFILE_KEY = ".secret_key"


def _keyfile() -> Path:
    return paths.data_dir() / _KEYFILE


def _secret_key() -> bytes:
    """首次运行生成 32 字节随机密钥, 之后复用."""
    kf = paths.data_dir() / _KEYFILE_KEY
    if kf.exists():
        return kf.read_bytes()
    key = os.urandom(32)
    kf.parent.mkdir(parents=True, exist_ok=True)
    kf.write_bytes(key)
    try:
        os.chmod(kf, 0o600)
    except Exception:  # noqa: BLE001
        pass
    return key


def _xor_stream(data: bytes, key: bytes) -> bytes:
    """SHA-256 计数器模式 XOR 流."""
    out = bytearray()
    counter = 0
    while len(out) < len(data):
        block = hashlib.sha256(key + counter.to_bytes(4, "big")).digest()
        chunk = data[counter * 32:(counter + 1) * 32]
        out.extend(bytes(a ^ b for a, b in zip(chunk, block)))
        counter += 1
    return bytes(out[:len(data)])


def _load() -> dict:
    f = _keyfile()
    if not f.exists():
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        key = _secret_key()
        out: dict[str, str] = {}
        for k, v in raw.items():
            try:
                blob = base64.b64decode(v)
                out[k] = _xor_stream(blob, key).decode("utf-8")
            except Exception:  # noqa: BLE001
                continue
        return out
    except Exception:  # noqa: BLE001
        return {}


def _save(store: dict) -> None:
    f = _keyfile()
    f.parent.mkdir(parents=True, exist_ok=True)
    key = _secret_key()
    raw = {}
    for k, v in store.items():
        encrypted = _xor_stream(v.encode("utf-8"), key)
        raw[k] = base64.b64encode(encrypted).decode("ascii")
    f.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except Exception:  # noqa: BLE001
        pass


def get(key: str) -> str | None:
    return _load().get(key)


def set(key: str, value: str) -> bool:
    if not value:
        delete(key)
        return True
    store = _load()
    store[key] = value
    _save(store)
    return True


def delete(key: str) -> None:
    store = _load()
    store.pop(key, None)
    _save(store)
