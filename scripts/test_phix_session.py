"""phix 会话层「重启后恢复登录态 / 解锁」的离线单测（不联网、不碰真实数据）。

背景（用户实测报的现象）：设置页明明登着 phix 账号，重启程序后**又显示登录框**。
根因是 `logged_in` 只看内存里的 client，而程序从来没有用盘上的令牌把它接回来。
这里把 `restore()` / `unlock()` 的行为钉死。

跑法：`python scripts/test_phix_session.py`（退出码 0 = 全过）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hellopinghe import phixcrypto as pc          # noqa: E402
from hellopinghe import phixsession as px         # noqa: E402

FAILED: list[str] = []
NETWORK_CALLS: list[str] = []


def check(label, got, want) -> None:
    if got != want:
        FAILED.append(f"{label}\n    got : {got!r}\n    want: {want!r}")


def ok(label, cond: bool) -> None:
    if not cond:
        FAILED.append(f"{label}（期望为真）")


def boom(*_a, **_k):
    NETWORK_CALLS.append("request")
    raise AssertionError("restore()/status() 不应该发网络请求")


class FakeClient:
    """只实现会话层用到的那几样；任何"请求"都记下来并炸掉。"""

    def __init__(self, server="http://127.0.0.1:8931", e2e=True, pin_dir=None):
        self.server = server
        self.token = None
        self.legacy_token = None
        self.refresh_token = None
        self.encrypted = False
        self.on_tokens = None
        self._me: dict = {}

    def absorb_tokens(self, info):
        if info.get("access_token"):
            self.token = info["access_token"]
        elif info.get("token"):
            self.token = info["token"]
        if info.get("refresh_token"):
            self.refresh_token = info["refresh_token"]

    # 网络方法
    def me(self, *_a, **_k):
        if not self._me:
            boom()
        return self._me


def session_with(cfg: dict, tokens: dict, client: FakeClient | None = None) -> px.PhixSession:
    px.load_config = lambda *a, **k: dict(cfg)
    px.stored_tokens = lambda migrate=True: dict(tokens)
    s = px.PhixSession()
    s._stop = True                      # 别让解锁去起自动同步定时器
    px.PhixSession.start_auto_sync = lambda self: None
    if client is not None:
        s.client = client
    return s


def fake_factory(mat: dict, username: str):
    def make(server, e2e=True, pin_dir=None):
        c = FakeClient(server)
        c._me = {"key_wrap": mat["key_wrap"], "kdf_salt": mat["kdf_salt"],
                 "username": username, "key_check": mat["key_check"],
                 "key_mode": mat["key_mode"], "user_id": 7}
        return c
    return make


class TokClient(FakeClient):
    """只用来"接着令牌"，从不联网。"""

    def __init__(self, server="http://127.0.0.1:8931", e2e=True, pin_dir=None):
        super().__init__(server)


px.cs.PhixClient = TokClient

CFG = {"server": "http://127.0.0.1:8931", "username": "tester", "user_id": 7,
       "key_mode": "password", "device": "测试机"}
TOK = {"access": "ACCESS-1", "refresh": "REFRESH-1", "legacy": ""}

# ---------------------------------------------------------------- 1 restore()
# 1a 有令牌 → 恢复成功，且**不发网络请求**
NETWORK_CALLS.clear()
s = session_with(CFG, TOK)
ok("有令牌时 restore() 应当成功", s.restore() is True)
ok("restore() 不该发网络请求", not NETWORK_CALLS)
st = s.status()
check("恢复后 logged_in", st["logged_in"], True)
check("恢复后 unlocked（密钥不在盘上，必须为 False）", st["unlocked"], False)
check("恢复后 username 取自配置", st["username"], "tester")
check("恢复后 user_id 取自配置", st["user_id"], 7)
check("恢复后 server", st["server"], "http://127.0.0.1:8931")
check("恢复后 has_access_token（界面据此判断「登着」）", st["has_access_token"], True)

# 1b 盘上没令牌 → 不恢复
s = session_with(CFG, {"access": "", "refresh": "", "legacy": ""})
check("没令牌时 restore() 应当失败", s.restore(), False)
check("没令牌时仍是未登录", s.status()["logged_in"], False)

# 1c 只有老式令牌也能恢复（服务器关掉兼容令牌前的历史数据）
s = session_with(CFG, {"access": "", "refresh": "", "legacy": "LEGACY-1"})
ok("只有老式令牌时也能恢复", s.restore() is True)

# 1d 没配服务器 → 不恢复（不能瞎猜地址）
s = session_with({"username": "tester"}, TOK)
check("没配服务器时不恢复", s.restore(), False)

# 1e 已经登着（内存里有 client）→ 幂等，直接 True
s = session_with(CFG, TOK, client=FakeClient())
check("已登录时 restore() 幂等", s.restore(), True)

# 1f 登录后重启：再恢复一次不该把已有的 dek 抹掉
s = session_with(CFG, TOK)
s.restore()
s.dek = b"\x01" * 32
s.restore()
ok("重复 restore() 不该清掉已解出的 DEK", s.dek == b"\x01" * 32)

# ---------------------------------------------------------------- 2 unlock()
mat = pc.new_material("tester", "secret123")          # 真做一遍 scrypt + AES-GCM
px.cs.PhixClient = fake_factory(mat, "tester")

# 2a 简单模式：输登录密码 → 解开
CLIENT = FakeClient()
s = session_with(CFG, TOK, client=CLIENT)
CLIENT._me = fake_factory(mat, "tester")("http://127.0.0.1:8931")._me
CLIENT._me["key_mode"] = "password"
st = s.unlock("secret123")
check("用登录密码解锁 → unlocked", st["unlocked"], True)
ok("解出来的正是那把 DEK", s.dek == mat["dek"])

# 2b 密码错 → 人话报错，且不会把 dek 留着
s = session_with(CFG, TOK, client=CLIENT)
try:
    s.unlock("wrong-password")
    FAILED.append("错误口令应当抛错")
except px.cs.PhixError as exc:
    check("错误口令的错误码", exc.code, "bad_passphrase")
    ok("错误口令的提示是人话", "不对" in exc.message and "InvalidTag" not in exc.message)
ok("错误口令不该留下 DEK", s.dek is None)

# 2c 强模式：同一个 DEK 换成"独立同步口令"包裹 → 用同步口令能解开
strong = pc.rewrap(mat["dek"], "tester", "sync-pass-9", key_mode="syncphrase",
                   recovery_code=mat["recovery_code"],
                   key_check_plain=None, kdf_algo=mat["kdf_algo"])
CFG_STRONG = dict(CFG, key_mode="syncphrase")
CLIENT2 = FakeClient()
CLIENT2._me = {"key_wrap": strong["key_wrap"], "kdf_salt": strong["kdf_salt"],
               "username": "tester", "key_check": strong["key_check"],
               "key_mode": "syncphrase", "user_id": 7}
s = session_with(CFG_STRONG, TOK, client=CLIENT2)
st = s.unlock("sync-pass-9")
check("强模式下用同步口令解锁", st["unlocked"], True)
ok("强模式解出的仍是同一把 DEK", s.dek == mat["dek"])

# 2d **模式记混了**也要能开：账号是强模式，但用户输的是登录密码
#    （独立同步口令没设过时两者其实一致；这里构造"两种解释都试一次"的路径）
CLIENT3 = FakeClient()
CLIENT3._me = dict(CLIENT2._me)
CLIENT3._me["key_mode"] = "password"      # 配置说是简单模式，实际材料是同步口令包的
s = session_with(dict(CFG, key_mode="password"), TOK, client=CLIENT3)
st = s.unlock("sync-pass-9")
check("模式记混时仍然能解锁（两种解释都试）", st["unlocked"], True)

# 2e 没登录就解锁 → 明确报"请先登录"
s = session_with(CFG, TOK)
try:
    s.unlock("whatever")
    FAILED.append("未登录时 unlock() 应当抛错")
except px.cs.PhixError as exc:
    check("未登录解锁的错误码", exc.code, "not_logged_in")

# ---------------------------------------------------------------- 3 status()
s = session_with(CFG, TOK)
s.restore()
st = s.status()
check("恢复后 needs_unlock", s.needs_unlock(), True)
ok("status() 不该发网络请求", not NETWORK_CALLS)
check("未解锁时加密标志为 False", st["encrypted"], False)

# ---------------------------------------------------------------- 结果
if FAILED:
    print(f"✗ {len(FAILED)} 项失败：\n")
    for item in FAILED:
        print("  · " + item)
    sys.exit(1)
print("✓ 全部通过（重启恢复登录态 / 两种口令解锁 / 不联网）")
