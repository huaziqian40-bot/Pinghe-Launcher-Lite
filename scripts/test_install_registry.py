# -*- coding: utf-8 -*-
"""安装登记（PHL / PHL Lite 共用 INI）自检。

    python -X utf8 scripts/test_install_registry.py

覆盖：读写、默认数据根、保留对方节、卸载删除、损坏文件容错、相对路径拒绝、
原子写、record_self 的 frozen 条件。全部在临时文件上跑，绝不碰真实登记。
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hellopinghe import install_registry, paths  # noqa: E402


class InstallRegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.ini = self.dir / "PingheSeries" / "installations.ini"
        patcher = mock.patch.dict(os.environ, {install_registry.REGISTRY_ENV: str(self.ini)})
        patcher.start()
        self.addCleanup(patcher.stop)
        paths.reset_cache()

    def test_record_and_read_both_programs(self):
        install_registry.record("phl", str(self.dir / "PH Launcher"),
                                data_root=str(self.dir / "PH Launcher" / "data"), version="2.0.13")
        install_registry.record(install_registry.APP_NAME, str(self.dir / "PH Launcher"),
                                data_root=str(self.dir / "PH Launcher" / "data"), version="1.2.9")
        phl = install_registry.other_installation()
        lite = install_registry.self_installation()
        self.assertIsNotNone(phl)
        self.assertEqual(phl["version"], "2.0.13")
        self.assertEqual(phl["data_root"], str(self.dir / "PH Launcher" / "data"))
        self.assertEqual(lite["version"], "1.2.9")
        self.assertIn("[phl]", self.ini.read_text(encoding="utf-8"))
        self.assertIn("[phl-lite]", self.ini.read_text(encoding="utf-8"))

    def test_default_data_root_is_path_data(self):
        install_registry.record("phl", str(self.dir / "PH Launcher"))
        phl = install_registry.other_installation()
        self.assertEqual(phl["data_root"], str(self.dir / "PH Launcher" / "data"))

    def test_update_preserves_other_section(self):
        install_registry.record("phl", str(self.dir / "PH Launcher"), version="2.0.13")
        install_registry.record(install_registry.APP_NAME, str(self.dir / "Elsewhere"), version="1.2.9")
        install_registry.record(install_registry.APP_NAME, str(self.dir / "Elsewhere2"), version="1.3.0")
        self.assertEqual(install_registry.other_installation()["version"], "2.0.13")
        self.assertEqual(install_registry.self_installation()["path"], str(self.dir / "Elsewhere2"))

    def test_remove_only_self(self):
        install_registry.record("phl", str(self.dir / "PH Launcher"))
        install_registry.record(install_registry.APP_NAME, str(self.dir / "PH Launcher"))
        self.assertTrue(install_registry.remove(install_registry.APP_NAME))
        self.assertIsNone(install_registry.self_installation())
        self.assertIsNotNone(install_registry.other_installation())
        self.assertFalse(install_registry.remove(install_registry.APP_NAME))

    def test_installed_zero_means_absent(self):
        self.ini.parent.mkdir(parents=True)
        self.ini.write_text("[phl]\ninstalled=0\npath=C:\\x\n", encoding="utf-8")
        self.assertIsNone(install_registry.installation("phl"))

    def test_missing_and_corrupt_files_read_as_empty(self):
        self.assertIsNone(install_registry.other_installation())
        self.ini.parent.mkdir(parents=True)
        self.ini.write_text("这不是 INI\n[broken\n", encoding="utf-8")
        self.assertEqual(install_registry.read(), {})
        self.assertIsNone(install_registry.other_installation())
        # 损坏后仍能重新写入（不抛错）
        install_registry.record("phl", str(self.dir / "PH Launcher"))
        self.assertIsNotNone(install_registry.other_installation())

    def test_relative_path_rejected(self):
        with self.assertRaises(ValueError):
            install_registry.record("phl", "relative/path")

    def test_atomic_write_leaves_no_temp(self):
        install_registry.record("phl", str(self.dir / "PH Launcher"))
        leftovers = [p.name for p in self.ini.parent.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.ini.stat().st_mode), 0o600)

    def test_record_self_respects_frozen(self):
        with mock.patch.object(sys, "frozen", False, create=True), \
             mock.patch.dict(os.environ, {install_registry.RECORD_ENV: ""}):
            self.assertIsNone(install_registry.record_self(version="1.2.9"))
        with mock.patch.object(sys, "frozen", True, create=True):
            install_registry.record_self(version="1.2.9", data_root=self.dir / "data",
                                         install_dir=self.dir / "PH Launcher")
        self.assertIsNotNone(install_registry.self_installation())
        self.assertEqual(install_registry.self_installation()["data_root"], str(self.dir / "data"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
