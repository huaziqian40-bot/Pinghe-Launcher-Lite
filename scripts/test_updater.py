# -*- coding: utf-8 -*-
"""Pinghe Launcher Lite updater 模块测试（不联网，mock 远程接口）。"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hellopinghe import updater


class UpdateCheckTest(unittest.TestCase):
    def test_current_version_matches_module_constant(self):
        # 别在这里硬编码版本号：发版 bump APP_VERSION 时会漏改而误报失败
        # （1.2.1 → 1.2.2 时就踩过一次）。只断言两者一致 + 是 semver。
        self.assertEqual(updater.current_version(), updater.APP_VERSION)
        self.assertRegex(updater.current_version(), r"^\d+\.\d+\.\d+$")

    def test_check_remote_parses_production_response(self):
        # 模拟生产 /api/v1/update/check 的返回
        payload = {
            "ok": True,
            "product": "phl-lite",
            "platform": "win",
            "latest_version": "1.2.1",
            "url": "https://phix.ing/updates/phl-lite/PingheLauncherLite.exe",
            "sha256": "a" * 64,
            "size": 12345,
        }
        with mock.patch.object(updater.urllib.request, "urlopen") as m:
            m.return_value.__enter__ = mock.Mock(return_value=mock.Mock(
                read=mock.Mock(return_value=json.dumps(payload).encode("utf-8"))))
            data = updater._check_remote()
        self.assertTrue(data["ok"])
        self.assertEqual(data["latest_version"], "1.2.1")

    def test_apply_update_skips_when_not_frozen(self):
        # 源码运行（非 PyInstaller 冻结）不应尝试替换 exe
        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertFalse(updater._apply_update({"url": "x", "sha256": "y"}))

    def test_launch_updater_writes_bat(self):
        # 验证 updater.bat 内容的关键结构
        import tempfile
        from hellopinghe import paths

        with mock.patch.object(updater, "_update_dir") as ud:
            ud.return_value = Path(tempfile.mkdtemp())
            with mock.patch.object(updater.subprocess, "Popen") as popen:
                updater._launch_updater(
                    Path(r"C:\tmp\new.exe"), Path(r"C:\app\PingheLauncherLite.exe"))
                popen.assert_called_once()
                # 第一个参数是 cmd.exe /c bat
                args = popen.call_args[0][0]
                self.assertEqual(args[0], "cmd.exe")
                bat = Path(args[2])
                text = bat.read_text(encoding="utf-8")
                self.assertIn("tasklist /FI", text)
                self.assertIn('copy /Y "C:\\tmp\\new.exe" "C:\\app\\PingheLauncherLite.exe"', text)
                self.assertIn('start "" "C:\\app\\PingheLauncherLite.exe"', text)


class MacAutoReplaceTest(unittest.TestCase):
    """macOS：自动下载 zip 并替换 .app（用户只需右键打开一次），不是让用户自己下载。"""

    def test_current_app_bundle_walks_up_from_executable(self):
        with mock.patch.object(sys, "executable",
                               "/Applications/Pinghe Launcher Lite.app/Contents/MacOS/PingheLauncherLite"):
            got = updater._current_app_bundle()
        # 注意：在 Windows 上跑测试时 Path 会把 /Applications 解释成当前盘下的路径，
        # 所以这里只断言"上溯到了 .app 那一层"，不硬比绝对路径（macOS 上是 /Applications/…）。
        self.assertTrue(got.endswith("Pinghe Launcher Lite.app"), got)
        self.assertNotIn("Contents", got, "应该停在 .app 层，不能停在里面")

    def test_current_app_bundle_empty_when_not_in_app(self):
        with mock.patch.object(sys, "executable", "/usr/local/bin/python3"):
            self.assertEqual(updater._current_app_bundle(), "")

    def test_stage_rejects_non_zip_and_dev_mode(self):
        # 非 zip 载荷（如 dmg）不接受
        self.assertFalse(updater._stage_mac_update({"url": "https://x/y.dmg", "sha256": "a" * 64}, "9.9.9"))
        # 开发模式（不在 .app 内）不自动替换
        with mock.patch.object(sys, "executable", "/usr/local/bin/python3"):
            self.assertFalse(
                updater._stage_mac_update({"url": "https://x/y.zip", "sha256": "a" * 64}, "9.9.9"))

    def test_swap_script_has_required_steps(self):
        import tempfile

        staging = Path(tempfile.mkdtemp())
        with mock.patch.object(updater.subprocess, "Popen") as popen, \
             mock.patch.object(updater, "_mac_notify"):
            updater._write_and_launch_swap_script(
                "/Applications/Pinghe Launcher Lite.app",
                "/tmp/staging/Pinghe Launcher Lite.app",
                staging, "9.9.9")
            self.assertTrue(popen.called, "应启动替换脚本")
        script = (staging / "swap.sh").read_text(encoding="utf-8")
        self.assertIn('kill -0 "$PID"', script)              # 等主进程退出
        self.assertIn(".Trash", script)                      # 旧包进废纸篓
        self.assertIn("xattr -dr com.apple.quarantine", script)  # 清隔离属性
        self.assertIn("codesign --sign - --deep --force", script)  # ad-hoc 重签
        self.assertIn('/usr/bin/open "$TARGET"', script)     # 重启
        self.assertIn("ditto", script)                       # 用 ditto 就位

    def test_sha256_mismatch_discards_staged_update(self):
        """坏包：SHA256 不匹配 → 丢弃，不写替换脚本。"""
        import tempfile
        from hellopinghe import paths

        staging = Path(tempfile.mkdtemp())
        with mock.patch.object(sys, "executable",
                               "/Applications/Pinghe Launcher Lite.app/Contents/MacOS/x"), \
             mock.patch.object(paths, "data_dir", return_value=staging), \
             mock.patch.object(updater, "_download_file") as dl, \
             mock.patch.object(updater, "_write_and_launch_swap_script") as swap:
            # 下载出来的内容与清单哈希不符
            def fake_dl(url, dest, timeout=600.0):
                dest.write_bytes(b"tampered")
            dl.side_effect = fake_dl
            ok = updater._stage_mac_update(
                {"url": "https://x/y.zip", "sha256": "0" * 64}, "9.9.9")
        self.assertFalse(ok, "坏包必须被拒绝")
        self.assertFalse(swap.called, "坏包不应启动替换")


class CardConfirmTest(unittest.TestCase):
    """卡片确认制：检查阶段**只弹卡片**，绝不下载；只有点「更新」才下载替换。

    用户要求（原话）：不要未经用户允许更新；每次检测到有新版本就在进入软件时跳一张卡片，
    写版本号 + 更新内容，下面三个按钮：取消 / 跳过本版本 / 更新。
    """

    @staticmethod
    def _run_now():
        """让 check_for_update_*_async 里的后台线程同步执行，便于断言。"""
        class _Sync:
            def __init__(self, target=None, **kw):
                self._target = target

            def start(self):
                if self._target:
                    self._target()

        return mock.patch.object(updater.threading, "Thread", _Sync)

    def _remote(self, latest="9.9.9", notes="修了几个 bug\n新增了周视图"):
        return {
            "ok": True,
            "latest_version": latest,
            "url": "https://phix.ing/updates/phl-lite/PingheLauncherLite.exe",
            "sha256": "a" * 64,
            "size": 1,
            "release_notes": notes,
        }

    # ---------- Windows ----------
    def test_check_only_notifies_and_never_downloads(self):
        seen = []
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(updater, "_check_remote", return_value=self._remote()), \
             mock.patch.object(updater, "_notify_ui", side_effect=seen.append), \
             mock.patch.object(updater, "_apply_update") as apply_mock, \
             self._run_now():
            updater.check_for_update_async(None)
        self.assertFalse(apply_mock.called, "检查阶段绝对不能下载/替换")
        self.assertEqual(len(seen), 1, "发现新版本应通知界面弹卡片")
        self.assertEqual(seen[0]["version"], "9.9.9")
        self.assertEqual(seen[0]["current"], updater.APP_VERSION)
        self.assertIn("周视图", seen[0]["notes"], "卡片要带上本次更新内容")

    def test_same_version_is_silent(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(updater, "_check_remote",
                               return_value=self._remote(latest=updater.APP_VERSION)), \
             mock.patch.object(updater, "_notify_ui") as notify, \
             self._run_now():
            updater.check_for_update_async(None)
        self.assertFalse(notify.called)

    def test_skipped_version_is_silent_but_newer_one_still_prompts(self):
        class Cfg:
            skipped_update_version = "9.9.9"

        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(updater, "_check_remote", return_value=self._remote()), \
             mock.patch.object(updater, "_notify_ui") as notify, \
             self._run_now():
            updater.check_for_update_async(Cfg())
        self.assertFalse(notify.called, "点过「跳过本版本」后该版本要静默")

        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(updater, "_check_remote",
                               return_value=self._remote(latest="9.9.10")), \
             mock.patch.object(updater, "_notify_ui") as notify2, \
             self._run_now():
            updater.check_for_update_async(Cfg())
        self.assertTrue(notify2.called, "更高的版本仍然要提示")

    def test_not_frozen_is_silent(self):
        with mock.patch.object(sys, "frozen", False, create=True), \
             mock.patch.object(updater, "_check_remote") as remote, \
             mock.patch.object(updater, "_notify_ui") as notify, \
             self._run_now():
            updater.check_for_update_async(None)
        self.assertFalse(remote.called, "源码运行不该联网检查")
        self.assertFalse(notify.called)

    # ---------- macOS ----------
    def test_mac_check_only_notifies_and_never_stages(self):
        seen = []
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(updater, "_check_remote", return_value=self._remote()), \
             mock.patch.object(updater, "_notify_ui", side_effect=seen.append), \
             mock.patch.object(updater, "_stage_mac_update") as stage, \
             self._run_now():
            updater.check_for_update_mac_async(None)
        self.assertFalse(stage.called, "macOS 检查阶段也绝不能下载/替换")
        self.assertEqual([s["version"] for s in seen], ["9.9.9"])

    def test_mac_skipped_version_is_silent(self):
        class Cfg:
            skipped_update_version = "9.9.9"

        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(updater, "_check_remote", return_value=self._remote()), \
             mock.patch.object(updater, "_notify_ui") as notify, \
             self._run_now():
            updater.check_for_update_mac_async(Cfg())
        self.assertFalse(notify.called)

    def test_notify_ui_calls_the_js_hook(self):
        import types
        win = mock.Mock()
        with mock.patch.dict(sys.modules, {"webview": types.SimpleNamespace(windows=[win])}):
            updater._notify_ui({"version": "9.9.9", "current": "1.2.2", "notes": "x"})
        js = win.evaluate_js.call_args[0][0]
        self.assertIn("__updateAvailable", js)
        self.assertIn("9.9.9", js)

    def test_notify_ui_retries_until_the_window_is_ready(self):
        """检查线程在 webview.start() 之前就起来了：窗口还没建好时推送会失败，
        必须重试 —— 否则网速快的时候卡片永远不弹（而且失败是静默的）。"""
        import types
        win = mock.Mock()
        # 前 3 次窗口没准备好（抛异常），第 4 次才送到
        win.evaluate_js.side_effect = [
            RuntimeError("window not ready"), RuntimeError("window not ready"),
            None, True,
        ]
        with mock.patch.dict(sys.modules, {"webview": types.SimpleNamespace(windows=[win])}), \
             mock.patch.object(updater.time, "sleep") as sleep:
            ok = updater._notify_ui({"version": "9.9.9", "current": "1.2.2"}, attempts=10)
        self.assertTrue(ok, "重试之后应该送达")
        self.assertEqual(win.evaluate_js.call_count, 4)
        self.assertEqual(sleep.call_count, 3, "每次失败之间要等一下再试")

    def test_notify_ui_gives_up_quietly(self):
        import types
        win = mock.Mock()
        win.evaluate_js.return_value = False     # 页面一直没加载出来
        with mock.patch.dict(sys.modules, {"webview": types.SimpleNamespace(windows=[])}), \
             mock.patch.object(updater.time, "sleep"):
            ok = updater._notify_ui({"version": "9.9.9"}, attempts=3)
        self.assertFalse(ok, "没有窗口时安静放弃，不能抛异常影响启动")

    def test_js_hooks_return_true_so_the_backend_knows_it_arrived(self):
        js = (Path(__file__).resolve().parent.parent / "ui" / "app.js").read_text(encoding="utf-8")
        at = js.index("window.__updateAvailable = (info) => {")
        block = js[at:at + 300]
        self.assertIn("return true;", block,
                      "回调必须返回 true，后端才能判断推送是否真的送到")
        at2 = js.index("window.__updateProgress = (p) => {")
        self.assertIn("return true;", js[at2:at2 + 300])

    def test_apply_update_is_the_only_download_path(self):
        """apply_update 是「用户点了更新」之后才走的路：校验失败要报错且不替换。"""
        errs = []
        with mock.patch.object(updater, "_check_remote", return_value=self._remote()), \
             mock.patch.object(updater, "_apply_update", return_value=False) as apply_mock, \
             mock.patch.object(updater, "_notify_ui_progress",
                               side_effect=lambda stage, **kw: errs.append(stage)):
            ok = updater.apply_update()
        self.assertTrue(apply_mock.called, "点更新后才允许下载")
        self.assertFalse(ok)
        self.assertIn("error", errs, "失败要回给卡片一个提示")


class UpdateCardMarkupTest(unittest.TestCase):
    """界面侧：卡片必须有版本号、更新内容、三个按钮，并接上后端回调。"""

    def setUp(self):
        self.ui = Path(__file__).resolve().parent.parent / "ui"

    def test_index_has_card_with_three_buttons(self):
        html = (self.ui / "index.html").read_text(encoding="utf-8")
        for i in ("update-modal", "update-version", "update-current",
                  "update-notes", "update-cancel", "update-skip", "update-now"):
            self.assertIn(f'id="{i}"', html, f"缺 #{i}")
        self.assertIn("跳过本版本", html)
        self.assertIn("取消", html)
        self.assertIn(">更新<", html)

    def test_app_js_binds_card_and_exposes_hooks(self):
        js = (self.ui / "app.js").read_text(encoding="utf-8")
        self.assertIn("window.__updateAvailable", js)
        self.assertIn("window.__updateProgress", js)
        self.assertIn('call("update_choice"', js)
        for c in ("cancel", "skip", "update"):
            self.assertIn(f'chooseUpdate("{c}")', js)
        self.assertIn("bindUpdateCard();", js, "启动时要绑定卡片按钮")

    def test_card_waits_for_the_splash_screen(self):
        """开机画面（#splash）z-index 比弹层高；检查比它先结束的话卡片会被挡住，
        所以必须先等开机画面收起来再弹 —— 否则用户根本看不到卡片。"""
        js = (self.ui / "app.js").read_text(encoding="utf-8")
        self.assertIn("function showUpdateCardAfterSplash", js)
        self.assertIn('window.__updateAvailable = (info) => {', js)
        self.assertIn("showUpdateCardAfterSplash(info)", js,
                      "__updateAvailable 必须走「等开机画面」这条路")
        self.assertIn('document.getElementById("splash")', js)
        css = (self.ui / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".splash-screen{position:fixed;inset:0;z-index:200", css,
                      "前提确认：开机画面确实盖在弹层（z-index:50）上面")

    def test_styles_have_update_card_rules(self):
        css = (self.ui / "styles.css").read_text(encoding="utf-8")
        for cls in (".update-card", ".update-version", ".update-notes", ".update-hint"):
            self.assertIn(cls, css)


class BridgeChoiceTest(unittest.TestCase):
    """bridge.update_choice：cancel/skip 不下载，skip 记住版本，只有 update 才下载。"""

    def _api(self):
        from hellopinghe.app.bridge import Api
        api = Api.__new__(Api)          # 不跑 __init__（避免建服务/读磁盘）
        api.cfg = mock.Mock()
        api.cfg.skipped_update_version = ""
        return api

    def test_cancel_downloads_nothing(self):
        api = self._api()
        with mock.patch.object(updater, "apply_update") as apply_mock:
            r = api.update_choice("cancel", "9.9.9")
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "cancel")
        self.assertFalse(apply_mock.called)
        self.assertEqual(api.cfg.skipped_update_version, "", "取消不该写任何记录")
        self.assertFalse(api.cfg.save.called)

    def test_skip_remembers_version_without_downloading(self):
        api = self._api()
        with mock.patch.object(updater, "apply_update") as apply_mock:
            r = api.update_choice("skip", "9.9.9")
        self.assertEqual(r["action"], "skip")
        self.assertFalse(apply_mock.called, "跳过绝不能下载")
        self.assertEqual(api.cfg.skipped_update_version, "9.9.9")
        self.assertTrue(api.cfg.save.called, "跳过要落盘，下次启动才静默")

    def test_update_spawns_download_thread(self):
        api = self._api()
        started = []

        class _Sync:
            def __init__(self, target=None, **kw):
                self._target = target

            def start(self):
                started.append(True)
                # 不真的执行 target（那会退出进程），只验证它被启动了

        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(updater, "apply_update") as apply_mock, \
             mock.patch.object(sys.modules["hellopinghe.app.bridge"], "threading") as th:
            th.Thread = _Sync
            r = api.update_choice("update", "9.9.9")
        self.assertEqual(r["action"], "update")
        self.assertTrue(started, "点「更新」才启动下载线程")

    def test_unknown_choice_is_rejected(self):
        api = self._api()
        r = api.update_choice("whatever", "9.9.9")
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
