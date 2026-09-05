"""密钥存储: 账号密码/授权码等敏感值.

- Windows: 用 DPAPI(当前用户绑定)加密后存数据目录的 secrets.json
  —— 满足"所有数据都在安装文件夹"的便携要求, 且退出登录用户即不可解密
- macOS/Linux: keyring(Keychain 等)

统一 API: get(key) / set(key, value) / delete(key)。key 形如
"mail_authcode:user@host"。
"""
from __future__ import annotations

import base64
import json
import sys

from . import paths

_KEYFILE = "secrets.json"


def _keyfile() -> Path:
    return paths.data_dir() / _KEYFILE


# ---------------------------------------------------------------- DPAPI(Windows)
def _dpapi_protect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError("CryptProtectData 失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError("CryptUnprotectData 失败(数据属于其他用户/机器?)")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


# ---------------------------------------------------------------- 文件读写
def _load() -> dict:
    f = _keyfile()
    if not f.exists():
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        try:
            out[k] = _dpapi_unprotect(base64.b64decode(v)).decode("utf-8")
        except Exception:  # noqa: BLE001
            continue   # 解不开(换机器/换用户)的条目按不存在处理
    return out


def _save(store: dict) -> None:
    f = _keyfile()
    f.parent.mkdir(parents=True, exist_ok=True)
    raw = {k: base64.b64encode(_dpapi_protect(v.encode("utf-8"))).decode("ascii")
           for k, v in store.items()}
    f.write_text(json.dumps(raw, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 统一 API
def get(key: str) -> str | None:
    if sys.platform == "win32":
        return _load().get(key)
    import keyring

    try:
        return keyring.get_password("hellopinghe", key)
    except Exception:  # noqa: BLE001
        return None


def set(key: str, value: str) -> bool:
    if not value:
        return delete(key)
    if sys.platform == "win32":
        store = _load()
        store[key] = value
        _save(store)
        return True
    import keyring

    try:
        keyring.set_password("hellopinghe", key, value)
        return True
    except Exception:  # noqa: BLE001
        return False


def delete(key: str) -> None:
    if sys.platform == "win32":
        store = _load()
        if key in store:
            store.pop(key)
            _save(store)
        return
    import keyring

    try:
        keyring.delete_password("hellopinghe", key)
    except Exception:  # noqa: BLE001
        pass
