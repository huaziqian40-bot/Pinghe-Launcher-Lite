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
    def test_current_version_is_1_2_1(self):
        self.assertEqual(updater.current_version(), "1.2.1")

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


if __name__ == "__main__":
    unittest.main()
