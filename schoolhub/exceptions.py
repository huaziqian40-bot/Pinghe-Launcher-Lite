"""SchoolHub 全局异常."""
from __future__ import annotations


class SchoolHubError(Exception):
    """基础异常."""


class LoginError(SchoolHubError):
    """登录失败(凭据错误/锁定/SSO 学校/结构变更)."""


class LoginRequiredError(SchoolHubError):
    """会话过期,需要重新登录."""
