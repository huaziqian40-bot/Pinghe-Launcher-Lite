"""phix 会话：把"登录 → 拿令牌 → 解出 DEK → 云同步"串起来的一层。

**DEK 只在内存里，绝不落盘**：进程退出就没了，下次运行重新用口令解一次。
这样即便有人拷走整个 ``data/``，没有口令也打不开云端的任何东西。

配置写在 ``settings.yaml`` 的 ``phix`` 段（非机密：服务器地址、用户名、开关），
令牌走 ``secrets_extra``（与四平台凭据同一个文件，用户已知晓）。

**令牌落盘契约（三键制，PLL 与 PHL 逐字一致）**
------------------------------------------------

===============  ==========================================  ==========================
键               语义                                        谁写
===============  ==========================================  ==========================
``phix:token``   **老式长期令牌**（兼容期用；服务端          登录/注册响应里**有** ``token``
                 ``PHIX_LEGACY_TOKENS`` 关掉后作废）。        时才写（续期响应里没有 → 不动它）
                 **不再当业务 Bearer 的首选**
``phix:access_token``   15 分钟的 Ed25519 JWT，**业务请求     登录 / 注册 / 每次续期
                        的 Bearer 首选**
``phix:refresh_token``  续期凭据（30 天，**用一次换一次**），  登录 / 注册 / 每次轮换
                        **只**喂给 ``/auth/refresh``
===============  ==========================================  ==========================

两条铁律（PHL 的 ``electron/phix-session.cjs`` 同款，谁改都得改两处）：

1. **读兼容**：读 ``phix:access_token`` / ``phix:refresh_token``；新键缺失时回落到
   旧键 —— ``phix:token``（老实现把访问令牌存在这儿，**只认像 JWT 的三段式**）
   与 ``phix:refresh``（老实现的续期键）。读到旧键就**顺手补写新键**（迁移）；
   旧键可以留着，但**不再依赖它**。
2. **写新键、不删别人的**：只往新键上补写；**除登出外不删任何键**（尤其不碰
   PHL 写在同一个 ``secrets_extra`` 里的东西）。登出时把这四个 phix 令牌键
   （三键 + 旧 ``phix:refresh``）一起清掉 —— 留着旧 refresh 会被上面的回落
   逻辑当成"还登着"。

**续期不碰密钥**：``/auth/refresh`` 只换令牌，响应里没有 ``key_wrap`` 之类的字段，
本层也只在续期后写上面几串令牌 —— DEK / KEK 一个字节都不会被写进任何文件。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from . import cloudsync as cs
from . import filestore as fs
from . import phixcrypto as pc
from .logutil import error as _log_error, warn as _log_warn

TOKEN_KEY = "phix:token"                  # 老式长期令牌（兼容期；不再当 Bearer 首选）
ACCESS_KEY = "phix:access_token"          # 15 分钟的 JWT → 业务请求的 Bearer
REFRESH_KEY = "phix:refresh_token"        # 续期凭据：只给 /auth/refresh
LEGACY_REFRESH_KEY = "phix:refresh"       # PLL 旧实现的续期键：**只读兼容**，不再写
DEFAULT_INTERVAL_MINUTES = 10


def looks_like_jwt(value: str) -> bool:
    """三段式（两个点）且每段非空 → 像 Ed25519 JWT。

    用途只有一个：判断老键 ``phix:token`` 里那串是"老实现存进去的访问令牌"还是
    "老式长期令牌"。**不能拿长期令牌当访问令牌用**（它不会续期，语义也不对），
    所以回落时只认像 JWT 的那串。与 PHL 的 ``looksLikeJwt`` 同口径。
    """
    parts = (value or "").split(".")
    return len(parts) == 3 and all(parts)



def default_config() -> dict:
    return {
        "server": "",
        "username": "",
        "user_id": 0,
        "device": cs.device_name(),
        "key_mode": "password",
        "auto_sync": True,
        "sync_interval_minutes": DEFAULT_INTERVAL_MINUTES,
        "objects": list(cs.DEFAULT_OBJECTS),
        "last_sync_at": "",
        # 应用层加密传输（对应 加密链路思路.md §3）：默认开。
        # 关掉就退回明文 HTTP —— 只有在**已经走 HTTPS** 时才建议关。
        "e2e": True,
    }


def _e2e_on() -> bool:
    try:
        return bool(load_config().get("e2e", True))
    except Exception:  # noqa: BLE001
        return True


def _pin_dir() -> Path:
    """固定服务器公钥的存放目录（**与 PHL 共用同一个位置与文件名规则**）。"""
    return fs.root() / cs.SYNC_DIR / "pinned"


def load_config() -> dict:
    doc = fs.load_settings() or {}
    cfg = dict(default_config())
    saved = doc.get("phix")
    if isinstance(saved, dict):
        cfg.update({k: v for k, v in saved.items() if k in cfg})
    return cfg


def save_config(**changes) -> dict:
    """只更新 ``phix`` 段，别的段（含用户手写的注释之外的内容）原样保留。"""

    def mutate(doc):
        section = doc.get("phix")
        if not isinstance(section, dict):
            section = {}
        for k, v in changes.items():
            if v is not None:
                section[k] = v
        doc["phix"] = section

    fs.update_settings(mutate)
    return load_config()


def _unwrap_or_fail(envelope: str, passphrase: str, salt: str, username: str,
                    what: str = "密码", algo: str = pc.KDF_ALGO) -> bytes:
    """解包 DEK。**口令错时给一句人话，别把密码学异常漏到界面上。**

    口令不对时是 AES-GCM 的认证标签先失败，`cryptography` 抛的是
    ``InvalidTag`` —— 界面直接显示出来就是一句用户看不懂的英文
    （PHL 移植时实测到同样的问题）。这里统一翻成带 code 的中文错误。
    """
    try:
        return pc.unwrap_dek(envelope, passphrase, salt, username, algo)
    except Exception as exc:  # noqa: BLE001  InvalidTag / ValueError 都算口令错
        raise cs.PhixError(
            "bad_passphrase",
            f"{what}不对（也可能是这个账号的密钥材料已损坏）") from exc


def account_dir_name(username: str) -> str:
    """账号名 → 安全的单层目录名（与 cloudsync 共用同一实现）。"""
    return cs.account_dir_name(username)


class PhixSession:
    """一个进程一份；线程安全（js_api 是多线程调用的）。"""

    def __init__(self):
        self._lock = threading.RLock()
        self.client: cs.PhixClient | None = None
        self.dek: bytes | None = None
        self.user_id: int = 0
        self.username: str = ""
        self.server: str = ""
        self.key_mode: str = "password"
        self.key_check: str = ""
        self.recovery_code: str = ""       # 注册后**只在内存里**放一会儿给界面显示
        self.last_report: dict | None = None
        self._timer: threading.Timer | None = None
        self._stop = False

    # ---------------- 状态 ----------------
    def status(self) -> dict:
        cfg = load_config()
        e2e_on = bool(cfg.get("e2e", True))
        # 一次读齐三串（含读兼容与迁移）：既省两次解析，也保证三个"有没有"
        # 报的是同一个快照。
        stored = stored_tokens()
        with self._lock:
            logged = bool(self.client and self.client.token)
            unlocked = self.dek is not None
            server = self.server or cfg.get("server") or ""
            return {
                "configured": bool(cfg.get("server") and cfg.get("username")),
                "server": server,
                "username": self.username or cfg.get("username") or "",
                "user_id": self.user_id or cfg.get("user_id") or 0,
                "logged_in": logged,
                "unlocked": unlocked,
                "key_mode": self.key_mode or cfg.get("key_mode") or "password",
                "auto_sync": bool(cfg.get("auto_sync", True)),
                "sync_interval_minutes": int(cfg.get("sync_interval_minutes")
                                             or DEFAULT_INTERVAL_MINUTES),
                "last_sync_at": cfg.get("last_sync_at") or "",
                "recovery_code": self.recovery_code,
                "device": cfg.get("device") or cs.device_name(),
                "objects": cfg.get("objects") or list(cs.DEFAULT_OBJECTS),
                "has_token": bool(stored["legacy"]),
                "has_refresh": bool(stored["refresh"]),
                # 与 PHL 的 status() 同名的三个"有没有"（**只报有没有，绝不回传令牌**）
                "has_access_token": bool(stored["access"]),
                "has_refresh_token": bool(stored["refresh"]),
                "last_report": _brief(self.last_report),
                "state": self._state_summary(),
                # 明文 HTTP 且不是本机 → 界面要显眼地提示（口令会明文过网线）。
                # **但开了应用层加密之后就不算不安全了** —— 那时网线上只有密文，
                # 有没有 HTTPS 都无所谓（这正是 加密链路思路.md §3 想要的）。
                "e2e": e2e_on,
                "encrypted": bool(getattr(self.client, "encrypted", False)),
                "insecure_transport": _is_insecure(server) and not e2e_on,
            }

    def _stored_token(self) -> str:
        """老式长期令牌（``phix:token``）。**不是**访问令牌。"""
        return _stored(TOKEN_KEY)

    def _stored_access(self) -> str:
        return stored_tokens()["access"]

    def _stored_refresh(self) -> str:
        return stored_tokens()["refresh"]

    # ---------------- 令牌落盘（三键制） ----------------
    def _persist_tokens(self, client=None) -> None:
        """把令牌写进 ``settings.yaml`` 的 ``secrets_extra``（三键制，见文件头契约）。

        **只写令牌**：不碰 ``phix`` 段、不碰密钥材料、**绝不写 DEK**；也
        **绝不删**任何键（除了登出 —— 见 `_clear_tokens`）。

        - ``phix:access_token``：当前业务请求用的访问令牌（JWT）。
        - ``phix:refresh_token``：续期令牌。**轮换后必须跟着更新**，否则下一个
          15 分钟后就续不上了；宽限期内重复续期（``rotated:false``）时
          ``client.refresh_token`` 本来就没被动过，这里写回去的还是原来那串。
        - ``phix:token``：**只在**客户端真的拿到了老式长期令牌时写
          （登录/注册响应里的 ``token``）。续期响应里没有它 → 一个字节都不动：
          老实现那头写进去的东西不是我们该抹的。
        """
        c = client if client is not None else self.client
        if c is None:
            return
        token = getattr(c, "token", "") or ""
        legacy = getattr(c, "legacy_token", "") or ""
        refresh = getattr(c, "refresh_token", "") or ""
        # `client.token` 在"只有老式令牌的老服务端"上就是那一串老令牌 —— 它没有
        # "15 分钟过期、能续期"的语义，**不能**写进 access 键（PHL 会照 JWT 用它）。
        access = token if (token and token != legacy) else ""
        try:
            from . import secrets as _secrets

            if access:
                _secrets.set(ACCESS_KEY, access)
            if refresh:
                _secrets.set(REFRESH_KEY, refresh)
            if legacy:
                _secrets.set(TOKEN_KEY, legacy)
        except Exception as exc:  # noqa: BLE001
            _log_warn(f"phix 令牌保存失败（下次要重新登录）：{exc}")

    def _clear_tokens(self) -> None:
        """登出：把本机这几串 phix 令牌全删掉（**别退化成只删一串**）。

        为什么要连旧键 ``phix:refresh`` 一起清：留着它，下一次读就会把这条已经
        被服务端作废的续期凭据**回落**成 ``phix:refresh_token``，界面上看起来
        还"登着"。**只清 phix 自己这几个令牌键**，``settings.yaml`` 里别的东西
        （四平台凭据、PHL 的字段、日程……）一个都不动。
        """
        try:
            from . import secrets as _secrets

            for key in (TOKEN_KEY, ACCESS_KEY, REFRESH_KEY, LEGACY_REFRESH_KEY):
                try:
                    _secrets.delete(key)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

    def _on_tokens_changed(self, client=None) -> None:
        """`PhixClient` 续期成功后的回调 —— 把新的 access/refresh 立刻落盘。

        服务端每续一次期就换一串新的 refresh：**不落盘的话，15 分钟后就再也续不上**
        （旧的超出宽限期再用会被判定为重放，整个会话作废）。

        注意这里**只写令牌**，DEK 仍在内存里、一个字节都不落盘。
        """
        self._persist_tokens(client)

    def _state_summary(self) -> dict:
        try:
            cfg = load_config()
            user = self.username or cfg.get("username") or ""
            base = fs.root() / cs.SYNC_DIR / "accounts" / cs.account_dir_name(user)
            st = fs.load_json(base / cs.STATE_NAME, {}) or {}
            if not st:      # 兼容还没分账号的老布局
                st = fs.load_json(fs.root() / cs.SYNC_DIR / cs.STATE_NAME, {}) or {}
            return {
                "last_sync_at": st.get("last_sync_at") or "",
                "objects": {k: v.get("revision") for k, v in (st.get("objects") or {}).items()},
                "conflicts": (st.get("conflicts") or [])[-10:],
            }
        except Exception:  # noqa: BLE001
            return {}

    # ---------------- 登录 / 注册 ----------------
    def login(self, server: str, username: str, password: str,
              sync_passphrase: str | None = None, device: str | None = None) -> dict:
        """用 phix 账号登录。成功即拿到 DEK（在内存里），可以开始同步。

        P3：登录响应里有两组令牌 —— ``access_token``（JWT，当 Bearer）与
        ``refresh_token``（续期用）。两个都由 `PhixClient` 收下，本层负责落盘。
        """
        server = resolve_server(server)
        device = device or cs.device_name()
        client = cs.PhixClient(server, e2e=_e2e_on(), pin_dir=_pin_dir())
        info = client.login(username, password, device=device)
        if not client.token:
            raise cs.PhixError("bad_response",
                               "服务端没有返回访问令牌（服务器版本可能太旧）")

        dek = None
        mode = info.get("key_mode") or "password"
        algo = info.get("kdf_algo") or pc.KDF_ALGO
        if mode == "password":
            dek = _unwrap_or_fail(info["key_wrap"], password, info["kdf_salt"],
                                  info["username"], "密码", algo)
            if not pc.check_dek(dek, info["username"], info["key_check"]):
                raise cs.PhixError("bad_passphrase",
                                   "口令能解出 DEK，但自检没通过（数据可能已损坏）")
        elif sync_passphrase:
            dek = _unwrap_or_fail(info["key_wrap"], sync_passphrase, info["kdf_salt"],
                                  info["username"], "同步口令", algo)
            if not pc.check_dek(dek, info["username"], info["key_check"]):
                raise cs.PhixError("bad_passphrase", "同步口令不对")

        with self._lock:
            self.client = client
            self.dek = dek
            self.user_id = info["user_id"]
            self.username = info["username"]
            self.server = server
            self.key_mode = mode
            self.key_check = info.get("key_check") or ""
            self.recovery_code = ""

        # 到这里登录才算真的成功 —— 现在才把令牌挂上回调、写进磁盘
        # （口令错的时候不该在盘上留下一条"登录过"的令牌）。
        client.on_tokens = self._on_tokens_changed
        self._persist_tokens(client)

        save_config(server=server, username=info["username"], user_id=info["user_id"],
                    device=device, key_mode=mode)
        if dek is not None:
            self.start_auto_sync()
        return self.status()

    def unlock(self, secret: str) -> dict:
        """解开内存里的 DEK（**口令不外发**）。

        `secret` 按账号的 `key_mode` 解释：`password` 模式 = 登录密码，
        `syncphrase` 模式 = 独立同步口令。若按模式没解开，会**再试另一种解释**
        （两种口令恰好都设成一样的、或用户记混了模式时，不该白报一次错）。
        """
        with self._lock:
            if not self.client:
                raise cs.PhixError("not_logged_in", "请先登录")
            info = self.client.me()
            mode = (info.get("key_mode") or self.key_mode
                    or load_config().get("key_mode") or "password")
            attempts = ["登录密码", "同步口令"]
            if mode == "syncphrase":
                attempts.reverse()
            last_err: Exception | None = None
            for label in attempts:
                try:
                    dek = _unwrap_or_fail(info["key_wrap"], secret, info["kdf_salt"],
                                          info["username"], label)
                except cs.PhixError as exc:
                    last_err = exc
                    continue
                if pc.check_dek(dek, info["username"], info["key_check"]):
                    self.dek = dek
                    self.key_mode = mode
                    break
                last_err = cs.PhixError("bad_passphrase", f"{label}不对")
            else:
                raise (last_err or cs.PhixError("bad_passphrase", "口令不对"))
        self.start_auto_sync()
        return self.status()

    def restore(self) -> bool:
        """程序启动时用**本机存着的令牌**把登录状态接回来（不发任何网络请求）。

        没有这一步的话，重启后 `logged_in` 恒为 False（它只看内存里的 client），
        于是：设置页明明登着却**又显示登录框**、自动同步永远不启动。
        这正是用户实测报上来的现象。

        DEK 仍然**只活在内存里**（这是有意的安全设计）：恢复之后是
        `logged_in=True` + `unlocked=False`，输一次口令（登录密码或独立同步口令）
        就能继续同步。
        """
        with self._lock:
            if self.client and self.client.token:
                return True
            cfg = load_config()
            server = (self.server or cfg.get("server") or "").strip()
            if not server:
                return False
            stored = stored_tokens()
            access = stored.get("access") or stored.get("legacy") or ""
            if not access:
                return False
            try:
                client = cs.PhixClient(_norm_server(server), e2e=_e2e_on(),
                                       pin_dir=_pin_dir())
            except Exception:  # noqa: BLE001  地址坏了不该让程序起不来
                return False
            client.absorb_tokens({"access_token": stored.get("access") or "",
                                  "token": stored.get("legacy") or "",
                                  "refresh_token": stored.get("refresh") or ""})
            if not client.token:
                return False
            self.client = client
            self.server = client.server
            self.username = cfg.get("username") or ""
            self.user_id = cfg.get("user_id") or 0
            self.key_mode = cfg.get("key_mode") or "password"
            self.dek = None            # 口令不在盘上，必须由用户再输一次
            # 令牌以后可能被续期 → 挂上落盘回调（与 login 一致）
            client.on_tokens = self._on_tokens_changed
        return True

    def needs_unlock(self) -> bool:
        """已登录但 DEK 还没解出来（界面要提示输一次口令）。"""
        with self._lock:
            return bool(self.client and self.client.token and self.dek is None)

    def register(self, server: str, username: str, password: str,
                 key_mode: str = "password") -> dict:
        """注册新账号。返回里带**恢复码**，界面必须提示用户抄下来。"""
        server = resolve_server(server)
        device = cs.device_name()
        client = cs.PhixClient(server, e2e=_e2e_on(), pin_dir=_pin_dir())
        info, mat = client.register(username, password, device=device,
                                    material=None, agree=True)
        if not client.token:
            raise cs.PhixError("bad_response",
                               "服务端没有返回访问令牌（服务器版本可能太旧）")
        dek = mat["dek"]
        with self._lock:
            self.client = client
            self.dek = dek
            self.user_id = info["user_id"]
            self.username = info["username"]
            self.server = server
            self.key_mode = key_mode
            self.key_check = mat["key_check"]
            self.recovery_code = mat["recovery_code"]
        client.on_tokens = self._on_tokens_changed
        self._persist_tokens(client)
        save_config(server=server, username=info["username"], user_id=info["user_id"],
                    device=device, key_mode=key_mode)
        self.start_auto_sync()
        st = self.status()
        st["recovery_code"] = mat["recovery_code"]
        return st

    def logout(self, forget_token: bool = True) -> dict:
        """退出登录：注销服务端会话 + 清掉本机这几个令牌键。

        - ``POST /auth/logout`` 会话一注销，**访问令牌立刻失效**（不等它自然过期）。
        - 访问令牌刚过期时会先自动续一次期再注销（否则服务端那个会话会一直活到
          30 天后，在设备列表里显示成"还登着"）。
        - 本机 ``phix:token`` / ``phix:access_token`` / ``phix:refresh_token`` 与旧键
          ``phix:refresh`` **一起删**（少删一串的话，下次续期还能用旧凭据把会话
          "复活"，等于没退）。``settings.yaml`` 里别的东西一个都不动。
        """
        self.stop_auto_sync()
        with self._lock:
            client = self.client
            self.client = None
            self.dek = None
            self.key_check = ""
            self.recovery_code = ""
        # 登出就把进程内缓存的 MK 抹掉 —— 那东西等价于"口令派生出来的钥匙"，
        # 留着没有任何好处（见 phixcrypto 里 MK 缓存那段说明）。
        try:
            pc.clear_mk_cache()
        except AttributeError:      # 老版本客户端没有这个函数，忽略
            pass
        if client is not None:
            client.on_tokens = None       # 别让登出过程中的令牌变化又写回磁盘
            try:
                client.logout()
            except Exception as exc:  # noqa: BLE001  已失效的令牌登出失败无所谓
                _log_warn(f"phix 登出请求没成功（本地令牌照样清）：{exc}")
        if forget_token:
            self._clear_tokens()
        return self.status()

    # ---------------- 换密码 / 切同步口令 ----------------
    def change_password(self, old_password: str, new_password: str) -> dict:
        """换登录密码。

        - ``password`` 模式：DEK 由登录密码包裹 → 用新密码重新包裹一次。
        - ``syncphrase`` 模式：DEK 由**独立同步口令**包裹，与登录密码无关 →
          **一个字节都不动**，只改 Django 那边的登录密码。
          （曾经不分模式一律重包裹，结果把"用同步口令包裹"换成"用新密码包裹"，
           下次拿同步口令就解不开了。）
        """
        with self._lock:
            self._require_unlocked()
            client, dek = self.client, self.dek
            info = client.me()
            proof = pc.prove_dek(dek, self.username, info["key_check"])
            if self.key_mode == "syncphrase":
                # DEK 由独立同步口令包裹，改登录密码**不动它**；
                # 但**登录凭证必须换** —— 服务器那边存的是 AuthHash，
                # 少了这一句，用户改完密码就再也登不进来了（实测踩过）。
                mat = {"kdf_algo": info.get("kdf_algo"),
                       "key_mode": "syncphrase"}
                if pc.uses_auth_hash(info.get("kdf_algo")):
                    mat["auth_hash"] = pc.auth_hash_hex(
                        new_password, info.get("auth_salt"), info.get("kdf_algo"))
            else:
                mat = pc.rewrap(dek, self.username, new_password, "password",
                                key_check_plain=proof,
                                kdf_algo=info.get("kdf_algo"),
                                auth_salt=info.get("auth_salt"),
                                auth_passphrase=new_password)
            client.change_password(old_password, new_password, proof, mat,
                                   old_auth_salt=info.get("auth_salt"),
                                   old_algo=info.get("kdf_algo"))
        save_config(key_mode=self.key_mode)
        return self.status()

    def use_login_password(self, login_password: str, new_login_password: str = "") -> dict:
        """从强模式切回简单模式：DEK 改回由登录密码包裹。"""
        with self._lock:
            self._require_unlocked()
            client, dek = self.client, self.dek
            info = client.me()
            proof = pc.prove_dek(dek, self.username, info["key_check"])
            phrase = new_login_password or login_password
            mat = pc.rewrap(dek, self.username, phrase, "password",
                            key_check_plain=proof,
                            kdf_algo=info.get("kdf_algo"),
                            auth_salt=info.get("auth_salt"),
                            auth_passphrase=phrase)
            client.rewrap(phrase, proof, mat, auth_salt=info.get("auth_salt"),
                          algo=info.get("kdf_algo"))
            self.key_mode = "password"
        save_config(key_mode="password")
        return self.status()

    def set_sync_passphrase(self, login_password: str, sync_passphrase: str) -> dict:
        """切到强模式：DEK 改由**独立同步口令**包裹，服务端从此完全解不开。"""
        with self._lock:
            self._require_unlocked()
            client, dek = self.client, self.dek
            info = client.me()
            proof = pc.prove_dek(dek, self.username, info["key_check"])
            mat = pc.rewrap(dek, self.username, sync_passphrase, "syncphrase",
                            key_check_plain=proof,
                            kdf_algo=info.get("kdf_algo"),
                            auth_salt=info.get("auth_salt"),
                            # 登录口令没变 → AuthHash 也不该变，否则下次登录对不上
                            auth_passphrase=login_password)
            client.rewrap(login_password, proof, mat, auth_salt=info.get("auth_salt"),
                          algo=info.get("kdf_algo"))
            self.key_mode = "syncphrase"
        save_config(key_mode="syncphrase")
        return self.status()

    def devices(self) -> dict:
        """登录设备与会话列表（P3 `/auth/devices`）。

        返回服务端原样给的 ``{sessions, devices, access_ttl, refresh_ttl}``：

        - ``sessions``：**一次登录 = 一个会话**（正式形态）。带 ``id`` / ``device`` /
          ``created_at`` / ``last_seen_at`` / ``revoked`` / ``current`` 等字段；
          注销某个会话 → 那台设备的访问令牌**立刻**失效。
        - ``devices``：兼容期的老式长期令牌（P3 之后新登录基本不再产生），
          列出来只是让界面能显示并清理历史设备。

        服务端**不会**下发 refresh 明文，这里也只是转发它给的东西（别自己拼）。
        """
        with self._lock:
            if not self.client:
                raise cs.PhixError("not_logged_in", "请先登录")
            data = self.client.devices()
        return data if isinstance(data, dict) else {}

    def sessions(self) -> list:
        """只取会话列表（界面用）。"""
        return list(self.devices().get("sessions") or [])

    def revoke_device(self, session_id=None, all_except_current: bool = False) -> dict:
        """注销某一台设备（``session_id``）或"除本机以外的全部"。

        注销成功返回**刷新后的**列表，界面可以直接重绘。
        """
        with self._lock:
            if not self.client:
                raise cs.PhixError("not_logged_in", "请先登录")
            self.client.revoke_device(session_id=session_id,
                                      all_except_current=all_except_current)
        return self.devices()

    # ---------------- 同步 ----------------
    def sync(self, force: bool = False, dry_run: bool = False,
             objects=None, prefer: str = "merge") -> dict:
        """`prefer`：`merge`（默认）/ `local`（本地覆盖云端）/ `remote`（云端覆盖本地）。

        登录时如果本地和云端**两边都有数据**，界面会先问用户选哪个方向，
        再把选择透传到这里。见 `cs.SyncEngine.sync` 与 `sync_probe`。
        """
        with self._lock:
            self._require_unlocked()
            cfg = load_config()
            engine = cs.SyncEngine(
                self.client, self.dek, self.user_id, self.username,
                data_dir=fs.root(), device=cfg.get("device") or cs.device_name(),
                objects=objects or cfg.get("objects") or cs.DEFAULT_OBJECTS,
            )
            report = engine.sync(dry_run=dry_run, force=force, prefer=prefer)
            self.last_report = report
        if report.get("ok"):
            _drop_caches()
            save_config(last_sync_at=fs.now_iso())
        return report

    def sync_probe(self) -> dict:
        """登录后问不问"用哪边覆盖哪边"。需要已登录且已解锁。"""
        with self._lock:
            self._require_unlocked()
            cfg = load_config()
            engine = cs.SyncEngine(
                self.client, self.dek, self.user_id, self.username,
                data_dir=fs.root(), device=cfg.get("device") or cs.device_name(),
                objects=cfg.get("objects") or cs.DEFAULT_OBJECTS,
            )
            return engine.probe()

    def _require_unlocked(self):
        if not self.client:
            raise cs.PhixError("not_logged_in", "还没登录 phix 账号")
        if self.dek is None:
            raise cs.PhixError(
                "locked",
                "已登录但数据是锁着的 —— 这个账号用的是独立同步口令，请输入同步口令解锁")

    # ---------------- 自动同步 ----------------
    def start_auto_sync(self) -> None:
        cfg = load_config()
        if not cfg.get("auto_sync", True):
            return
        minutes = max(2, int(cfg.get("sync_interval_minutes") or DEFAULT_INTERVAL_MINUTES))
        self.stop_auto_sync()
        self._stop = False
        self._schedule(minutes * 60)

    def stop_auto_sync(self) -> None:
        self._stop = True
        with self._lock:
            if self._timer is not None:
                try:
                    self._timer.cancel()
                except Exception:  # noqa: BLE001
                    pass
                self._timer = None

    def _schedule(self, delay: float) -> None:
        if self._stop:
            return
        t = threading.Timer(delay, self._tick)
        t.daemon = True
        with self._lock:
            self._timer = t
        t.start()

    def _tick(self) -> None:
        if self._stop:
            return
        try:
            if self.client and self.dek is not None:
                self.sync()
        except Exception as exc:  # noqa: BLE001  后台同步失败绝不能炸掉程序
            _log_warn(f"phix 自动同步失败：{exc}")
        finally:
            cfg = load_config()
            minutes = max(2, int(cfg.get("sync_interval_minutes")
                                 or DEFAULT_INTERVAL_MINUTES))
            self._schedule(minutes * 60)


def _stored(key: str) -> str:
    """读 ``settings.yaml`` 的 ``secrets_extra[key]``（读不到就是空串，不抛）。"""
    try:
        from . import secrets as _secrets

        return _secrets.get(key) or ""
    except Exception:  # noqa: BLE001
        return ""


def stored_tokens(migrate: bool = True) -> dict:
    """本机落盘的令牌：``{"access", "refresh", "legacy"}``（读不到就是空串）。

    **读兼容**（见文件头契约）：
    - ``access``  ← ``phix:access_token``；缺失时回落到 ``phix:token`` 里
      **像 JWT 的那串**（老实现就是这么存的）。不像 JWT 就说明它是老式长期令牌，
      **不当访问令牌用**。
    - ``refresh`` ← ``phix:refresh_token``；缺失时回落到旧键 ``phix:refresh``。
    - ``legacy``  ← ``phix:token``（老式长期令牌，原样报出来）。

    ``migrate=True`` 时，只要**用上了回落**就顺手把新键补上（**只补写，不删旧键**，
    幂等）。这样 PHL 打开同一份 ``settings.yaml`` 就能看到 PLL 旧格式留下的令牌，
    反过来也一样 —— 这正是这次要修的"两个程序互相看不见对方令牌"。
    """
    legacy = _stored(TOKEN_KEY)
    access = _stored(ACCESS_KEY)
    refresh = _stored(REFRESH_KEY)
    need = []
    if not access and looks_like_jwt(legacy):
        access = legacy
        need.append((ACCESS_KEY, access))
    if not refresh:
        old_refresh = _stored(LEGACY_REFRESH_KEY)
        if old_refresh:
            refresh = old_refresh
            need.append((REFRESH_KEY, refresh))
    if need and migrate:
        try:
            from . import secrets as _secrets

            for key, value in need:
                _secrets.set(key, value)
        except Exception as exc:  # noqa: BLE001  迁不成不算错，旧键照样能用
            _log_warn(f"phix 令牌迁移到新键失败（旧键仍然有效）：{exc}")
    return {"access": access, "refresh": refresh, "legacy": legacy}


def _norm_server(server: str) -> str:
    s = (server or "").strip().rstrip("/")
    if not s:
        raise cs.PhixError("bad_server", "请填写 phix 服务器地址")
    if not s.startswith(("http://", "https://")):
        s = "http://" + s
    if "/api/v1" in s:
        s = s.split("/api/v1", 1)[0]
    return s


#: phix 服务器候选：内网自建（社团机房那台）→ 公网入口 `phix.ing`。
#: 用户 2026-09-21 再次明确：**登录界面不要让用户填服务器地址**（"服务器地址就是 phix.ing"），
#: 与 PH Launcher 的 `PHIX_SERVER_CANDIDATES` 保持一致。
SERVER_CANDIDATES = ("http://192.168.5.41:8931", "https://phix.ing/api/v1")


def resolve_server(server: str = "") -> str:
    """该连哪台服务器 —— 界面不填时的自动选择，顺序：

    ① 调用方显式给的地址；② 配置里记住的上一次成功的地址；
    ③ 挨个 `ping` 候选（内网 → 公网），谁答应就用谁；
    ④ 一个都不通 → 回落到公网入口，让后面真正的请求给出准确错误
       （而不是在这里冒一句"请填写服务器地址"把用户难住）。
    """
    explicit = (server or "").strip()
    if explicit:
        return _norm_server(explicit)
    try:
        remembered = (load_config().get("server") or "").strip()
    except Exception:  # noqa: BLE001  配置读不到不算错
        remembered = ""
    if remembered:
        return _norm_server(remembered)
    for candidate in SERVER_CANDIDATES:
        base = _norm_server(candidate)
        try:
            cs.PhixClient(base, timeout=4, e2e=_e2e_on(), pin_dir=_pin_dir()).ping()
            return base
        except Exception:  # noqa: BLE001  不通就试下一个候选
            continue
    return _norm_server(SERVER_CANDIDATES[-1])


def server_probe() -> dict:
    """给界面用的"自动探测中…"结果：把当前会连的服务器报回去（不抛异常）。"""
    try:
        return {"ok": True, "server": resolve_server()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "server": "", "error": str(getattr(exc, "message", exc))}


def _is_insecure(server: str) -> bool:
    """明文 HTTP 且不是本机 → True。用于界面提示（不阻止，但要显眼地说清楚）。"""
    s = (server or "").strip().lower()
    if not s.startswith("http://"):
        return False
    host = s[len("http://"):].split("/", 1)[0].split(":", 1)[0]
    return host not in ("127.0.0.1", "localhost", "::1", "[::1]")


# ---------------- 忘记密码：用恢复码重设 ----------------
def recover(server: str, username: str, recovery_code: str, new_password: str,
            key_mode: str = "password") -> dict:
    """用**恢复码**重设登录密码（不需要旧密码、不需要登录）。

    流程：向服务端要公开密钥材料 → 本地用恢复码解出 DEK → 用新口令重新包裹 →
    连同"持有 DEK 的证明"一起提交。全程新口令与恢复码都在本地，只上传密文。

    恢复码错了会在**本地**就失败（GCM 认证标签过不去），不会白跑一次网络请求。
    """
    server = resolve_server(server)
    if len(new_password or "") < 6:
        raise cs.PhixError("bad_password", "新密码至少 6 位")
    client = cs.PhixClient(server, e2e=_e2e_on(), pin_dir=_pin_dir())
    info = client.key_material(username)
    algo = info.get("kdf_algo") or pc.KDF_ALGO
    try:
        dek = pc.unwrap_dek_with_recovery(info["recovery_wrap"], recovery_code,
                                          info["recovery_salt"], username, algo)
    except Exception as exc:  # noqa: BLE001
        raise cs.PhixError("bad_recovery_code", "恢复码不对（解不开数据）") from exc
    if not pc.check_dek(dek, username, info["key_check"]):
        raise cs.PhixError("bad_recovery_code", "恢复码不对（自检没通过）")
    proof = pc.prove_dek(dek, username, info["key_check"])
    mat = pc.rewrap(dek, username, new_password, key_mode, key_check_plain=proof,
                    kdf_algo=algo, auth_salt=info.get("auth_salt"),
                    auth_passphrase=new_password)
    client.recover(username, new_password, proof, mat)
    save_config(server=server, username=username, key_mode=key_mode)
    return {"ok": True, "username": username, "server": server,
            "message": "密码已重设，用新密码登录即可（云端数据没动过）"}


def _brief(report) -> dict | None:
    if not report:
        return None
    return {
        "ok": report.get("ok"),
        "skipped": report.get("skipped"),
        "pulled": report.get("pulled") or [],
        "pushed": report.get("pushed") or [],
        "errors": report.get("errors") or [],
        "conflicts": len(report.get("conflicts") or []),
        "finished_at": report.get("finished_at"),
    }


def _drop_caches() -> None:
    """同步改写了本地文件 → 让 bridge 的快照缓存失效，界面立刻能看到新数据。"""
    try:
        from .app import bridge as _bridge

        _bridge._snap_drop("home")
        _bridge._snap_drop_prefix("tt|")
        _bridge._snap_drop("courses", "mail", "gt", "sched")
    except Exception:  # noqa: BLE001
        pass


#: 进程内唯一实例
SESSION = PhixSession()
