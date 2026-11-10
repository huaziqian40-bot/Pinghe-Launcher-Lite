# -*- coding: utf-8 -*-
"""数据根解析优先级自检（登记 data_root / 环境变量 / portable.flag）。

    python -X utf8 scripts/test_paths_data_root.py

全部在临时目录上跑；只断言解析结果，不写用户目录。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hellopinghe import install_registry, paths  # noqa: E402


class DataRootTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.ini = self.dir / "PingheSeries" / "installations.ini"
        self.env = mock.patch.dict(os.environ, {install_registry.REGISTRY_ENV: str(self.ini)})
        self.env.start()
        self.addCleanup(self.env.stop)
        paths.reset_cache()

    def tearDown(self):
        paths.reset_cache()

    def _registry_root(self, data_root: Path | None = None) -> Path:
        install_registry.record(install_registry.APP_NAME, str(self.dir / "PH Launcher"),
                                data_root=str(data_root) if data_root else "")
        paths.reset_cache()
        return data_root or (self.dir / "PH Launcher" / "data")

    def test_env_wins_over_registry(self):
        self._registry_root(self.dir / "registered")
        with mock.patch.dict(os.environ, {paths.DATA_DIR_ENV: str(self.dir / "env")}), \
             mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(paths.data_dir(), self.dir / "env")

    def test_registry_data_root_used_for_installed_app(self):
        expected = self._registry_root(self.dir / "shared-data")
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(paths.data_dir(), expected)

    def test_source_run_ignores_registry(self):
        self._registry_root(self.dir / "shared-data")
        with mock.patch.object(sys, "frozen", False, create=True), \
             mock.patch.dict(os.environ, {install_registry.RECORD_ENV: ""}):
            self.assertEqual(paths.data_dir(), paths.LEGACY_HOME_DIR)

    def test_portable_flag_used_without_registry(self):
        exe = self.dir / "app"
        exe.mkdir()
        (exe / paths.FLAG_NAME).write_text("", encoding="utf-8")
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(paths, "exe_dir", return_value=exe):
            self.assertEqual(paths.data_dir(), exe / "data")

    def test_relative_or_file_data_root_ignored(self):
        bogus = self.dir / "not-a-dir"
        bogus.write_text("file", encoding="utf-8")
        install_registry.record(install_registry.APP_NAME, str(self.dir / "PH Launcher"),
                                data_root=str(bogus))
        paths.reset_cache()
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(paths, "exe_dir", return_value=None):
            self.assertEqual(paths.data_dir(), paths.LEGACY_HOME_DIR)

    def test_reset_cache_picks_up_new_root(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            self._registry_root(self.dir / "first")
            self.assertEqual(paths.data_dir(), self.dir / "first")
            install_registry.record(install_registry.APP_NAME, str(self.dir / "PH Launcher"),
                                    data_root=str(self.dir / "second"))
            paths.reset_cache()
            self.assertEqual(paths.data_dir(), self.dir / "second")


if __name__ == "__main__":
    unittest.main(verbosity=2)
