"""phix 客户端密码学（PLL 侧权威实现；PHL 侧 electron/phix-crypto.cjs 与之逐字节一致）。

三层密钥（详见 D:\\phix\\phix-协议规范.md §2）：

    口令 --scrypt--> KEK --解开--> DEK --HKDF--> 对象密钥 --AES-GCM--> 密文信封

**为什么是三层而不是"口令直接加密数据"**：换口令时只需重新包裹 DEK 一次，
云端所有密文一个字节都不用动；忘记口令时还能用恢复码解出同一把 DEK。

本模块只依赖标准库 + cryptography，不碰网络、不碰文件，便于单测。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import unicodedata

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# ---- 协议常量（与规范 §2.4 一致；两端必须完全一致，改这里 = 改协议） ----
ENVELOPE_PREFIX = "PHIX1."
KDF_ALGO = "scrypt-n15-r8-p1"
SCRYPT_N = 32768                  # 2^15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024  # 必须显式给：默认 32MiB 装不下 N=2^15,r=8
DEK_BYTES = 32
NONCE_BYTES = 12
SALT_BYTES = 16
OBJECT_KEY_SALT = b"phix/v1/object-keys"
KEYCHECK_NAME = "__keycheck__"
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去掉易混的 I O 0 1
RECOVERY_CHARS = 24


# ---------------- 编码 ----------------

def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


#: base64url 的合法字符（无填充）
_B64URL_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def b64d(text: str) -> bytes:
    """严格解码 base64url（无填充）。

    标准库的 ``urlsafe_b64decode`` 默认**静默丢弃非法字符**（``"@@@@"`` 会被当成空串），
    这在解析外部来的信封时太宽松了 —— 这里显式校验字符集与回环一致，
    与 Node 侧 ``Buffer.from(s,'base64url')`` 的严格行为对齐。
    """
    if not isinstance(text, str):
        raise ValueError("base64url 必须是字符串")
    if any(ch not in _B64URL_CHARS for ch in text):
        raise ValueError("信封格式损坏（含非 base64url 字符）")
    if len(text) % 4 == 1:
        raise ValueError("信封格式损坏（base64url 长度非法）")
    pad = (4 - len(text) % 4) % 4
    raw = base64.urlsafe_b64decode(text + "=" * pad)
    if b64e(raw) != text:
        raise ValueError("信封格式损坏（base64url 未规范化）")
    return raw


#: 与 JavaScript ``String.prototype.trim()`` **完全一致**的空白集合。
#: Python 的 ``str.strip()`` 用的是 ``str.isspace()``，比 JS 多认 U+001C–U+001F、
#: U+0085，又少认 U+FEFF —— 两端不一致会让同一条口令派生出不同的 KEK。
_JS_TRIM_CHARS = "".join(
    [chr(c) for c in range(0x09, 0x0E)] +            # TAB LF VT FF CR
    ["\u0020", "\u00a0", "\u1680"] +
    [chr(c) for c in range(0x2000, 0x200B)] +        # EN QUAD … HAIR SPACE
    ["\u2028", "\u2029", "\u202f", "\u205f", "\u3000", "\ufeff"]
)


def normalize_passphrase(s: str) -> bytes:
    """口令归一化：NFKC + 去掉首尾空白（**按 JS trim 的空白集**）→ UTF-8 字节。"""
    if not isinstance(s, str):
        raise TypeError("口令必须是字符串")
    return unicodedata.normalize("NFKC", s).strip(_JS_TRIM_CHARS).encode("utf-8")


def new_salt() -> str:
    return secrets.token_hex(SALT_BYTES)


# ---------------- AAD：两族，各自绑定的东西不同 ----------------
#
# 身份族绑 username：注册**之前**就要算好 key_wrap / recovery_wrap / key_check，
#   那时还拿不到 user_id，所以身份族只能用 username。
# 对象族绑 user_id + 对象名：同步发生在登录之后，user_id 已知，
#   绑死"谁 + 哪个文件"，服务端张冠李戴就解不开。

def aad_identity(username: str) -> bytes:
    return f"phix/v1/identity|{username}".encode("utf-8")


def aad_object(user_id: int, name: str) -> bytes:
    return f"phix/v1/object|{user_id}|{name}".encode("utf-8")


# ---------------- KDF ----------------
#
# 两代派生方式（对应 `加密链路思路.md` §2.2）：
#
#   v1（老账号）  口令 --scrypt--> KEK                  KEK 直接用来包 DEK
#   v2（新账号）  口令 --scrypt--> MK --HKDF("auth")--> AuthHash  ← 发给服务器
#                                   --HKDF("enc") --> KEK       ← 永不出客户端
#
# **v2 的意义**：服务器只拿到 AuthHash，而 `AuthHash = HKDF(MK)` 是**单向**的 ——
# 它反推不出 MK，也就永远算不出 KEK。于是**即使服务器被入侵、即使它记下了登录时收到的东西，
# 也解不开用户云端的数据**。v1 则做不到这一点（服务器见过口令，等于见过钥匙）。

KDF_ALGO_V1 = "scrypt-n15-r8-p1"
KDF_ALGO_V2 = "scrypt-hkdf-v2"
KDF_ALGO = KDF_ALGO_V2          # 新账号默认用 v2
KDF_ALGOS = (KDF_ALGO_V1, KDF_ALGO_V2)
AUTH_INFO = b"phix/v1/auth"
ENC_INFO = b"phix/v1/enc"


# ---- MK 缓存（**只是省时间，不改变任何密码学行为**） ----
#
# 为什么需要：登录一次要跑**两次** scrypt —— 一次算 AuthHash、一次解 DEK，
# 而这两次输入完全相同（同一个口令、同一个盐、同一组参数）。
# 实测每次约 150 ms（N=2^15），也就是**每次登录白花 150 ms**。
#
# 边界（必须守住）：
#   · 只在**进程内存**里，绝不落盘；进程退出即消失。
#   · 键 = (口令, 盐, 参数)，所以换账号/换盐不会串味。
#   · 有上限（8 条）与 TTL（300 秒），避免长期驻留。
#   · `clear_mk_cache()` 在登出/切换账号时调用，立刻抹掉。
MK_CACHE_MAX = 8
MK_CACHE_TTL = 300.0
_mk_cache: dict = {}
_mk_lock = threading.Lock()
_mk_stats = {"hit": 0, "miss": 0}


def clear_mk_cache():
    """把缓存里的 MK 抹掉（登出、切换账号、改口令之后都应该调）。"""
    with _mk_lock:
        _mk_cache.clear()


def mk_cache_stats() -> dict:
    """给测试与诊断用：命中/未命中次数与当前条数。"""
    with _mk_lock:
        return {**_mk_stats, "size": len(_mk_cache)}


def _mk_cache_get(key):
    now = time.monotonic()
    with _mk_lock:
        hit = _mk_cache.get(key)
        if hit and now - hit[0] <= MK_CACHE_TTL:
            _mk_stats["hit"] += 1
            return hit[1]
        if hit:
            _mk_cache.pop(key, None)
        _mk_stats["miss"] += 1
        return None


def _mk_cache_put(key, mk: bytes):
    now = time.monotonic()
    with _mk_lock:
        _mk_cache[key] = (now, mk)
        # 先清过期的，再按"最久没用"裁到上限
        for k in [k for k, (t, _v) in _mk_cache.items() if now - t > MK_CACHE_TTL]:
            _mk_cache.pop(k, None)
        while len(_mk_cache) > MK_CACHE_MAX:
            oldest = min(_mk_cache.items(), key=lambda kv: kv[1][0])[0]
            _mk_cache.pop(oldest, None)


def derive_mk(passphrase: str, salt_hex: str) -> bytes:
    """口令 → MK（两代共用这一步：都是 scrypt）。

    **带进程内缓存**：同样的 (口令, 盐, 参数) 只算一次 scrypt。
    登录时"算 AuthHash"与"解 DEK"用的就是同一组输入，于是省下一次 ~150 ms。
    """
    key = (normalize_passphrase(passphrase), salt_hex, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    cached = _mk_cache_get(key)
    if cached is not None:
        return cached
    mk = hashlib.scrypt(
        key[0],
        salt=_salt_bytes(salt_hex),
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=DEK_BYTES, maxmem=SCRYPT_MAXMEM,
    )
    _mk_cache_put(key, mk)
    return mk


def derive_kek(passphrase: str, salt_hex: str, algo: str = KDF_ALGO) -> bytes:
    """口令 → KEK（32 字节）。`algo` 决定用哪一代；v1 的 KEK 就是 MK。"""
    mk = derive_mk(passphrase, salt_hex)
    if algo == KDF_ALGO_V1:
        return mk
    if algo != KDF_ALGO_V2:
        raise ValueError(f"不认识的 KDF：{algo}")
    return _hkdf_sha256(mk, _salt_bytes(salt_hex), ENC_INFO, DEK_BYTES)


def derive_auth_hash(passphrase: str, salt_hex: str, algo: str = KDF_ALGO) -> bytes:
    """口令 → AuthHash（32 字节）。**这是唯一可以发给服务器的东西。**

    v1 账号没有这个概念（服务器直接比对口令原文），调用它会报错——
    那种账号只能继续走老路径。
    """
    if algo == KDF_ALGO_V1:
        raise ValueError("v1 账号没有 AuthHash（它只能发口令原文）")
    if algo != KDF_ALGO_V2:
        raise ValueError(f"不认识的 KDF：{algo}")
    mk = derive_mk(passphrase, salt_hex)
    return _hkdf_sha256(mk, _salt_bytes(salt_hex), AUTH_INFO, DEK_BYTES)


def auth_hash_hex(passphrase: str, salt_hex: str, algo: str = KDF_ALGO) -> str:
    """给服务器发的那串东西（hex）。"""
    return derive_auth_hash(passphrase, salt_hex, algo).hex()


def uses_auth_hash(algo: str | None) -> bool:
    """这个账号的 KDF 版本是否需要发 AuthHash（而不是口令原文）。"""
    return (algo or KDF_ALGO) != KDF_ALGO_V1


def _salt_bytes(salt_hex: str) -> bytes:
    """把盐从 hex 解出来，并**校验长度**（规范只允许 16 字节）。"""
    if not isinstance(salt_hex, str):
        raise TypeError("盐必须是十六进制字符串")
    try:
        raw = bytes.fromhex(salt_hex)
    except ValueError as exc:
        raise ValueError("盐不是合法的十六进制") from exc
    if len(raw) != SALT_BYTES:
        raise ValueError(f"盐长度不对（应为 {SALT_BYTES} 字节，实际 {len(raw)}）")
    return raw


def _hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """HKDF-SHA256。Node 侧对应 `crypto.hkdfSync('sha256', ...)`。"""
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt,
                info=info).derive(ikm)


def derive_object_key(dek: bytes, name: str) -> bytes:
    """DEK + 对象名 → 对象密钥。**只依赖 DEK 与名字**，所以换口令不会让密文作废。"""
    return HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=OBJECT_KEY_SALT, info=name.encode("utf-8"),
    ).derive(dek)


# ---------------- 信封 ----------------

def seal(key: bytes, plaintext: bytes, aad: bytes) -> str:
    """加密 → 信封字符串 `PHIX1.<b64 nonce>.<b64 ct||tag>`。"""
    nonce = os.urandom(NONCE_BYTES)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return f"{ENVELOPE_PREFIX}{b64e(nonce)}.{b64e(ct)}"


def unseal(key: bytes, envelope: str, aad: bytes) -> bytes:
    """解密信封；口令 / 名字 / 用户不对会抛 InvalidTag（cryptography.exceptions）。"""
    if not isinstance(envelope, str) or not envelope.startswith(ENVELOPE_PREFIX):
        raise ValueError("不是 PHIX1 信封")
    rest = envelope[len(ENVELOPE_PREFIX):]
    try:
        nonce_s, ct_s = rest.split(".", 1)
        nonce, ct = b64d(nonce_s), b64d(ct_s)
    except (ValueError, TypeError) as exc:
        raise ValueError("信封格式损坏") from exc
    return AESGCM(key).decrypt(nonce, ct, aad)


def seal_object(dek: bytes, user_id: int, name: str, plaintext: bytes) -> str:
    return seal(derive_object_key(dek, name), plaintext, aad_object(user_id, name))


def unseal_object(dek: bytes, user_id: int, name: str, envelope: str) -> bytes:
    return unseal(derive_object_key(dek, name), envelope, aad_object(user_id, name))


# ---------------- DEK 包裹 / 解包（身份族 AAD） ----------------

def wrap_dek(dek: bytes, passphrase: str, salt_hex: str, username: str,
             algo: str = KDF_ALGO) -> str:
    return seal(derive_kek(passphrase, salt_hex, algo), dek, aad_identity(username))


def unwrap_dek(envelope: str, passphrase: str, salt_hex: str, username: str,
               algo: str = KDF_ALGO) -> bytes:
    dek = unseal(derive_kek(passphrase, salt_hex, algo), envelope, aad_identity(username))
    if len(dek) != DEK_BYTES:
        raise ValueError("DEK 长度不对")
    return dek


def make_key_check(dek: bytes, username: str, plain_hex: str) -> str:
    """自检块：用 `__keycheck__` 的对象密钥加密**一串随机明文**（不是公开常量）。

    明文由服务端保存且**任何接口都不下发**，所以"报一串常量"伪造不了
    「我手里有 DEK」这件事；只有解开这个信封才能拿到它。
    """
    return seal(derive_object_key(dek, KEYCHECK_NAME),
                bytes.fromhex(plain_hex), aad_identity(username))


def new_key_check_plain() -> str:
    return secrets.token_hex(32)


def prove_dek(dek: bytes, username: str, key_check: str) -> str:
    """解出服务端存的自检块明文（hex）。解不开 → 口令/DEK 不对。"""
    return unseal(derive_object_key(dek, KEYCHECK_NAME), key_check,
                  aad_identity(username)).hex()


def check_dek(dek: bytes, username: str, key_check: str) -> bool:
    """自检块解得开 ⟹ DEK 正确（GCM 认证标签保证）。"""
    try:
        prove_dek(dek, username, key_check)
    except Exception:  # noqa: BLE001  InvalidTag / ValueError 都算口令错
        return False
    return True


# ---------------- 应用层加密传输：密封盒（对应 加密链路思路.md §3.1） ----------------
#
# 与上面的"对象加密"是两回事：
#   对象加密 = 数据在云端怎么存（长期密钥，DEK 派生）
#   密封盒   = 数据在网线上怎么走（一次性会话密钥 SK，用完就扔）
#
# 每次请求换一把 SK：截获了也没用，因为它只对那一次请求有效。

SEAL_INFO = b"phix/v1/seal"
REQ_AAD_PREFIX = b"phix/v1/req|"
EPK_LEN = 32
NONCE_LEN = 12
SK_LEN = 32


def seal_box(msg: bytes, recipient_pk: bytes) -> bytes:
    """把消息密封给某个 X25519 公钥的持有者（只有对方能开）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import (X25519PrivateKey,
                                                                  X25519PublicKey)

    esk = X25519PrivateKey.generate()
    epk = esk.public_key().public_bytes(serialization.Encoding.Raw,
                                        serialization.PublicFormat.Raw)
    shared = esk.exchange(X25519PublicKey.from_public_bytes(recipient_pk))
    salt = epk + recipient_pk
    key = _hkdf_sha256(shared, salt, SEAL_INFO, 32)
    nonce = os.urandom(NONCE_LEN)
    return epk + nonce + AESGCM(key).encrypt(nonce, msg, salt)


