"""phix 云同步客户端（PLL 侧）。

设计原则（来自 ``云同步开发-交接文档.md`` §7.2）:

- **本地 ``data/`` 永远是唯一真相源**。同步是"加在旁边"的一层,绝不改成"以云为准"。
- 服务端只存**不透明密文**,看不见对象里是什么;所以**合并在客户端做**。
- 拿不准就**保留两份并报告**,绝不静默丢数据。

同步单位是一个个**具名对象**(见 ``phix-协议规范.md`` §5),映射到本地文件/字段::

    settings.accounts   settings.yaml 的 accounts 段(四平台凭据)
    settings.lessons    settings.yaml 的 lessons(选课)
    settings.ui         settings.yaml 的 ui 段(排序偏好)
    settings.ai         settings.yaml 的 ai 段(供应商与 key)
    schedule            data/Schedule(日程)
    timetable           data/Timetable(课表)
    school              data/School(学校快照)
    agent:<会话id>      data/agent/<id>.json(AI 会话)

**怎么做到"删除也能同步"**:光有 本地 + 远端 两份是分不清"对方删了"还是"我新加的"的。
所以本地额外存一份**上次同步后的明文快照**(``data/.sync/last/``),用标准的
三方合并(基版 / 本地 / 远端)判断,删除才会正确地传播。

**绝不碰的东西**:``phl/``、``phll/``、``logs/``、``_backups/``、``_migrated_backup/``、
运行标记 ``.phl-running`` / ``.pll-running``、以及同步状态目录 ``.sync/`` 本身。
学校网站的 Cookie 与浏览器 profile 就在前两个里面 —— 那等于登录态,不上云。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import threading
from pathlib import Path

import requests

from . import aiconfig
from . import filestore as fs
from . import paths
from . import phixcrypto as pc
from . import shareddata
from . import sharedschool

# ---------------------------------------------------------------- 常量
SYNC_DIR = ".sync"
STATE_NAME = "state.json"
SNAPSHOT_SUBDIR = "last"
DEFAULT_TIMEOUT = 30
MAX_PAYLOAD = 8 * 1024 * 1024
API_PREFIX = "/api/v1"

#: 这些前缀/名字**永远不上云**（硬编码，不依赖配置）
NEVER_SYNC = (
    "phl", "phll", "logs", "_backups", "_migrated_backup",
    ".phl-running", ".pll-running", SYNC_DIR, ".gh_token",
)

#: 默认同步哪些对象
DEFAULT_OBJECTS = (
    "settings.accounts", "settings.lessons", "settings.ui",
    "schedule", "timetable", "school",
    "profile", "mood",
)

#: **绝不触发自动续期**的端点（P3）。
#:
#: 续期本身、以及登录/注册/取密钥材料这些"免认证"端点，都不该在 401 时去续期：
#: 它们要么拿不到 Bearer，要么就是续期接口自己 —— 在续期接口上再续一次等于死循环。
#: 特别注意 ``/auth/logout`` **不在**这个名单里：访问令牌刚好过期时，用户点"退出登录"
#: 仍然应该能续一次期、把服务端会话真正注销掉（否则会话会一直挂在设备列表里）。
NO_REFRESH_PATHS = (
    "/ping", "/auth/refresh", "/auth/login", "/auth/register",
    "/auth/keymaterial", "/auth/recover",
)

_MISSING = object()


class PhixError(Exception):
    """服务端/网络错误。``code`` 与协议 §4.7 的枚举一致。"""

    def __init__(self, code: str, message: str, status: int = 0, payload=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.payload = payload or {}

    @property
    def is_conflict(self) -> bool:
        return self.code == "revision_conflict"

    def __str__(self) -> str:
        return self.message


def _refresh_failed(exc: PhixError) -> PhixError:
    """续期失败 → 翻成一句人话，并且**明确"不许重试"**。

    为什么不能重试：refresh 是"用一次换一次"的。旧那串一旦超出宽限期再用，
    服务端会判定为**令牌被偷**，把整个会话作废。客户端连点 = 自己把会话点没了。
    所以这里只翻错误，绝不在外面套重试。
    """
    if exc.status != 401:
        return exc                      # 网络/服务端故障：原样往上抛
    if "已被使用过" in (exc.message or ""):
        return PhixError(
            "session_revoked",
            "这次登录已被服务端作废（续期凭据被判定为重复使用），需要重新登录",
            exc.status, exc.payload)
    return PhixError(
        "relogin_required",
        f"续期被拒绝：{exc.message}（需要重新登录）",
        exc.status, exc.payload)


# ---------------------------------------------------------------- HTTP 客户端
class PhixClient:
    """对 phix 服务端 REST API 的薄封装（只做 HTTP，不含任何业务）。

    **应用层加密传输**（对应 `加密链路思路.md` §3）：服务器在 `/ping` 里报出
    `enc=1` 与 X25519 公钥 `pk`，之后每个请求的体都用一次性的会话密钥加密
    （`seal_box`），响应也加密回来。**网线上只有密文，不接 TLS 也不怕被嗅探。**

    - 公钥**首次信任后固定存本地**（`<pin_dir>/<host>.txt`）；下次对不上就拒绝连接
      —— 防止有人冒充服务器。确认是服务器换了钥匙之后，
      删掉那个文件（或调 `trust_new_server_key()`）再连。
    - 服务器不支持（`enc=0`）或探测失败 → 自动退回明文路径，**绝不因此连不上**。

    **P3 双令牌（JWT + refresh）**：业务请求用 ``access_token``（Ed25519 签名的
    JWT，15 分钟）作 Bearer；``refresh_token``（30 天、用一次换一次）只喂给
    `/auth/refresh`。业务请求收到 ``401 + code=token_expired`` 时自动续期一次、
    用新令牌**重试原请求一次**（只一次，绝不循环）；续期失败直接抛给上层
    （**不重试** —— 拿旧 refresh 连点会被服务端判成重放、整个会话作废）。
    令牌一有变化就调 ``on_tokens(client)``，由会话层负责落盘（见 phixsession）。
    """

    def __init__(self, server: str, token: str | None = None,
                 timeout: int = DEFAULT_TIMEOUT, e2e: bool = True, pin_dir=None,
                 refresh_token: str | None = None, on_tokens=None):
        self.server = (server or "").rstrip("/")
        self.token = token
        self.timeout = timeout
        self.e2e_enabled = bool(e2e)
        self.server_pk: bytes | None = None
        self.pin_dir = Path(pin_dir) if pin_dir else None
        #: 续期令牌（P3）。**只发给 `/auth/refresh`**，别当 Bearer 用。
        self.refresh_token = refresh_token or None
        #: 老式长期令牌（兼容期），仅留在内存里给老接口用，不参与业务请求。
        self.legacy_token: str | None = None
        #: 令牌变化回调：``on_tokens(client)``。**只传令牌，绝不传 DEK**。
        self.on_tokens = on_tokens
        self._token_lock = threading.RLock()
        self._renew_lock = threading.RLock()

    # -- 应用层加密 --
    @property
    def encrypted(self) -> bool:
        """当前是否真的在用信封。"""
        return self.server_pk is not None

    def _pin_path(self) -> Path | None:
        if self.pin_dir is None:
            return None
        host = re.sub(r"[^A-Za-z0-9._-]", "_", self.server.split("://", 1)[-1])[:120]
        return self.pin_dir / f"{host}.txt"

    def trust_new_server_key(self) -> None:
        """清掉固定的服务器公钥（确认服务器确实换了钥匙之后才该调用）。"""
        p = self._pin_path()
        if p and p.exists():
            try:
                p.unlink()
            except OSError:
                pass
        self.server_pk = None

    def ensure_e2e(self) -> bool:
        """探测并启用应用层加密。返回是否启用。**不会因为失败就抛错。**"""
        if not self.e2e_enabled:
            return False
        if self.server_pk is not None:
            return True
        try:
            info = self._plain("GET", "/ping", None, token="")
        except Exception:  # noqa: BLE001  探测失败就退回明文，别挡着用户
            self.e2e_enabled = False
            return False
        try:
            if int(info.get("enc") or 0) != 1 or not info.get("pk"):
                self.e2e_enabled = False
                return False
            pk = pc.b64d(info["pk"])
        except Exception:  # noqa: BLE001
            self.e2e_enabled = False
            return False
        if len(pk) != 32:
            self.e2e_enabled = False
            return False

        p = self._pin_path()
        if p is not None:
            old = None
            try:
                if p.exists():
                    old = bytes.fromhex(p.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                old = None
            if old is not None and old != pk:
                raise PhixError(
                    "server_key_changed",
                    "服务器的加密公钥和本机记住的不一样！可能是服务器重装过，"
                    "也可能是有人在中间冒充。确认无误后再重新信任（设置页有入口）。")
            if old is None:
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(pk.hex() + "\n", encoding="utf-8")
                except OSError:
                    pass          # 存不下就只是不能固定，不该挡住使用
        self.server_pk = pk
        return True

    # -- 内部 --
    def _url(self, path: str) -> str:
        return f"{self.server}/api/v1{path}"

    def _req(self, method: str, path: str, payload=None, token=None, timeout=None):
        """发一个业务请求；遇到"令牌过期"自动续期一次并用新令牌**重试一次**。

        只重试一次：重试后的请求再失败（包括又回 `token_expired`）就直接抛给上层，
        **绝不在这里循环**。续期本身失败也**不重试**（见 `refresh_access`）。
        """
        used = token if token is not None else self.token
        try:
            return self._req_once(method, path, payload, token, timeout)
        except PhixError as exc:
            if not self._should_refresh(exc, path):
                raise
            self._renew_for(used, timeout)          # 失败直接抛，不重试
            return self._req_once(method, path, payload, None, timeout)

    def _req_once(self, method: str, path: str, payload=None, token=None, timeout=None):
        """单次请求（不含任何续期逻辑）。"""
        if path.split("?", 1)[0] != "/ping" and self.e2e_enabled:
            if self.server_pk is None:
                try:
                    self.ensure_e2e()
                except PhixError:
                    raise
            if self.server_pk is not None:
                return self._enc_req(method, path, payload, token, timeout)
        return self._plain(method, path, payload, token, timeout)

    def _plain(self, method: str, path: str, payload=None, token=None, timeout=None):
        headers = {"Accept": "application/json"}
        tok = token if token is not None else self.token
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        try:
            resp = requests.request(
                method, self._url(path), json=payload, headers=headers,
                timeout=timeout or self.timeout,
            )
        except requests.Timeout as exc:
            raise PhixError("network", f"连接超时：{self.server}") from exc
        except requests.RequestException as exc:
            raise PhixError("network", f"连不上 phix 服务：{exc}") from exc
        return self._decode(resp, resp.json() if _has_json(resp) else {})

    def _enc_req(self, method: str, path: str, payload=None, token=None, timeout=None):
        path_only, _, query = path.partition("?")
        # AAD 里用**完整路径**（含 /api/v1 前缀）—— 服务端那侧看到的就是它
        full_path = API_PREFIX + path_only
        headers = {"Accept": "application/json", "X-Phix-Enc": "1"}
        tok = token if token is not None else self.token
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        envelope, sk = pc.make_envelope(self.server_pk, method, full_path, payload, query)
        try:
            resp = requests.request(method, self._url(path_only), json=envelope,
                                    headers=headers, timeout=timeout or self.timeout)
        except requests.Timeout as exc:
            raise PhixError("network", f"连接超时：{self.server}") from exc
        except requests.RequestException as exc:
            raise PhixError("network", f"连不上 phix 服务：{exc}") from exc

        data = resp.json() if _has_json(resp) else {}
        # 信封解不开时服务器是**明文**回错误的（它那时还没拿到会话密钥），
        # 所以这里要看内容判断，不能只看状态码。
        if isinstance(data, dict) and "iv" in data and "ct" in data:
            try:
                raw = pc.open_envelope_response(sk, method, full_path, data)
                data = json.loads(raw.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                raise PhixError("bad_envelope",
                                f"服务器回复的信封解不开：{exc}", resp.status_code) from exc
        return self._decode(resp, data)

    @staticmethod
    def _decode(resp, data):
        if not isinstance(data, dict):
            data = {}
        if resp.status_code >= 400 or not data.get("ok", resp.status_code < 400):
            e = data.get("error") or {}
            raise PhixError(e.get("code") or "server_error",
                            e.get("message") or f"服务端返回 {resp.status_code}",
                            resp.status_code, data)
        return data

    # -- 令牌（P3：access + refresh） --
    def absorb_tokens(self, info: dict) -> None:
        """把登录/注册响应里的令牌收进实例。

        - ``access_token``（JWT）→ ``self.token``：**业务请求用它当 Bearer**。
        - 老式 ``token`` → ``self.legacy_token``，并在**没有** access 时兜底当 Bearer
          （服务器关掉兼容令牌 `PHIX_LEGACY_TOKENS=0` 之后就不会再有这个字段了）。
        - ``refresh_token`` → ``self.refresh_token``：只喂 `/auth/refresh`。

        幂等；任一个字段缺省就保持原值（**不会**把已有令牌清成空）。
        """
        if not isinstance(info, dict):
            return
        access = info.get("access_token") or ""
        legacy = info.get("token") or ""
        refresh = info.get("refresh_token") or ""
        with self._token_lock:
            if access:
                self.token = access
            elif legacy:
                self.token = legacy
            if legacy:
                self.legacy_token = legacy
            if refresh:
                self.refresh_token = refresh
        self._notify_tokens()

    def _notify_tokens(self) -> None:
        """令牌变了 → 通知会话层落盘。**回调失败绝不能影响正在进行的请求。**"""
        cb = self.on_tokens
        if cb is None:
            return
        try:
            cb(self)
        except Exception as exc:  # noqa: BLE001
            from .logutil import warn as _log_warn

            _log_warn(f"phix 令牌保存回调失败（下次可能要重新登录）：{exc}")

    def _apply_refresh_response(self, data: dict) -> dict:
        """把 `/auth/refresh` 的响应落到本地。

        - ``rotated: true``（响应里有 ``refresh_token``）→ 存下**新的 access 与 refresh**。
          ⚠️ 不存新 refresh 的话，15 分钟后旧的超出宽限期再用就成了"重放"。
        - ``rotated: false``（宽限期内重复续期，响应里**没有** ``refresh_token``）
          → 只更新 access，**本地那串 refresh 一个字节都不动**，也不算错误：
          并发续期就是这样，两个请求里只有拿到 `refresh_token` 的那个说了算。
        """
        data = data if isinstance(data, dict) else {}
        access = data.get("access_token") or ""
        new_refresh = data.get("refresh_token") or ""
        with self._token_lock:
            if access:
                self.token = access
            if new_refresh:
                self.refresh_token = new_refresh
        self._notify_tokens()
        return data

    def refresh_access(self, timeout=None) -> dict:
        """用 refresh 令牌换新 access（并轮换 refresh）。**免认证，失败绝不重试。**

        返回服务端响应（``rotated`` / ``expires_in`` 等）。失败时抛
        :class:`PhixError`，``code`` 为 ``session_revoked``（被判重放、会话已作废）
        或 ``relogin_required``（凭据无效）—— 两种都必须**重新登录**。
        """
        with self._token_lock:
            rt = (self.refresh_token or "").strip()
        if not rt:
            raise PhixError("no_refresh_token",
                            "本机没有保存续期凭据，请重新登录")
        try:
            data = self._req_once("POST", "/auth/refresh",
                                  {"refresh_token": rt}, token="", timeout=timeout)
        except PhixError as exc:
            raise _refresh_failed(exc) from exc
        return self._apply_refresh_response(data)

    def _should_refresh(self, exc: PhixError, path: str) -> bool:
        """该不该拿这次 401 去续期？

        **只有** ``code=token_expired`` 才算"该续期"；`unauthorized`（会话被注销 /
        令牌无效 / 签名不对）续期也没用，必须重新登录。另外要求这次请求**真的带了
        Bearer**（没带凭据的请求不可能"令牌过期"，别去续）。
        """
        if exc.status != 401 or exc.code != "token_expired":
            return False
        if path.split("?", 1)[0] in NO_REFRESH_PATHS:
            return False
        if not self.token:
            return False
        return bool((self.refresh_token or "").strip())

    def _renew_for(self, stale_token, timeout=None) -> None:
        """并发续期保护：**同一个过期令牌只需要续一次**。

        两个线程同时撞上过期时，如果都拿同一串 refresh 去续，第二次就成了
        "宽限期外重放"（grace=0 的服务器上直接撤销整个会话）。所以这里串行化：
        发现令牌已经被别的线程换掉了就直接用新的，不再打第二次。
        """
        with self._renew_lock:
            if self.token and stale_token and self.token != stale_token:
                return                      # 已经有人续过了
            self.refresh_access(timeout=timeout)

    # -- 认证 --
    def ping(self):
        return self._req("GET", "/ping", token="")

    # **身份凭证**：v2 账号发 AuthHash（服务器由此反推不出口令、更算不出 KEK），
    # v1 老账号只能发口令原文。服务器两边都收，客户端按账号的 kdf_algo 决定发哪种。
    #
    # 注意用的是 **auth_salt**（注册时定下、永不改变），不是 kdf_salt
    # （那个会随换包裹口令而变）—— 用错会导致换了同步口令之后登录失败。
    @staticmethod
    def _credential(password=None, auth_salt=None, algo=None, material=None,
                    prefix: str = "") -> dict:
        if material and material.get("auth_hash"):
            return {f"{prefix}auth_hash": material["auth_hash"]}
        if password is not None and auth_salt and algo and pc.uses_auth_hash(algo):
            return {f"{prefix}auth_hash": pc.auth_hash_hex(password, auth_salt, algo)}
        if password is not None:
            return {f"{prefix}password": password}
        return {}

    def keymaterial(self, username):
        """取某个账号的**公开**密钥材料（免登录）。

        客户端靠它知道这个账号的 `kdf_algo` 与 `auth_salt` ——
        决定登录时发 AuthHash 还是口令原文、以及用哪个盐算。
        """
        return self._req("POST", "/auth/keymaterial", {"username": username}, token="")

    def register(self, username, password, device="", material=None, agree=True):
        mat = material or pc.new_material(username, password)
        body = {
            "username": username, "agree": bool(agree), "device": device,
            "kdf_algo": mat["kdf_algo"], "kdf_salt": mat["kdf_salt"],
            "auth_salt": mat.get("auth_salt", ""),
            "key_wrap": mat["key_wrap"], "key_mode": mat["key_mode"],
            "key_check": mat["key_check"],
            "key_check_plain": mat["key_check_plain"],
            "recovery_salt": mat["recovery_salt"],
            "recovery_wrap": mat["recovery_wrap"],
        }
        body.update(self._credential(password, material=mat))
        data = self._req("POST", "/auth/register", body, token="")
        self.absorb_tokens(data)      # P3：access/refresh 两组令牌一起收下
        return data, mat

    def login(self, username, password, device="", server_meta=None):
        """登录。

        先问一次 `/auth/keymaterial` 拿该账号的 `kdf_algo` 与 `auth_salt`：
        v2 账号发 `auth_hash`（**服务器永远见不到口令**），v1 老账号发口令原文。

        成功后 `access_token` 进 `self.token`（Bearer），`refresh_token` 进
        `self.refresh_token`（只喂 `/auth/refresh`）—— 落盘由会话层负责。
        """
        meta = server_meta if server_meta is not None else self.keymaterial(username)
        algo = meta.get("kdf_algo") or pc.KDF_ALGO_V1
        auth_salt = meta.get("auth_salt") or meta.get("kdf_salt")
        body = {"username": username, "device": device}
        body.update(self._credential(password, auth_salt=auth_salt, algo=algo))
        data = self._req("POST", "/auth/login", body, token="")
        self.absorb_tokens(data)
        return data

    def me(self, token=None):
        return self._req("GET", "/auth/me", token=token)

    def logout(self, token=None):
        """注销当前会话（服务端立刻吊销访问令牌 + 老式令牌）。

        成功后本地令牌一并清掉：这次登录已经结束了，留着只会让人以为还登着。
        （清的是**内存里的**；`settings.yaml` 里那两串由会话层删。）
        """
        data = self._req("POST", "/auth/logout", {}, token=token)
        with self._token_lock:
            self.token = None
            self.refresh_token = None
            self.legacy_token = None
        return data

    def change_password(self, old_password, new_password, dek_proof, material,
                        old_auth_salt=None, old_algo=None, token=None):
        """换登录密码。

        - ``material`` 里**没给的字段一律不发** —— syncphrase 模式改登录密码时
          不应带上 key_wrap，否则会把 DEK 的包裹方式换掉。
        - ``old_auth_salt`` / ``old_algo`` 是**当前**账号的凭证盐与代次，用来算旧凭证。
        """
        body = {"dek_proof": dek_proof}
        body.update(self._credential(old_password, auth_salt=old_auth_salt,
                                     algo=old_algo, prefix="old_"))
        body.update(self._credential(new_password, material=material, prefix="new_"))
        for key in ("kdf_algo", "kdf_salt", "auth_salt", "key_wrap", "key_check",
                    "key_mode"):
            if material.get(key):
                body[key] = material[key]
        return self._req("POST", "/auth/password", body, token=token)

    def rewrap(self, password, dek_proof, material, auth_salt=None, algo=None,
               token=None):
        body = {
            "dek_proof": dek_proof,
            "kdf_algo": material["kdf_algo"], "kdf_salt": material["kdf_salt"],
            "auth_salt": material.get("auth_salt", ""),
            "key_wrap": material["key_wrap"], "key_check": material["key_check"],
            "key_mode": material["key_mode"],
        }
        body.update(self._credential(password, auth_salt=auth_salt, algo=algo))
        return self._req("POST", "/auth/rewrap", body, token=token)

    def recover(self, username, new_password, dek_proof, material):
        body = {
            "username": username,
            "kdf_algo": material["kdf_algo"], "kdf_salt": material["kdf_salt"],
            "auth_salt": material.get("auth_salt", ""),
            "key_wrap": material["key_wrap"], "key_check": material["key_check"],
            "key_mode": material["key_mode"], "dek_proof": dek_proof,
        }
        body.update(self._credential(new_password, material=material, prefix="new_"))
        return self._req("POST", "/auth/recover", body, token="")

    def key_material(self, username):
        """取某个账号的**公开**密钥材料（忘记密码时用；不需要登录）。

        服务端对不存在的账号会返回形状一致的假材料，所以这里拿不到"账号是否存在"。
        """
        return self._req("POST", "/auth/keymaterial", {"username": username}, token="")

    def devices(self, token=None):
        """本账号的登录设备与会话（P3 `/auth/devices`）。

        返回 ``{sessions, devices, access_ttl, refresh_ttl}``：
        ``sessions`` 是正式形态（一次登录 = 一个会话，带 ``current`` 标记），
        ``devices`` 是兼容期的老式长期令牌（历史设备）。**不含 refresh 明文**。
        """
        return self._req("GET", "/auth/devices", token=token)

    def revoke_device(self, session_id=None, all_except_current: bool = False,
                      token_id=None, all_tokens: bool = False, token=None):
        """注销会话 / 老式令牌（P3 `/auth/devices/revoke`）。

        - ``session_id``：注销某一个会话（那台设备的访问令牌**立刻**失效）
        - ``all_except_current=True``：注销本机以外的全部会话 —— 手机上就能"退出其它设备"

        参数可组合，但**一个都不给会被服务端拒掉**，所以这里先本地拦一道，
        免得白跑一次网络请求。
        """
        body: dict = {}
        if session_id:
            body["session_id"] = int(session_id)
        if all_except_current:
            body["all_except_current"] = True
        if token_id:
            body["token_id"] = int(token_id)
        if all_tokens:
            body["all_tokens"] = True
        if not body:
            raise PhixError("bad_request", "要指定注销哪一台设备")
        return self._req("POST", "/auth/devices/revoke", body, token=token)

    # -- 同步 --
    def manifest(self, token=None):
        return self._req("GET", "/sync/manifest", token=token)

    def get_object(self, name, token=None):
        return self._req("GET", f"/sync/objects/{name}", token=token)

    def put_object(self, name, base_revision, payload, device="", token=None):
        return self._req("PUT", f"/sync/objects/{name}",
                         {"base_revision": int(base_revision), "payload": payload,
                          "device": device}, token=token)

    def delete_object(self, name, base_revision, token=None):
        return self._req("PUT", f"/sync/objects/{name}",
                         {"base_revision": int(base_revision), "deleted": True},
                         token=token)

    def batch(self, items, token=None):
        return self._req("POST", "/sync/objects/batch", {"objects": items}, token=token)


# ---------------------------------------------------------------- 三方合并原语
def _plain(v):
    """把内部哨兵 ``_MISSING`` 归一成 ``None``。

    **必须做**：``_MISSING`` 是个 ``object()``，一旦被原样塞进 ``conflicts``，
    紧接着 ``save_state`` 的 ``json.dumps`` 会抛 ``TypeError: Object of type object
    is not JSON serializable``，**整轮同步直接失败**（PHL 移植时实测复现过）。
    """
    return None if v is _MISSING else v


def _is_empty(v):
    """None / 空串 / 空字典 / 空数组 / 缺省 → 视为"没值"。"""
    return v is _MISSING or v is None or v == "" or v == {} or v == []


# 这些键只是记账用的元信息，判断"这个对象有没有真内容"时要忽略它们。
# 不忽略的话 `{"updated_at": "...", "events": []}` 会被当成"有内容"，
# 于是空的一边就能去覆盖满的一边（正是要防的事故）。
_META_KEYS = frozenset({
    "updated_at", "modified_at", "fetched_at", "created", "created_at",
    "app", "version", "revision", "kind", "updated_by", "device",
})


def _is_empty_doc(doc) -> bool:
    """整份同步对象算不算"空"（忽略元信息键，递归看内容）。"""
    if doc is None or doc == {} or doc == []:
        return True
    if isinstance(doc, dict):
        return all(_is_empty_doc(v) for k, v in doc.items() if k not in _META_KEYS)
    if isinstance(doc, (list, tuple)):
        return not doc
    return False


def _brief_doc(doc) -> str:
    """把一份同步对象压成一句人话，用于冲突记录（别把整份数据塞进去）。"""
    if doc is None:
        return "（没有）"
    if isinstance(doc, (list, tuple)):
        return f"{len(doc)} 条"
    if isinstance(doc, dict):
        for key in ("events", "items", "tasks", "providers", "classes", "sessions"):
            if isinstance(doc.get(key), list):
                return f"{len(doc[key])} 条"
        return f"{len(doc)} 个字段"
    return str(doc)[:40]


def _apply_preference(name, prefer, local, remote, merged, conflicts):
    """按用户在登录时选的方向改写合并结果。

    `prefer=="local"` → 整份用本地；`prefer=="remote"` → 整份用云端。
    两边只要有一边是空的就**原样退回合并结果**：空的一边没有"覆盖"的资格，
    否则会把另一边的数据抹掉（历史上课表被清成 0 就是这么来的）。
    """
    if prefer not in ("local", "remote"):
        return merged, conflicts
    winner = local if prefer == "local" else remote
    loser = remote if prefer == "local" else local
    if _is_empty_doc(winner) or _is_empty_doc(loser):
        return merged, conflicts
    note = ("按你在登录时选的：用本地数据覆盖 phix 账号"
            if prefer == "local" else
            "按你在登录时选的：用 phix 账号的数据覆盖本地")
    return winner, [{"path": name, "local": _brief_doc(local),
                     "remote": _brief_doc(remote), "note": note}]


def _merge_scalar(base, local, remote, path, conflicts):
    """标量/整体替换型值的三方合并。"""
    if local == remote:
        return local
    if base is _MISSING or base is None:
        # 没有基版 = 首次同步，或两边各自新增了这个键。
        # 这时让**空的那边让步**：否则一台新设备上还没配过的空值会把云端的配置抹掉
        # （实测：新设备首拉时 `ai.active_model` 被本机的 "" 覆盖，且没有任何提示）。
        if _is_empty(local) and not _is_empty(remote):
            return remote
        if _is_empty(remote) and not _is_empty(local):
            return local
        conflicts.append({"path": path, "local": _plain(local), "remote": _plain(remote),
                          "base": None,
                          "note": "没有基版（首次同步或新增键），两边都有值且不同 → 保留本地"})
        return local
    if local == base:
        return remote          # 只有远端改了
    if remote == base:
        return local           # 只有本地改了
    conflicts.append({"path": path, "local": _plain(local), "remote": _plain(remote),
                      "base": _plain(base), "note": "两边都改了且不同 → 保留本地"})
    return local


def _merge_dict(base, local, remote, path, conflicts):
    """嵌套字典逐叶子三方合并。"""
    base = base if isinstance(base, dict) else {}
    local = local if isinstance(local, dict) else {}
    remote = remote if isinstance(remote, dict) else {}
    out = {}
    for key in sorted(set(base) | set(local) | set(remote)):
        b = base.get(key, _MISSING)
        l = local.get(key, _MISSING)  # noqa: E741
        r = remote.get(key, _MISSING)
        sub = f"{path}.{key}" if path else str(key)
        if l is _MISSING and r is _MISSING:
            continue                                  # 两边都没有 = 删掉
        if l is _MISSING:
            out[key] = _plain(r)
            if b is not _MISSING and r is _MISSING:
                conflicts.append({"path": sub, "local": None, "remote": None,
                                  "base": _plain(b), "note": "本地删除、远端也删除"})
            continue
        if r is _MISSING:
            if b is _MISSING:
                out[key] = _plain(l)                  # 只是本地新增
            elif l == b:
                continue                              # 远端删了且本地没动 → 跟着删
            else:
                out[key] = _plain(l)
                conflicts.append({"path": sub, "local": _plain(l), "remote": None,
                                  "base": _plain(b),
                                  "note": "远端删除、本地又改过 → 保留本地"})
            continue
        if isinstance(l, dict) or isinstance(r, dict):
            merged = _merge_dict(b if isinstance(b, dict) else {},
                                 l if isinstance(l, dict) else {},
                                 r if isinstance(r, dict) else {}, sub, conflicts)
            if merged or l or r:
                out[key] = merged
            continue
        out[key] = _merge_scalar(b, _plain(l), _plain(r), sub, conflicts)
    return out


def _merge_list_of_dicts(base, local, remote, key_of, path, conflicts,
                         union_only: bool = False):
    """按 key 取并集的三方合并（用于选课、日程、课卡这类"带主键的集合"）。

    ``union_only=True`` 时**只取并集、永不删除、不报冲突、冲突时留本地**——
    用于课表这类"抓取缓存"：某台设备没有某天的数据不代表那天没课，
    把"本地缺"当成"用户删了"会导致两台设备互相删除/互相恢复地打架。
    """
    def index(rows):
        out = {}
        for row in rows or []:
            if isinstance(row, dict):
                k = key_of(row)
                if k not in (None, ""):
                    out[str(k)] = row
        return out

    bi, li, ri = index(base), index(local), index(remote)
    out = []
    for key in list(li) + [k for k in ri if k not in li]:
        b, l, r = bi.get(key, _MISSING), li.get(key, _MISSING), ri.get(key, _MISSING)  # noqa: E741
        if l is _MISSING and r is _MISSING:
            continue
        if union_only:
            if l is _MISSING:
                out.append(r)                   # 远端有、本地没有 → 收下
            else:
                out.append(l)                   # 两边都有 → 留本地（缓存不值得比新旧）
            continue
        if l is _MISSING:
            out.append(r)                       # 远端新增（且本地没有）
            continue
        if r is _MISSING:
            if b is _MISSING:
                out.append(l)                   # 本地新增
            elif json.dumps(l, sort_keys=True) == json.dumps(b, sort_keys=True):
                continue                        # 本地没动、远端删了 → 跟着删
            else:
                out.append(l)
                conflicts.append({"path": f"{path}[{key}]", "local": l, "remote": None,
                                  "base": b, "note": "远端删除、本地又改过 → 保留本地"})
            continue
        if json.dumps(l, sort_keys=True) == json.dumps(r, sort_keys=True):
            out.append(l)
            continue
        if b is not _MISSING and json.dumps(r, sort_keys=True) == json.dumps(b, sort_keys=True):
            out.append(l)                       # 只有本地改了
            continue
        if b is not _MISSING and json.dumps(l, sort_keys=True) == json.dumps(b, sort_keys=True):
            out.append(r)                       # 只有远端改了
            continue
        # 两边都改了 → 用带时间戳的字段判断新旧, 判不了就保留本地并报告
        pick = _pick_newer(l, r)
        out.append(pick)
        if pick is l:
            conflicts.append({"path": f"{path}[{key}]", "local": l, "remote": r,
                              "base": b if b is not _MISSING else None,
                              "note": "同一条两边都改了 → 取较新的（本地），远端那份见冲突记录"})
    return out


def _ts_of(row: dict):
    for field in ("updated_at", "modified_at", "created", "fetched_at"):
        v = row.get(field)
        if isinstance(v, str) and v:
            try:
                from datetime import datetime

                return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
            except Exception:  # noqa: BLE001
                continue
    return None


def _pick_newer(a: dict, b: dict) -> dict:
    ta, tb = _ts_of(a), _ts_of(b)
    if ta is not None and tb is not None and ta != tb:
        return a if ta > tb else b
    return a


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == \
           json.dumps(b, sort_keys=True, ensure_ascii=False)


def _merge_events(base, local, remote, next_id: int, conflicts):
    """日程事件的三方合并，**专治跨设备 id 撞车**。

    两个设备各自"现存最大 id + 1"时很容易同时分配到同一个 id（第一次同步后
    两边 id 集合相同，紧接着各加一条就必然撞车）。这时候不能二选一 ——
    交接文档 §7.2.5 明确要求**保留两份**：本地那条留住原 id，远端那条**改号**后照样留下。

    返回 ``(events, next_free_id)``。
    """
    def index(rows):
        out = {}
        for row in rows or []:
            if isinstance(row, dict) and isinstance(row.get("id"), int):
                out[row["id"]] = row
        return out

    bi, li, ri = index(base), index(local), index(remote)
    out: dict[int, dict] = {}
    dupes: list[dict] = []

    for key in list(li) + [k for k in ri if k not in li]:
        b = bi.get(key, _MISSING)
        l = li.get(key, _MISSING)     # noqa: E741
        r = ri.get(key, _MISSING)
        if l is _MISSING and r is _MISSING:
            continue
        if l is _MISSING:
            if b is _MISSING:
                out[key] = r                              # 远端新增
            elif _same(r, b):
                continue                                  # 本地删了、远端没动 → 跟着删
            else:
                out[key] = r                              # 本地删了但远端改过 → 保留远端
                conflicts.append({"path": f"events[{key}]", "local": None, "remote": r,
                                  "base": b, "note": "本地删除、远端又改过 → 保留远端那份"})
            continue
        if r is _MISSING:
            if b is _MISSING:
                out[key] = l                              # 本地新增
            elif _same(l, b):
                continue                                  # 本地没动、远端删了 → 跟着删
            else:
                out[key] = l                              # 远端删了但本地改过 → 保留本地
                conflicts.append({"path": f"events[{key}]", "local": l, "remote": None,
                                  "base": b, "note": "远端删除、本地又改过 → 保留本地"})
            continue
        if _same(l, r):
            out[key] = l
            continue
        if b is not _MISSING and _same(r, b):
            out[key] = l                                  # 只有本地改了
            continue
        if b is not _MISSING and _same(l, b):
            out[key] = r                                  # 只有远端改了
            continue
        if b is not _MISSING:
            # 同一条事件两边都改了 → 取较新的，并报告
            pick = _pick_newer(l, r)
            out[key] = pick
            conflicts.append({"path": f"events[{key}]", "local": l, "remote": r,
                              "base": b,
                              "note": "同一条两边都改了 → 取较新的那份，另一份见本条记录"})
            continue
        # 两边各自新增、却撞了同一个 id → 保留本地，远端改号
        out[key] = l
        dupes.append(r)

    for row in dupes:
        while next_id in out:
            next_id += 1
        moved = dict(row)
        moved["id"] = next_id
        out[next_id] = moved
        conflicts.append({
            "path": f"events[{row.get('id')}]",
            "local": row.get("title"), "remote": row.get("title"),
            "note": f"两台设备各自新增了一条日程、id 撞车 → 远端那条改号为 {next_id}，两份都保留",
        })
        next_id += 1

    return list(out.values()), next_id


# ---------------------------------------------------------------- 同步引擎
class SyncEngine:
    """一次同步的完整流程。**每次同步新建一个实例**，DEK 只存在内存里。"""

    def __init__(self, client: PhixClient, dek: bytes, user_id: int, username: str,
                 data_dir: Path | None = None, device: str | None = None,
                 objects=None):
        self.client = client
        self.dek = dek
        self.user_id = user_id
        self.username = username
        self.root = Path(data_dir) if data_dir else fs.root()
        self.device = device or socket.gethostname()
        self.objects = list(objects) if objects else list(DEFAULT_OBJECTS)

    # ---------- 状态文件 ----------
    #
    # 状态与快照**按账号隔离**：`data/.sync/accounts/<账号>/`。
    # 为什么必须隔离：快照是"上次同步后的明文"，三方合并靠它判断删除。
    # 如果在同一个 data/ 上换了 phix 账号而沿用旧账号的快照，
    # 新账号的云端没有的内容会被判成"远端删除了"→ **把本地数据删掉**。
    # 两个程序（PHL/PLL）用同一个账号时共享同一份，这正是我们想要的。

    def _account_dir(self) -> Path:
        return self.root / SYNC_DIR / "accounts" / account_dir_name(self.username)

    @property
    def state_path(self) -> Path:
        return self._account_dir() / STATE_NAME

    @property
    def snapshot_dir(self) -> Path:
        return self._account_dir() / SNAPSHOT_SUBDIR

    def _migrate_legacy_state(self) -> None:
        """把老布局的 ``.sync/state.json``（没分账号）搬进按账号的目录。

        只在**它确实属于当前账号**时搬；搬是"复制"，旧文件原样留着不动。
        """
        legacy = self.root / SYNC_DIR / STATE_NAME
        if not legacy.is_file() or self.state_path.exists():
            return
        doc = fs.load_json(legacy, None)
        if not isinstance(doc, dict) or not doc.get("objects"):
            return
        owner = doc.get("username")
        if owner and owner != self.username:
            return                      # 是别的账号的 → 绝不动
        self.save_state(doc)
        old_snap = self.root / SYNC_DIR / SNAPSHOT_SUBDIR
        if old_snap.is_dir():
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
            for p in old_snap.glob("*.json"):
                dst = self.snapshot_dir / p.name
                if not dst.exists():
                    try:
                        shutil.copy2(p, dst)
                    except OSError:
                        pass
        # 老状态是"一台机器一份"、谁写的都可能，它记的 sha256 未必对应本轮的本地文件。
        # 把 sha256 清掉 → `load_snapshot` 不认这些老快照 → 第一轮退化成"只取并集"，
        # 重建可信基线；宁可晚一轮传播删除，也绝不猜着删数据。
        # 快照文件本身**保留不删**（只是暂时不认它）。
        state = self.load_state()
        objs = state.get("objects") or {}
        if objs:
            for entry in objs.values():
                if isinstance(entry, dict):
                    entry["sha256"] = ""
                    entry["migrated"] = True
            self.save_state(state)

    def load_state(self) -> dict:
        self._migrate_legacy_state()
        return fs.load_json(self.state_path, {}) or {}

    def save_state(self, state: dict) -> None:
        state["version"] = 1
        state["kind"] = "phix-sync-state"
        state["conflicts"] = [_json_safe(c) for c in (state.get("conflicts") or [])][-50:]
        fs.save_json(self.state_path, state)

    def _snap_path(self, name: str) -> Path:
        return self.snapshot_dir / (name.replace(":", "__").replace("/", "__") + ".json")

    def load_snapshot(self, name: str):
        """取"上次同步后的明文"作为三方合并的**基版**。

        **只有当状态文件里的 sha256 与快照内容一致时才认它。**
        快照文件可能来自别处（老布局迁移过来的、被别的程序改写的），
        拿一份不完整或不属于本轮的明文当基版，会把"本地没有"误判成"本地删了"，
        于是**静默删掉远端的数据**（实测：base 有 5 条、本地只剩 1 条、
        远端 5 条 → 合并结果只剩 1 条，还把删除推回云端）。

        对不上就返回 None → 合并退化成"只取并集"（没有基版时的既有语义），
        宁可这轮不传播删除，也绝不猜着删。
        """
        snap = fs.load_json(self._snap_path(name), None)
        if snap is None:
            return None
        entry = ((self.load_state().get("objects") or {}).get(name)) or {}
        if entry.get("sha256") != _hash_doc(snap):
            return None
        return snap

    def save_snapshot(self, name: str, doc) -> None:
        fs.save_json(self._snap_path(name), doc)

    # ---------- 收集 / 写回 ----------
    def _agent_ids(self) -> list[str]:
        d = self.root / fs.AGENT_DIR
        if not d.is_dir():
            return []
        return sorted(p.stem for p in d.glob("*.json") if p.is_file())

    def all_object_names(self) -> list[str]:
        """默认对象 + 本地现有的每个 AI 会话（一个会话一个对象）。"""
        names = list(self.objects)
        names += [f"agent:{i}" for i in self._agent_ids()]
        return names

    def collect(self, name: str):
        """把本地内容读成可 JSON 序列化的文档；不存在返回 None。

        注意：这里一律读 **self.root**（引擎自己的数据根），而不是全局
        `fs.load_settings()` —— 后者写死了 `paths.data_dir()`，在副本/测试环境上会读错地方。
        """
        settings = fs.load_settings_at(self._settings_path) or {}
        if name == "settings.accounts":
            return {"accounts": settings.get("accounts") or {}}
        if name == "settings.lessons":
            return {"lessons": settings.get("lessons") or []}
        if name == "settings.ui":
            return {"ui": settings.get("ui") or {}}
        if name == "settings.ai":
            # **同步载荷与本地文档结构解耦**(2026-09-13 三端统一)：
            # 本地 settings.yaml 还是 {"ai": {"providers":[{id,name,protocol,base_url,
            # api_key,models,notes}], "active_provider_id", "active_model"}},
            # 上云的一律是**规范形态** {"providers":[{name,protocol,base_url,model,
            # api_key}], "default_index", "updated_at", "updated_by"} —— 与网页端、
            # PHL 读写同一个对象，任何一端改完三端都能读。
            ai = settings.get("ai") or {}
            doc = aiconfig.serialize(aiconfig.normalize_local(ai), writer="pll")
            stored_at = ai.get("updated_at")
            stored_by = ai.get("updated_by")
            missing_at = not (isinstance(stored_at, str) and stored_at.strip())
            missing_by = not (isinstance(stored_by, str) and stored_by.strip())
            if missing_at or missing_by:
                # **本地从没记过时间戳/署名**：`serialize` 兜底盖了个"现在"，而这个值
                # 每轮都会变 —— 那就永远收敛不了（每轮 collect 都像是"本地改了"，每轮
                # 空推一份）。所以把这两个字段**立刻写回本地文档**，下一轮读回来就是
                # 同一个值。只写这两个字段，别的（本地专有/未知字段）一律不碰。
                # （`updated_by` 也必须一起补：只补 updated_at 的话，第二轮的载荷会
                #  因为多了一个 updated_by 而与第一轮不同 —— 同样收敛不了。）
                stamp = stored_at if not missing_at else (doc.get("updated_at") or "")
                byline = stored_by if not missing_by else (doc.get("updated_by") or "pll")

                def remember(d, at=stamp, by=byline):
                    section = d.get("ai")
                    section = dict(section) if isinstance(section, dict) else {}
                    if not (isinstance(section.get("updated_at"), str)
                            and section["updated_at"].strip()):
                        section["updated_at"] = at
                    if not (isinstance(section.get("updated_by"), str)
                            and section["updated_by"].strip()):
                        section["updated_by"] = by
                    d["ai"] = section
                    return d

                fs.update_settings_at(self._settings_path, remember)
                # 把刚落盘的值同步到**这一轮**的载荷上：否则第一轮少一个 updated_by、
                # 第二轮才出现，两轮的哈希不同 → 又是一轮空推。
                doc["updated_at"] = stamp
                doc["updated_by"] = byline
            return {"ai": doc}
        if name == "schedule":
            return fs.load_json(self.root / fs.SCHEDULE, None)
        if name == "timetable":
            return fs.load_json(self.root / "Timetable", None)
        if name == "school":
            return fs.load_json(self.root / "School", None)
        if name == "profile":
            return fs.load_json(self.root / "Profile", None)
        if name == "mood":
            return fs.load_json(self.root / "Mood", None)
        if name.startswith("agent:"):
            sid = name.split(":", 1)[1]
            return fs.load_json(self.root / fs.AGENT_DIR / f"{sid}.json", None)
        return None

    @property
    def _settings_path(self) -> Path:
        return self.root / fs.SETTINGS

    def _apply_ai(self, current: dict, payload) -> dict:
        """把 **规范形态** 的 AI 配置落回本地 ``settings.yaml`` 的 ai 段。

        - 只换 ``providers`` / ``active_provider_id`` / ``active_model``
        - 每条本地的 ``id`` / ``models`` 列表 / ``notes`` 以及 ai 段里其它未知字段
          一律保留(同步载荷与本地文档结构解耦)
        - 同步元信息 ``updated_at`` / ``updated_by`` **写进本地文档**: 这样下一轮
          ``collect()`` 读回来的哈希与云端一致, 不会"每轮都以为本地改了"而空推一份。

        哪条是"本机当前在用": 云端载荷的 ``local_index``(本机上次同步时用的那条,
        别的设备改不动它)优先; 没有就按原来的 active 名字对; 再不行用 default_index。

        注意: 这是给 ``fs.update_settings_at`` 的 mutate 回调 —— **只能改传进来的
        这份文档并返回**, 绝不能在回调里再调一次 ``update_settings_at``
        (那把锁是同一把, 会直接自锁死, 实测挂住整轮同步)。
        """
        payload = payload if isinstance(payload, dict) else {}
        meta = {k: payload.get(k) for k in ("updated_at", "updated_by") if payload.get(k)}
        local_index = payload.get("local_index")
        index = None
        if local_index is not None:
            try:
                index = int(local_index)
            except (TypeError, ValueError):
                index = None
        out = aiconfig.apply_to_local_doc(current, payload, index=index, writer="pll")
        ai = out.get("ai")
        if isinstance(ai, dict):
            ai.update(meta)
        return out

    def _apply_removal(self, name: str) -> bool:
        """按「云端把这个对象删了」清理本地。返回**是否真的清理掉了**。

        只为可以安全删除的对象动手；`schedule` / `timetable` / `school` 是两个程序
        共用的**大文件**（用户日程、整周课表、学校快照），误删代价太大 ——
        这里一律保留本地，由调用方报告出去。
        """
        if name.startswith("agent:"):
            sid = name.split(":", 1)[1]
            p = self.root / fs.AGENT_DIR / f"{sid}.json"
            try:
                p.unlink(missing_ok=True)
                return True
            except OSError:
                return False
        if name.startswith("settings."):
            section = name.split(".", 1)[1]
            if section not in ("accounts", "lessons", "ui", "ai"):
                return False

            def mutate(d):
                d[section] = [] if section == "lessons" else {}

            fs.update_settings_at(self._settings_path, mutate)
            return True
        return False

    def apply(self, name: str, doc) -> None:
        """把合并结果写回本地（一律读-改-写 + 原子替换）。

        ``doc is None`` 表示「云端把这个对象删了」→ 走 ``_apply_removal``。
        """
        if doc is None:
            self._apply_removal(name)
            return
        if name.startswith("settings."):
            section = name.split(".", 1)[1]
            value = doc.get(section)
            if section == "lessons":
                value = value or []
            elif section in ("accounts", "ui", "ai"):
                value = value or {}

            if section == "ai":
                # 云端来的**规范形态**要落回本地的 ai 段结构(ID/models 列表/notes
                # 全保留), 而不是把规范形态原样塞进 settings.yaml。
                apply_ai = self._apply_ai

                def mutate(d):
                    return apply_ai(d, value)

                fs.update_settings_at(self._settings_path, mutate)
                return

            def mutate(d):
                d[section] = value

            fs.update_settings_at(self._settings_path, mutate)
            return
        if name == "schedule":
            fs.save_json(self.root / fs.SCHEDULE, doc)
            return
        if name == "timetable":
            days = doc.get("days") if isinstance(doc, dict) else None
            if isinstance(days, dict):
                # 直接落盘整份合并结果，**不走 write_timetable_days** ——
                # 那个函数会顺手把 updated_at/app 改成"现在/PLL"，于是本地文件的哈希
                # 永远对不上刚记的快照，表现为"每轮同步都说 timetable 有变化"。
                # 未知字段在合并时已经保留了（out = dict(local) + remote 的非 days 字段）。
                fs.save_json(self.root / "Timetable", doc)
            return
        if name == "school":
            fs.save_json(self.root / "School", doc)
            return
        if name == "profile":
            fs.save_json(self.root / "Profile", doc)
            return
        if name == "mood":
            fs.save_json(self.root / "Mood", doc)
            return
        if name.startswith("agent:"):
            sid = name.split(":", 1)[1]
            fs.save_json(self.root / fs.AGENT_DIR / f"{sid}.json", doc)

    # ---------- 各对象的合并 ----------
    def merge(self, name: str, base, local, remote):
        """返回 (merged_doc, conflicts)。"""
        conflicts: list[dict] = []

        if name == "settings.ai":
            # AI 配置走**规范化后的专用合并**(2026-09-13 三端统一):
            # 三方一律先归一成 {providers:[{name,protocol,base_url,model,api_key}],
            # default_index}, 再按**服务商名称**对齐逐条比新旧。
            # 直接用 _merge_dict 不行: 规范形态里的 providers 是**列表**,
            # `_merge_scalar` 只会整体替换 → 一方新加的服务商会被另一方抹掉。
            local_payload = (local or {}).get("ai") if isinstance(local, dict) else None
            remote_payload = (remote or {}).get("ai") if isinstance(remote, dict) else None
            base_payload = (base or {}).get("ai") if isinstance(base, dict) else None
            if aiconfig.is_empty(remote_payload) and not aiconfig.is_empty(local_payload):
                # 远端是这个对象的老形态(或干脆没配过) → 一律按"只取并集"处理,
                # 绝不因为远端空就把本地配好的服务商清掉。
                remote_payload = None
            merged = aiconfig.merge_payload(base_payload, local_payload, remote_payload,
                                            path="ai", conflicts=conflicts)
            doc = aiconfig.serialize(merged, writer="pll")
            if merged.get("from_settings") and not aiconfig._text(merged.get("updated_by")):
                doc.pop("updated_by", None)
            return {"ai": doc}, conflicts

        if name in ("settings.accounts", "settings.ui"):
            section = name.split(".", 1)[1]
            merged = _merge_dict(
                (base or {}).get(section) if isinstance(base, dict) else {},
                (local or {}).get(section) if isinstance(local, dict) else {},
                (remote or {}).get(section) if isinstance(remote, dict) else {},
                section, conflicts,
            )
            return {section: merged}, conflicts

        if name == "settings.lessons":
            def key(row):
                return "|".join([str(row.get("subject") or "").strip(),
                                 str(row.get("group") or "").strip(),
                                 str(row.get("teacher") or "").strip()])

            merged = _merge_list_of_dicts(
                (base or {}).get("lessons"),
                (local or {}).get("lessons"),
                (remote or {}).get("lessons"),
                key, "lessons", conflicts,
            )
            return {"lessons": merged}, conflicts

        if name == "schedule":
            base, local, remote = base or {}, local or {}, remote or {}
            last_ids = [int(v) for v in (base.get("lastId"), local.get("lastId"),
                                         remote.get("lastId")) if isinstance(v, int)]
            all_ids = [e.get("id") for e in
                       ((base.get("events") or []) + (local.get("events") or [])
                        + (remote.get("events") or []))
                       if isinstance(e, dict) and isinstance(e.get("id"), int)]
            events, next_id = _merge_events(base.get("events"), local.get("events"),
                                            remote.get("events"),
                                            max(last_ids + all_ids + [0]) + 1, conflicts)
            events.sort(key=lambda e: (str(e.get("day") or ""), str(e.get("time") or ""),
                                       e.get("id") or 0))
            out = dict(local)
            out.update({k: v for k, v in remote.items() if k not in ("events", "lastId")})
            out["events"] = events
            out["lastId"] = max([next_id - 1] + last_ids + all_ids + [0])
            out.setdefault("version", 1)
            out.setdefault("kind", "pinghe-schedule")
            return out, conflicts

        if name == "timetable":
            base, local, remote = base or {}, local or {}, remote or {}
            bd = base.get("days") or {}
            ld = local.get("days") or {}
            rd = remote.get("days") or {}
            out_days = {}
            for day in sorted(set(bd) | set(ld) | set(rd)):
                b, l, r = bd.get(day), ld.get(day), rd.get(day)  # noqa: E741
                # 护栏：远端"空的一天"绝不许清掉本地已有的课表
                if isinstance(r, list) and not r and isinstance(l, list) and l:
                    out_days[day] = l
                    continue
                if l is None and r is None:
                    continue
                if l is None:
                    out_days[day] = r
                    continue
                if r is None:
                    out_days[day] = l
                    continue

                def key(row):
                    return "|".join([str(row.get("subject") or ""),
                                     str(row.get("start") or ""),
                                     str(row.get("group") or "")])

                # 课表是抓取缓存：**只取并集**（union_only），不当成用户可删数据
                out_days[day] = _merge_list_of_dicts(
                    None, l, r, key, f"days.{day}", conflicts, union_only=True)
            out = dict(local)
            out.update({k: v for k, v in remote.items() if k != "days"})
            out["days"] = out_days
            out.setdefault("version", 1)
            out.setdefault("kind", "pinghe-timetable")
            return out, conflicts

        if name == "school":
            base, local, remote = base or {}, local or {}, remote or {}
            out = dict(local)
            # managebac / edupage 复用项目里已有的、踩过坑的合并函数
            if isinstance(remote.get("managebac"), dict):
                out["managebac"] = sharedschool.merge_managebac(
                    local.get("managebac"), remote.get("managebac"))
            if isinstance(remote.get("edupage"), dict):
                out["edupage"] = sharedschool.merge_edupage(
                    local.get("edupage"), remote.get("edupage"))
            # mail 段只是摘要；两边都有就取 fetched_at 较新的那份
            if isinstance(remote.get("mail"), dict) and isinstance(local.get("mail"), dict):
                out["mail"] = _pick_newer(local["mail"], remote["mail"])
            elif isinstance(remote.get("mail"), dict):
                out["mail"] = remote["mail"]
            for k, v in remote.items():
                if k not in ("managebac", "edupage", "mail"):
                    out.setdefault(k, v)
            out.setdefault("version", 1)
            out.setdefault("kind", "pinghe-school")
            return out, conflicts

        if name.startswith("agent:"):
            local, remote = local or {}, remote or {}
            lh = local.get("history") or []
            rh = remote.get("history") or []
            if len(rh) > len(lh):
                out = dict(local)
                out.update({k: v for k, v in remote.items()})
                return out, conflicts
            if len(rh) == len(lh) and rh != lh:
                conflicts.append({"path": name, "local": f"{len(lh)} 条",
                                  "remote": f"{len(rh)} 条",
                                  "note": "长度相同但内容不同 → 保留本地"})
            return local, conflicts

        # 未知对象：整体替换型三方合并
        return _merge_scalar(base, local, remote, name, conflicts), conflicts

    # ---------- 主流程 ----------
    def sync(self, names=None, dry_run=False, force=False,
             prefer: str = "merge") -> dict:
        """跑一轮同步。返回可读报告（也会写进 state.conflicts）。

        `prefer` 是**用户选的方向**（登录时"本地有数据、云端也有数据"会问一次）：
          · `merge`（默认）—— 三方合并，两边都不丢
          · `local`  —— 用本地覆盖云端（本地赢）
          · `remote` —— 用云端覆盖本地（云端赢）
        `local`/`remote` 只在两边**都真的有内容**时生效，否则自动退回 `merge`：
        让空的一边赢会直接把另一边的数据抹掉。
        """
        prefer = prefer if prefer in ("merge", "local", "remote") else "merge"
        report = {
            "ok": True, "server": self.client.server, "username": self.username,
            "started_at": fs.now_iso(), "objects": {}, "conflicts": [],
            "skipped": None, "pulled": [], "pushed": [], "errors": [],
            "prefer": prefer,
        }

        # 并发护栏：对方程序在跑就别抢着写（交接文档 §7.6 的保守方案）
        sib = shareddata.sibling_running(self.root, "pll")
        if sib and not force:
            report["ok"] = False
            report["skipped"] = f"{sib.get('name')} 正在运行，这轮同步先跳过（避免两边抢写）"
            return report

        try:
            man = self.client.manifest()
        except PhixError as exc:
            report["ok"] = False
            report["errors"].append(f"取清单失败：{exc}")
            return report

        remote_map = {o["name"]: o for o in man.get("objects", [])}
        state = self.load_state()
        state_objs = state.get("objects") or {}
        names = names or self.all_object_names()

        for name in names:
            if self._is_forbidden(name):
                report["objects"][name] = {"action": "skip", "reason": "在禁止上云名单里"}
                continue
            try:
                entry = self._sync_one(name, remote_map.get(name), state_objs.get(name),
                                       dry_run=dry_run, prefer=prefer)
            except PhixError as exc:
                entry = {"action": "error", "error": exc.message, "code": exc.code}
                report["errors"].append(f"{name}：{exc.message}")
            except Exception as exc:  # noqa: BLE001  单个对象出错不能拖垮整轮
                entry = {"action": "error", "error": f"{type(exc).__name__}: {exc}"}
                report["errors"].append(f"{name}：{type(exc).__name__}: {exc}")
            report["objects"][name] = entry
            if entry.get("conflicts"):
                report["conflicts"].extend(
                    [{"object": name, **c} for c in entry["conflicts"]])
            if entry.get("pulled"):
                report["pulled"].append(name)
            if entry.get("pushed"):
                report["pushed"].append(name)

        if not dry_run:
            # 注意：必须**重新读**状态文件再补元信息。`_record()` 每次都已把
            # objects[name] 写进去了，这里若用循环开始时的旧 state_objs 覆盖，
            # 会把整轮同步的进度全部抹掉 → 表现为"永远同步不完 / 永不收敛"。
            fresh = self.load_state()
            fresh["server"] = self.client.server
            fresh["user_id"] = self.user_id
            fresh["username"] = self.username
            fresh["device"] = self.device
            fresh["last_sync_at"] = fs.now_iso()
            fresh.setdefault("objects", {})
            fresh["conflicts"] = report["conflicts"][-50:]
            self.save_state(fresh)
            report["ok"] = not report["errors"]
        report["finished_at"] = fs.now_iso()
        return report

    # -- 登录后：要不要先问用户"用哪边覆盖哪边" --
    def probe(self) -> dict:
        """只看事实：本地每个对象有没有内容、云端有没有这个对象。

        **不下载密文、不解密、不写任何同步文件**，所以可以放心在登录后马上调。
        （唯一的写动作是 `collect("settings.ai")` 会给缺时间戳的本地
        settings.yaml 补一个 `updated_at`/`updated_by` —— 这与正式同步一致。）
        """
        out: dict = {"needs_choice": False, "objects": {}, "both": [],
                     "server": self.client.server, "error": None}
        try:
            man = self.client.manifest()
        except PhixError as exc:
            out["error"] = f"取清单失败：{exc}"
            return out
        remote_map = {o["name"]: o for o in man.get("objects", [])}
        for name in self.all_object_names():
            if self._is_forbidden(name):
                continue
            try:
                local = self.collect(name)
            except Exception:  # noqa: BLE001  本地某个文件读坏了不能拖垮探测
                local = None
            rentry = remote_map.get(name) or {}
            has_remote = bool(rentry) and not rentry.get("deleted")
            out["objects"][name] = {"local": not _is_empty_doc(local),
                                    "remote": has_remote}
        out["both"] = sorted(k for k, v in out["objects"].items()
                             if v["local"] and v["remote"])
        out["needs_choice"] = bool(out["both"])
        return out

    # -- 单个对象 --
    def _sync_one(self, name, remote_entry, state_entry, dry_run=False,
                  prefer: str = "merge") -> dict:
        out: dict = {"action": "noop", "conflicts": []}
        local = self.collect(name)
        base = self.load_snapshot(name)
        local_hash = _hash_doc(local)

        if remote_entry is None:
            # 远端还没有这个对象
            if local is None:
                out["action"] = "skip"
                return out
            out.update(action="push", pushed=True)
            if not dry_run:
                env = pc.seal_object(self.dek, self.user_id, name,
                                     _dump(local))
                res = self.client.put_object(name, 0, env, self.device)
                self._record(state_entry, name, res, local)
            return out

        remote_rev = remote_entry["revision"]
        synced_rev = (state_entry or {}).get("revision", 0)
        local_changed = (state_entry or {}).get("sha256") != local_hash
        remote_changed = remote_rev != synced_rev

        if remote_entry.get("deleted"):
            # 云端把这个对象删了
            if local is None:
                out["action"] = "remote-deleted"
                if not dry_run:
                    self._record(state_entry, name,
                                 {"revision": remote_rev}, None,
                                 extra={"remote_deleted": True})
                return out
            prev = state_entry or {}
            if prev.get("remote_deleted") and not local_changed:
                # 上一轮已经处理过了（比如"共用大文件保留本地"），别再每轮都报一次
                out["action"] = "noop"
                return out
            if local_changed:
                # 本地改过 → 保留本地并**推回云端**，等于否决这次删除。
                # 只报冲突不推的话，两边会每轮都吵一次，永远收敛不了。
                out["conflicts"].append({
                    "path": name, "local": "本地有内容", "remote": None,
                    "note": "云端删了这个对象、但本地改过 → 保留本地并推回云端"})
                out.update(action="push", pushed=True)
                if not dry_run:
                    env = pc.seal_object(self.dek, self.user_id, name, _dump(local))
                    try:
                        res = self.client.put_object(name, remote_rev, env, self.device)
                    except PhixError as exc:
                        if not exc.is_conflict:
                            raise
                        fresh = self.client.get_object(name)
                        res = self.client.put_object(
                            name, fresh.get("revision", remote_rev), env, self.device)
                    self._record(state_entry, name, res, local)
                return out

            removed = self._apply_removal(name)
            out["action"] = "pull-delete" if removed else "kept-local"
            out["pulled"] = removed
            if not removed:
                out["conflicts"].append({
                    "path": name, "local": "本地有内容", "remote": None,
                    "note": "云端删了这个对象；它是两个程序共用的大文件"
                            "（日程 / 课表 / 学校数据），本地保留着，没有自动删"})
            if not dry_run:
                self._record(state_entry, name,
                             {"revision": remote_rev}, None if removed else local,
                             extra={"remote_deleted": True})
            return out

        payload = self.client.get_object(name)
        remote_env = payload.get("payload")
        remote = None
        if remote_env:
            try:
                remote = json.loads(pc.unseal_object(
                    self.dek, self.user_id, name, remote_env).decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                raise PhixError("decrypt_failed",
                                f"解不开远端密文（口令不对？）：{exc}") from exc

        if not remote_changed and not local_changed:
            out["action"] = "noop"
            return out

        # 其余情况**一律走三方合并**。合并才是安全操作：曾经给"只有远端变了"
        # 做过直接 apply 的快捷路径，结果绕过了课表"空的一周不许清空"这类护栏，
        # 实测把本地 71 张课卡清成了 0。
        merged, conflicts = self.merge(name, base, local, remote)
        # 用户在登录时选了"用本地覆盖云端 / 用云端覆盖本地"：这时不做三方合并，
        # 整份取被选中的那一边（缺一边或一边是空的会自动退回合并结果）。
        merged, conflicts = _apply_preference(
            name, prefer, local, remote, merged, conflicts)
        out["conflicts"] = conflicts
        merged_hash = _hash_doc(merged)
        remote_hash = _hash_doc(remote)

        need_write_local = merged_hash != local_hash
        need_push = merged_hash != remote_hash
        if not need_write_local and not need_push:
            out["action"] = "noop"
            # **首次同步且本地恰好与云端一致时，也必须把基线记下来。**
            # 不记的话这个对象的 state 就永远是空的，于是：
            #   local_changed 永远为 True（拿 None 跟哈希比）
            #   → "云端把这个对象删了"会被误判成"本地改过"，拒绝跟随删除；
            #   → 每轮都要白拉一次密文再解密（慢，且日志一直有动作）。
            # 实测：新设备首拉时因为两边数据本就相同，7 个对象全是 noop、一个都没记，
            # 随后云端删除根本传播不过来。
            if not dry_run and local is not None:
                prev = state_entry or {}
                if (prev.get("revision") != remote_rev
                        or prev.get("sha256") != merged_hash):
                    self._record(state_entry, name, payload, merged)
            return out
        out["action"] = ("merge" if (need_write_local and need_push)
                         else "pull" if need_write_local else "push")
        out["pulled"] = need_write_local
        out["pushed"] = need_push
        if dry_run:
            return out

        if need_write_local:
            self.apply(name, merged)
            # 落盘后**重新读一遍**再定快照：写入路径可能顺手改写 updated_at/app
            # 之类的字段，不重读就会"快照哈希永远对不上 → 每轮都以为有变化"。
            after = self.collect(name)
            if after is not None:
                merged = after
                merged_hash = _hash_doc(merged)

        need_push = merged_hash != remote_hash
        out["pushed"] = need_push
        if not need_push:
            out["action"] = "pull"
            self._record(state_entry, name, payload, merged)
            return out

        env = pc.seal_object(self.dek, self.user_id, name, _dump(merged))
        applied_hash = merged_hash
        try:
            res = self.client.put_object(name, remote_rev, env, self.device)
        except PhixError as exc:
            if not exc.is_conflict:
                raise
            # 有人在我们同步期间又写了 → 拉最新再合并一次（最多重试 3 次）
            res = None
            for _ in range(3):
                fresh = self.client.get_object(name)
                try:
                    fdoc = json.loads(pc.unseal_object(
                        self.dek, self.user_id, name,
                        fresh["payload"]).decode("utf-8"))
                except Exception:  # noqa: BLE001
                    break
                merged, more = self.merge(name, base, merged, fdoc)
                merged, more = _apply_preference(
                    name, prefer, local, remote, merged, more)
                conflicts.extend(more)
                out["conflicts"] = conflicts
                if _hash_doc(merged) != applied_hash:
                    self.apply(name, merged)
                    applied_hash = _hash_doc(merged)
                env = pc.seal_object(self.dek, self.user_id, name, _dump(merged))
                try:
                    res = self.client.put_object(name, fresh["revision"], env,
                                                 self.device)
                    break
                except PhixError as exc2:
                    if not exc2.is_conflict:
                        raise
            if res is None:
                raise PhixError("revision_conflict",
                                "远端一直在被别人改，这轮先跳过（下轮再合）")
        self._record(state_entry, name, res, merged)
        return out

    def _record(self, state_entry, name, res, doc, extra: dict | None = None) -> None:
        """写回 state.objects 与本地明文快照。"""
        state = self.load_state()
        objs = state.setdefault("objects", {})
        entry = {
            "revision": int(res.get("revision") or 0),
            "sha256": _hash_doc(doc),
            "synced_at": fs.now_iso(),
        }
        if extra:
            entry.update(extra)
        objs[name] = entry
        state["server"] = self.client.server
        state["user_id"] = self.user_id
        state["username"] = self.username
        state["device"] = self.device
        self.save_state(state)
        if doc is None:
            p = self._snap_path(name)
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            self.save_snapshot(name, doc)

    @staticmethod
    def _is_forbidden(name: str) -> bool:
        """禁止上云判定。既认对象名（``settings.lessons``、``agent:<id>``），
        也认**路径形态**（``phll/managebac/session_x.json``）—— 只要第一段命中
        黑名单目录名就拒绝，避免以后有人把文件路径直接当对象名传进来。
        """
        if not isinstance(name, str) or not name.strip():
            return True
        n = name.strip().strip("/")
        if n in NEVER_SYNC:
            return True
        head = n.split("/", 1)[0]          # 目录名或文件名
        if head in NEVER_SYNC:
            return True
        ns = n.split(":", 1)[0]            # 命名空间（settings.* / agent:*）
        return ns in NEVER_SYNC


# ---------------------------------------------------------------- 工具
def _json_safe(v):
    """把任何非 JSON 类型（尤其是内部哨兵）转成可序列化的形式。

    兜底用：冲突记录会直接写进状态文件，一条不可序列化的值就会让整轮同步失败。
    """
    if v is _MISSING:
        return None
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_json_safe(x) for x in v]
    return str(v)


def _has_json(resp) -> bool:
    """响应体看起来是不是 JSON（错误页可能是 HTML）。"""
    ctype = (resp.headers.get("Content-Type") or "").lower()
    return "json" in ctype


def _dump(doc) -> bytes:
    return json.dumps(doc, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _hash_doc(doc) -> str:
    if doc is None:
        return ""
    return hashlib.sha256(_dump(doc)).hexdigest()


def account_dir_name(username: str) -> str:
    """账号名 → 安全的单层目录名。**PLL 与 PHL 必须逐字符一致**（否则两台程序
    各记各的状态，三方合并会退化）。

    - 非 ``[A-Za-z0-9._@+-]`` 的字符换成 ``_``
    - 截断到 60 个字符
    - 空、或纯点串（``.`` / ``..`` / ``...``）→ ``default``
      （纯点串不含非法字符，会原样变成目录名，然后被 path.join 解析到上一级）
    """
    safe = re.sub(r"[^A-Za-z0-9._@+-]", "_", username or "")[:60]
    if not safe or set(safe) <= {"."}:
        return "default"
    return safe


def device_name() -> str:
    try:
        return socket.gethostname() or "本机"
    except Exception:  # noqa: BLE001
        return "本机"


def data_root() -> Path:
    return paths.data_dir()
