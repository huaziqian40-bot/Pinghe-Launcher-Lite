"""Hello Pinghe! Launcher 全局异常."""
from __future__ import annotations


class PingheError(Exception):
    """基础异常."""


class LoginError(PingheError):
    """登录失败(凭据错误/锁定/SSO 学校/结构变更)."""


class LoginRequiredError(PingheError):
    """会话过期,需要重新登录."""
