# -*- coding: utf-8 -*-
"""设置 →「关于软件」的接线测试。

不联网、不起界面：只读源码，守住三件**容易写错而且后果很误导**的事：

  1. 四样东西都在（当前版本 / 检查更新 / 作者信息 / 访问官网），
     而且作者信息与官网页脚一字不差；
  2. 「检查更新」的三种结果**分明** —— 尤其
     **检查失败绝不能显示成「已是最新版本」**。离线时谎报"已是最新"，
     用户会以为自己没落后，也就不会去更新；
  3. 版本号只有一个来源（后端 `updater.current_version()`），
     前端不许写死一个 `v1.2.x` —— 写死了发版就会漏改、界面永远显示旧版本。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
BRIDGE = (ROOT / "hellopinghe" / "app" / "bridge.py").read_text(encoding="utf-8")
UPDATER = (ROOT / "hellopinghe" / "updater.py").read_text(encoding="utf-8")

#: 与官网页脚（D:\phix\website\index.html 的 .site-footer）逐字一致
FOOTER_LINES = [
    "上海民办平和学校 · 维修社 · 用代码装点校园生活",
    "📧 norine.liu@icloud.com / huaziqian40@gmail.com",
    "📞 18901712280 / 13675821816",
    "💬 微信：Norine2010 / int_32_2147483647",
    "© 2026 phix",
]


class AboutSectionTest(unittest.TestCase):
    """设置页里确实有「关于软件」，四样东西齐全。"""

    def test_section_exists(self):
        self.assertIn("关于软件", HTML)
        self.assertIn('id="about-current-version"', HTML, "要有「当前版本」的位置")
        self.assertIn('id="about-check-update"', HTML, "要有「检查更新」按钮")
        self.assertIn('id="about-visit-site"', HTML, "要有「访问官网」按钮")

    def test_author_info_matches_the_website_footer(self):
        for line in FOOTER_LINES:
            self.assertIn(line, HTML, f"作者信息缺了这句：{line}")
        self.assertIn("B站主页", HTML)

    def test_js_is_actually_wired_up(self):
        """HTML 摆了按钮但没人管它，是这次最容易漏的一半 —— 专门守住。"""
        self.assertRegex(JS, r"function initAbout\(\)", "要有 initAbout")
        self.assertRegex(JS, r"initAbout\(\);", "loadSettings 里要真的调用它")
        self.assertIn('call("app_version")', JS, "版本要问后端拿")
        self.assertIn('call("update_check")', JS, "检查更新要走后端那套")
        self.assertIn('call("open_external"', JS, "外链要用后端的 open_external")
        for anchor in ('about-check-update', 'about-bilibili', 'about-visit-site'):
            self.assertIn(anchor, JS, f"{anchor} 没有绑定")
        # 后端那一侧也得真的存在
        self.assertRegex(BRIDGE, r"def app_version\(")
        self.assertRegex(BRIDGE, r"def update_check\(")
        self.assertRegex(UPDATER, r"def check_for_update_manual\(")
        self.assertRegex(UPDATER, r"def current_version\(")


class UpdateCheckOutcomeTest(unittest.TestCase):
    """三种结果必须分明 —— 尤其是「失败」不能冒充「已是最新」。"""

    def test_up_to_date_branch_says_so(self):
        self.assertIn('status === "up-to-date"', JS)
        self.assertIn('"已是最新版本"', JS)

    def test_new_version_branch_pops_the_card(self):
        self.assertIn('status === "new"', JS)
        self.assertIn("showUpdateCard(", JS, "发现新版本要弹更新卡片")

    def test_failure_is_not_reported_as_up_to_date(self):
        """核心不变式：失败分支里绝不能出现「已是最新版本」。"""
        # 取 initAbout 之后到 manualUpdateCheck 结束的那一段
        start = JS.index("async function manualUpdateCheck(")
        body = JS[start:JS.index("\n}\n", JS.index("btn.textContent = label;", start))]

        self.assertIn("检查失败", body, "失败时要说「检查失败」")
        self.assertIn("is-error", body, "失败要用错误样式")

        # 把「失败」那两条分支单独切出来，确认里面没有「已是最新版本」
        fail_parts = re.findall(r"\} else \{(.*?)\n    \}", body, re.S) + \
                     re.findall(r"catch \(e\) \{(.*?)\n  \}", body, re.S)
        self.assertTrue(fail_parts, "没找到失败分支，测试本身要修")
        for part in fail_parts:
            self.assertNotIn("已是最新版本", part,
                             "失败分支里出现了「已是最新版本」—— 离线会被谎报成已最新")

    def test_offline_and_response_failure_both_fail(self):
        """后端把「有没有更新」和「请求成不成功」分开：拿不到数据一律算失败。"""
        self.assertIn('"failed"', UPDATER)
        self.assertIn("检查失败，请稍后再试", UPDATER)

    def test_button_always_recovers(self):
        """忙态结束后必须恢复可点，否则用户点一次就再也点不动了。"""
        self.assertIn("btn.disabled = false;", JS)
        self.assertIn("finally {", JS)


class VersionSingleSourceTest(unittest.TestCase):
    """版本号不许在前端写死。"""

    def test_frontend_does_not_hardcode_a_version(self):
        # 允许形如 v1.2.5 的**注释**里出现的示例，但代码里不该有
        code = re.sub(r"/\*.*?\*/", " ", JS, flags=re.S)
        code = re.sub(r"(?m)^\s*//.*$", " ", code)
        hits = re.findall(r'["\']v?\d+\.\d+\.\d+["\']', code)
        self.assertEqual(hits, [], f"前端写死了版本号：{hits} —— 应读 updater.current_version()")

    def test_bridge_reads_the_single_source(self):
        m = re.search(r"def app_version\(self\)[^\n]*\n(.*?)\n    def ", BRIDGE, re.S)
        self.assertIsNotNone(m, "找不到 app_version")
        self.assertIn("current_version()", m.group(1),
                      "app_version 必须走 updater.current_version()，别自己拼一个")


if __name__ == "__main__":
    unittest.main()