def open_box(sealed: bytes, recipient_sk_raw: bytes, recipient_pk: bytes) -> bytes:
    """打开给自己的密封盒（用 X25519 私钥原始字节）。"""
    from cryptography.hazmat.primitives.asymmetric.x25519 import (X25519PrivateKey,
                                                                  X25519PublicKey)

    if len(sealed) < EPK_LEN + NONCE_LEN + 16:
        raise ValueError("密封盒长度不对")
    epk = sealed[:EPK_LEN]
    nonce = sealed[EPK_LEN:EPK_LEN + NONCE_LEN]
    ct = sealed[EPK_LEN + NONCE_LEN:]
    sk = X25519PrivateKey.from_private_bytes(recipient_sk_raw)
    shared = sk.exchange(X25519PublicKey.from_public_bytes(epk))
    salt = epk + recipient_pk
    key = _hkdf_sha256(shared, salt, SEAL_INFO, 32)
    return AESGCM(key).decrypt(nonce, ct, salt)


def env_aad(method: str, path: str) -> bytes:
    return REQ_AAD_PREFIX + method.encode("utf-8") + b"|" + path.encode("utf-8")


def new_session_key() -> bytes:
    return os.urandom(SK_LEN)


def make_envelope(server_pk: bytes, method: str, path: str, body,
                  query: str = "") -> tuple[dict, bytes]:
    """把一个请求包装成信封。返回 (信封 dict, 本次的会话密钥 SK)。"""
    import time as _time

    sk = new_session_key()
    inner = {
        "m": method, "p": path, "q": query, "b": body,
        "ts": int(_time.time()), "nonce": b64e(os.urandom(16)),
    }
    iv = os.urandom(NONCE_LEN)
    ct = AESGCM(sk).encrypt(iv, json.dumps(inner, ensure_ascii=False).encode("utf-8"),
                            env_aad(method, path))
    return {"sealed_sk": b64e(seal_box(sk, server_pk)), "iv": b64e(iv),
            "ct": b64e(ct)}, sk


