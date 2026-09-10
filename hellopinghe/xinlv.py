"""心履 (xin-lv.com) 心情记录 REST 客户端 v1.

协议要点(官方 API 文档 2026-08-18, 已实测探活):
- Bearer token 认证: 登录/注册发放, 长期有效, 多设备并存; 401 → 本地令牌失效
- 心情记录是追加型数据: 客户端生成 uuid v4 → 服务端按 uuid upsert;
  冲突以 updated_at 最新者赢(LWW); 删除 = 置 deleted=true 的墓碑照常同步
- sync/pull 的 ?since= 必须 URL 编码 —— ISO8601 里的 + 不编码会被当空格,
  静默退化为全量拉取(requests 的 params= 会自动编码)
- push 单批 ≤500 条(超出被忽略), pull 单次 ≤2000 条(需循环拉)
- 心情 key 固定 10 个; intensity_level 1-4, intensity_percent 0-100
"""
from __future__ import annotations

import urllib.parse

import requests

from .exceptions import PingheError

BASE = "https://xin-lv.com"
API = BASE + "/api/v1/"
DEVICE = "windows-phl-lite"
USER_AGENT = "PingheLauncherLite/1.1 (xinlv-client)"

#: 心情枚举(与服务端第 2 节一致; 前端图标 ui/xinlv/mood_<key>.png)
MOODS: dict[str, dict] = {
    "happy":    {"label": "开心", "emoji": "😄", "color": "#FFD56B", "valence": 1},
    "calm":     {"label": "平静", "emoji": "🙂", "color": "#9BD1C6", "valence": 1},
    "excited":  {"label": "兴奋", "emoji": "🤩", "color": "#FF9F68", "valence": 1},
    "grateful": {"label": "感恩", "emoji": "🥰", "color": "#F7A6C4", "valence": 1},
    "tired":    {"label": "疲惫", "emoji": "😪", "color": "#A6A6C9", "valence": -1},
    "anxious":  {"label": "焦虑", "emoji": "😟", "color": "#7FA6E8", "valence": -1},
    "sad":      {"label": "难过", "emoji": "😢", "color": "#6D8FB8", "valence": -1},
    "angry":    {"label": "愤怒", "emoji": "😠", "color": "#E8736B", "valence": -1},
    "lonely":   {"label": "孤独", "emoji": "🌧️", "color": "#8E94B8", "valence": -1},
    "numb":     {"label": "麻木", "emoji": "😶", "color": "#B0B0B0", "valence": 0},
}

#: 强度等级文案(1=略微 2=有点 3=相当 4=十分)
INTENSITY_LEVELS: dict[int, str] = {1: "略微", 2: "有点", 3: "相当", 4: "十分"}

#: 徽章档位(连续天数 → 名称)
BADGES: dict[int, str] = {5: "初心", 30: "坚持", 100: "百日", 365: "一年", 1000: "千日"}


class XinlvAuthError(PingheError):
    """401: 令牌无效/已注销 —— 调用方应清除本地令牌并引导重新登录."""


class XinlvClient:
    """无状态 HTTP 客户端; token 由调用方(服务层)保管."""

    def __init__(self, timeout: float = 30.0):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        self.timeout = timeout

    # ------------------------------------------------------------ 基础
    def _req(self, method: str, path: str, token: str | None = None,
             params: dict | None = None, json_body: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            resp = self.session.request(
                method, API + path, headers=headers, params=params,
                json=json_body, timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise PingheError(f"心履服务连接失败: {exc}") from exc
        if resp.status_code == 401:
            raise XinlvAuthError("心履登录已失效, 请重新登录")
        if resp.status_code == 429:
            raise PingheError("操作太频繁, 请几分钟后再试")
        if resp.status_code >= 400:
            try:
                msg = resp.json().get("error", "")
            except Exception:  # noqa: BLE001
                msg = ""
            raise PingheError(f"心履: {msg or f'HTTP {resp.status_code}'}")
        if not resp.content:
            return {}
        return resp.json()

    # ------------------------------------------------------------ 认证
    def ping(self) -> dict:
        return self._req("GET", "ping/")

    def login(self, username: str, password: str) -> dict:
        return self._req("POST", "login/", json_body={
            "username": username, "password": password, "device": DEVICE,
        })

    def register(self, username: str, password: str, agree: bool) -> dict:
        return self._req("POST", "register/", json_body={
            "username": username, "password": password,
            "agree": bool(agree), "device": DEVICE,
        })

    def logout(self, token: str) -> dict:
        return self._req("POST", "logout/", token=token)

    # ------------------------------------------------------------ 同步
    def pull(self, token: str, since: str | None = None) -> dict:
        # requests 会自动 URL 编码 params(since 里的 + → %2B), 满足文档要求
        params = {"since": since} if since else None
        return self._req("GET", "sync/pull/", token=token, params=params)

    def push(self, token: str, entries: list[dict]) -> dict:
        return self._req("POST", "sync/push/", token=token,
                         json_body={"entries": entries})

    # ------------------------------------------------------------ 内容
    def recommend(self, token: str, mood: str) -> dict:
        return self._req("GET", "recommend/", token=token,
                         params={"mood": urllib.parse.quote(mood)})

    def catalog(self, token: str) -> dict:
        return self._req("GET", "catalog/", token=token)

    def profile(self, token: str) -> dict:
        return self._req("GET", "profile/", token=token)


def validate_entry(date_str: str, mood: str, level: int, percent: int) -> None:
    """客户端输入校验(与服务端规则一致, 提前拦截给用户友好报错)."""
    from datetime import date as _date

    try:
        d = _date.fromisoformat(date_str)
    except ValueError as exc:
        raise PingheError("日期格式应为 YYYY-MM-DD") from exc
    if d > _date.today():
        raise PingheError("心情日期不能是未来")
    if mood not in MOODS:
        raise PingheError("未知的心情类型")
    if not 1 <= int(level) <= 4:
        raise PingheError("强度等级应为 1-4")
    if not 0 <= int(percent) <= 100:
        raise PingheError("强度百分比应为 0-100")
