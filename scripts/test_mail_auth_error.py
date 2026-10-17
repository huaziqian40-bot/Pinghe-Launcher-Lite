# -*- coding: utf-8 -*-
"""邮箱认证/网络错误的分类自检（不联网、不读也不写任何真实凭据）。

用户 2026-10-17 反馈：有同学用 Mac 版收不到平和邮箱的邮件，界面提示"检查 IMAP 设置"，
但设置其实都对。根因之一在错误分类：网易企业邮在密码/授权码不对时返回的是
**ERR.LOGIN.PASSERR**（2026-10-17 拿假账号实测确认），而这个码以前没有单独处理，
于是掉进"通用检查 ①②③"那张设置清单里 —— 学生照着查设置，却查不出任何问题。

这里守住三件事：
  1. ERR.LOGIN.PASSERR 要说清"授权码可能已失效、去重新生成"；
  2. 网络 / DNS / 证书问题要单独说，**不能再叫用户去检查邮箱设置**；
  3. 已经说清原因时，不再附加那张"通用检查"清单（否则又把话绕回设置上）。

    python scripts/test_mail_auth_error.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hellopinghe.config import Config  # noqa: E402
from hellopinghe.app.services import MailService  # noqa: E402


class ImapErrorClassifyTest(unittest.TestCase):
    def setUp(self):
        self.mail = MailService(Config())

    def message(self, text: str) -> str:
        return str(self.mail._imap_error(Exception(text)))

    # ---- 认证类 ---------------------------------------------------------
    def test_passerr_points_at_expired_authcode(self):
        m = self.message("b'ERR.LOGIN.PASSERR'")
        self.assertIn("授权码", m)
        self.assertIn("失效", m)
        self.assertIn("重新生成", m)
        # 已经知道原因，就别再让人去翻那张通用设置清单
        self.assertNotIn("通用检查", m)

    def test_reqcode_and_illegal_email_still_explained(self):
        self.assertIn("REQCODE", self.message("ERR.LOGIN.REQCODE"))
        self.assertIn("未开通 IMAP", self.message("ERR.ILLEGAL.EMAIL"))

    # ---- 网络类：绝不能说是设置问题 --------------------------------------
    def test_network_problems_are_never_reported_as_settings(self):
        cases = [
            ("socket.timeout: timed out", ("网络", "代理")),
            ("[Errno 8] nodename nor servname provided, or not known", ("上网", "网络")),
            ("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", ("系统时间", "代理")),
            ("Connection refused", ("网络", "代理")),
        ]
        for raw, hints in cases:
            m = self.message(raw)
            self.assertNotIn("通用检查", m, raw)
            self.assertTrue(any(h in m for h in hints), f"{raw} → {m}")

    def test_unknown_error_still_keeps_the_generic_checklist(self):
        # 真不知道原因时，那张清单还是有用的
        m = self.message("some brand new server error")
        self.assertIn("通用检查", m)


if __name__ == "__main__":
    unittest.main()
