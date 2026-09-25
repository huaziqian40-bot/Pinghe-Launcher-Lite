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


if __name__ == "__main__":
    unittest.main()