def open_envelope_response(sk: bytes, method: str, path: str, envelope: dict) -> bytes:
    """解开服务器返回的信封。"""
    iv = b64d(envelope["iv"])
    ct = b64d(envelope["ct"])
    return AESGCM(sk).decrypt(iv, ct, env_aad(method, path))


# ---------------- 恢复码 ----------------

def new_recovery_code() -> str:
    """24 位 base32（去掉易混字符）≈ 115 bit 熵，格式 XXXX-XXXX-…-XXXX 便于抄写。"""
    chars = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_CHARS))
    return "-".join(chars[i:i + 4] for i in range(0, RECOVERY_CHARS, 4))


def normalize_recovery_code(code: str) -> str:
    """去掉分隔符与空白、统一大写；只保留 **ASCII 字母数字**。

    不用 ``str.isalnum()`` —— 那是 Unicode 语义，会放过全角 ``Ａ``/``３``、``é`` 之类
    base32 字母表之外的字符；Node 侧只认 ``[0-9A-Za-z]``，两端必须一致。
    """
    if not isinstance(code, str):
        raise TypeError("恢复码必须是字符串")
    up = unicodedata.normalize("NFKC", code).upper()
    return "".join(ch for ch in up if ch in _ASCII_ALNUM)


_ASCII_ALNUM = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def wrap_dek_with_recovery(dek: bytes, code: str, salt_hex: str, username: str,
                           algo: str = KDF_ALGO) -> str:
    return seal(derive_kek(normalize_recovery_code(code), salt_hex, algo), dek,
                aad_identity(username))


def unwrap_dek_with_recovery(envelope: str, code: str, salt_hex: str,
                             username: str, algo: str = KDF_ALGO) -> bytes:
    dek = unseal(derive_kek(normalize_recovery_code(code), salt_hex, algo), envelope,
                 aad_identity(username))
    if len(dek) != DEK_BYTES:
        raise ValueError("DEK 长度不对")
    return dek


# ---------------- 一站式：生成 / 重新包裹密钥材料 ----------------

def material_from_dek(dek: bytes, username: str, passphrase: str,
                      key_mode: str = "password",
                      recovery_code: str | None = None,
                      key_check_plain: str | None = None,
                      kdf_algo: str = KDF_ALGO,
                      auth_salt: str | None = None,
                      auth_passphrase: str | None = None) -> dict:
    """用一把已知 DEK 生成整套密钥材料（注册与「换口令重新包裹」都走它）。

    `key_check_plain` 在**重新包裹**时必须把原来那串传进来 —— 它是服务端认定的
    秘密，换了就对不上了。注册时留空则自动生成。

    **两个盐、两条链，不能混**（混过一次，见下面的注意事项）：

    | 名字 | 用途 | 什么时候变 |
    |---|---|---|
    | `kdf_salt` | 派生 **KEK**（包 DEK 的那把） | 换包裹口令时**会变** |
    | `auth_salt` | 派生 **AuthHash**（发给服务器的登录凭证） | **注册时定下，永不改变** |

    如果两者共用一个盐：切「独立同步口令」会换 `kdf_salt`，于是 AuthHash 跟着变，
    而服务器存的还是旧的那个 → **下次登录直接失败**。这是实测踩过的坑。

    `auth_passphrase` 是**登录口令**（默认等于 `passphrase`）：切成同步口令模式时
    登录口令没变，AuthHash 也就不该变——所以要把登录口令单独传进来。
    """
    code = recovery_code or new_recovery_code()
    plain = key_check_plain or new_key_check_plain()
    salt = new_salt()
    rsalt = new_salt()
    asalt = auth_salt or new_salt()
    out = {
        "dek": dek,
        "recovery_code": code,
        "kdf_algo": kdf_algo,
        "kdf_salt": salt,
        "auth_salt": asalt,
        "recovery_salt": rsalt,
        "key_wrap": wrap_dek(dek, passphrase, salt, username, kdf_algo),
        "recovery_wrap": wrap_dek_with_recovery(dek, code, rsalt, username, kdf_algo),
        "key_check": make_key_check(dek, username, plain),
        "key_check_plain": plain,
        "key_mode": key_mode,
    }
    if uses_auth_hash(kdf_algo):
        out["auth_hash"] = auth_hash_hex(auth_passphrase or passphrase, asalt, kdf_algo)
    return out


def new_material(username: str, passphrase: str, key_mode: str = "password",
                 kdf_algo: str = KDF_ALGO) -> dict:
    """注册新账号：随机生成一把 DEK，返回整套材料 + 恢复码 + AuthHash。"""
    return material_from_dek(os.urandom(DEK_BYTES), username, passphrase, key_mode,
                             kdf_algo=kdf_algo)


def rewrap(dek: bytes, username: str, new_passphrase: str,
           key_mode: str = "password", recovery_code: str | None = None,
           key_check_plain: str | None = None,
           kdf_algo: str = KDF_ALGO,
           auth_salt: str | None = None,
           auth_passphrase: str | None = None) -> dict:
    """换口令 / 切模式：**同一把 DEK**，只换包裹。密文一个字节都不动。

    `auth_salt` 必须把**原来那个**传进来（它永不改变）；`auth_passphrase`
    是当前的登录口令。这样切同步口令时 AuthHash 保持不变，登录照常。
    """
    return material_from_dek(dek, username, new_passphrase, key_mode,
                             recovery_code, key_check_plain, kdf_algo,
                             auth_salt, auth_passphrase)


# ---------------- 便于测试：纯函数式自检 ----------------

def selfcheck() -> dict:
    """跑一遍本地往返，确认这套参数在本机能正常工作。"""
    username = "selfcheck"
    mat = new_material(username, "correct horse battery staple")
    dek = mat["dek"]
    ok_wrap = unwrap_dek(mat["key_wrap"], "correct horse battery staple",
                         mat["kdf_salt"], username) == dek
    ok_check = check_dek(dek, username, mat["key_check"])
    bad_check = check_dek(os.urandom(32), username, mat["key_check"])
    ok_proof = prove_dek(dek, username, mat["key_check"]) == mat["key_check_plain"]
    ok_rec = unwrap_dek_with_recovery(mat["recovery_wrap"], mat["recovery_code"],
                                      mat["recovery_salt"], username) == dek
    env = seal_object(dek, 7, "schedule", b'{"hello":"\xe4\xb8\xad\xe6\x96\x87"}')
    back = unseal_object(dek, 7, "schedule", env)
    wrong_name = False
    try:
        unseal_object(dek, 7, "timetable", env)
    except Exception:  # noqa: BLE001
        wrong_name = True
    rw = rewrap(dek, username, "new phrase", "password", mat["recovery_code"],
                mat["key_check_plain"])
    ok_rewrap = (unwrap_dek(rw["key_wrap"], "new phrase", rw["kdf_salt"], username) == dek
                 and rw["key_check_plain"] == mat["key_check_plain"])
    return {
        "key_wrap_roundtrip": ok_wrap,
        "key_check_ok": ok_check,
        "key_check_rejects_wrong_dek": not bad_check,
        "dek_proof_matches": ok_proof,
        "recovery_roundtrip": ok_rec,
        "object_roundtrip": back.decode("utf-8").startswith("{"),
        "aad_binds_name": wrong_name,
        "rewrap_keeps_dek_and_check": ok_rewrap,
    }
