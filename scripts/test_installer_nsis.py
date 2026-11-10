# -*- coding: utf-8 -*-
"""NSIS 发布入口的静态守护；不会运行真实安装器/卸载器。"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_windows

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "installer" / "PingheLauncherLite.nsi"


class NsisSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8")
        # 去注释再守护指令，避免中文安全说明掩盖危险操作。
        cls.commands = "\n".join(line.split(";", 1)[0].strip()
                                 for line in cls.source.splitlines())

    def test_standard_assisted_per_user_directory(self):
        self.assertRegex(self.commands, r'!include\s+"MUI2\.nsh"')
        self.assertIn("!insertmacro MUI_PAGE_DIRECTORY", self.commands)
        self.assertIn("RequestExecutionLevel user", self.commands)
        self.assertIn('InstallDir "$LOCALAPPDATA\\Programs\\PH Launcher"', self.commands)
        self.assertIn("MUI_DIRECTORYPAGE_TEXT_TOP", self.commands)
        self.assertIn("共用", self.source)
        self.assertNotIn("Program Files\\HPHL", self.commands)

    def test_shared_data_and_writability_checked_even_for_silent_install(self):
        self.assertIn('StrCpy $0 "$INSTDIR\\data"', self.commands)
        self.assertIn('CreateDirectory "$0"', self.commands, "数据根可能是 PHL 登记的目录")
        self.assertIn('FileOpen $0 "$INSTDIR\\portable.flag" a', self.commands)
        self.assertIn('GetTempFileName $0 "$INSTDIR"', self.commands)
        self.assertIn("Call VerifyInstallDirectory", self.commands)
        self.assertRegex(self.commands, r"(?m)^\s*IfErrors invalid_directory$")

    def test_old_msi_data_warning_never_migrates_implicitly(self):
        self.assertIn('$LOCALAPPDATA\\Programs\\PingheLauncherLite\\data\\settings.yaml', self.commands)
        self.assertIn('$INSTDIR\\data\\*.*', self.commands)
        self.assertIn('旧 Lite 数据不会自动合并', self.source)
        self.assertIn('切勿删除旧数据', self.source)
        # 允许"用户明确点是"才复制旧数据；绝不能无提示自动迁移，也不能覆盖已有 data。
        self.assertIn('MessageBox MB_YESNO', self.commands)
        self.assertIn('/SD IDNO', self.commands, "静默安装必须默认不复制旧数据")
        self.assertNotRegex(self.commands, r'(?im)^\s*(?:ExecWait|Rename|CopyFiles)\b')
        copy_at = self.commands.find("nsExec::ExecToLog 'xcopy")
        ask_at = self.commands.find("MessageBox MB_YESNO")
        self.assertGreater(copy_at, ask_at, "复制必须排在用户确认之后")
        self.assertIn('IfFileExists "$INSTDIR\\data\\settings.yaml" install_files',
                      self.commands, "目标已有数据时直接跳过复制")
        self.assertIn('IfFileExists "$INSTDIR\\data\\*.*" install_files',
                      self.commands, "目标 data 非空时不得复制")
        self.assertIn('StrCmp "$INSTDIR" "$LOCALAPPDATA\\Programs\\PingheLauncherLite" same_as_legacy',
                      self.commands, "自动更新（/D=旧 MSI 目录）时不能自称有两份数据")
        self.assertIn("same_as_legacy:", self.commands)

    def test_phl_reverse_uninstall_guard_when_reference_exists(self):
        # PHL 仓库只读且可不存在；存在时确认其 customRemoveFiles 不会递归删整个目录
        # 或 Lite exe。注意它可以删除自己的 resources/locales，Lite 不应碰这些文件。
        reference = Path(os.environ.get('PHL_INSTALLER_NSH',
                                        r'D:\phl-dev\PH-Launcher\build\installer.nsh'))
        if not reference.is_file():
            self.skipTest('PHL NSIS 参考脚本不在本机，Lite 测试仍可独立运行')
        text = reference.read_text(encoding='utf-8')
        match = re.search(r'!macro customRemoveFiles\s*(.*?)\s*!macroend', text, re.DOTALL)
        self.assertIsNotNone(match, 'PHL 必须接管 electron-builder 默认递归删除行为')
        body = match.group(1)
        self.assertNotRegex(body, r'(?im)^\s*RMDir\s+/r\s+"?\$INSTDIR(?:"|\s|$)')
        self.assertNotRegex(body, r'(?im)^\s*Delete\s+.*PingheLauncherLite\.exe')
        self.assertNotRegex(body, r'(?im)^\s*RMDir\s+/r\s+.*\\data\b')

    def test_install_registry_default_directory_and_data_root(self):
        # 安装登记：两个程序通过固定 INI 互相发现，安装器默认装到对方目录。
        self.assertIn("Function .onInit", self.commands)
        self.assertIn("$LOCALAPPDATA\\PingheSeries\\installations.ini", self.commands)
        self.assertIn('ReadINIStr $1 "$0" "phl" "path"', self.commands)
        self.assertIn('ReadINIStr $2 "$0" "phl" "data_root"', self.commands)
        self.assertIn('StrCpy $INSTDIR "$1"', self.commands)
        self.assertIn("StrCpy $DetectedPhlDataRoot", self.commands)
        self.assertIn("EnumRegKey", self.commands, "旧版 PHL 没写登记时要查注册表")
        self.assertIn('StrCmp $5 "PH Launcher"', self.commands)
        self.assertIn('"phl-lite" "path" "$INSTDIR"', self.commands)
        self.assertIn('"phl-lite" "data_root" "$0"', self.commands)
        self.assertIn('DeleteINISec "$LOCALAPPDATA\\PingheSeries\\installations.ini" "phl-lite"',
                      self.commands, "卸载只删自己的条目")
        self.assertNotIn('DeleteINISec "$LOCALAPPDATA\\PingheSeries\\installations.ini" "phl"',
                         self.commands, "绝不能删 PHL 的登记条目")

    def test_uninstall_keeps_shared_data_and_other_program(self):
        uninstall = self.commands.split('Section "Uninstall"', 1)[1]
        for own_file in ('${LITE_EXE}', '${LITE_UNINSTALLER}', '${LITE_SHORTCUT}'):
            self.assertIn(own_file, uninstall)
        self.assertIn('DeleteRegKey HKCU "${LITE_UNINSTALL_KEY}"', uninstall)
        self.assertIn('StrCmp "$0" "$INSTDIR" 0 skip_global_entries', uninstall)
        # 严禁递归删除、宽泛通配符、清除共用目录和 PHL 文件。
        self.assertNotRegex(uninstall, r"(?im)^\s*RMDir\s+/r\b")
        self.assertNotRegex(uninstall, r"(?im)^\s*RMDir\s+\"?\$INSTDIR\b")
        self.assertNotRegex(uninstall, r"(?im)^\s*Delete\s+.*(?:\*|portable\.flag|\\data|\\locales|\\resources|PH Launcher\.exe)")
        self.assertNotIn('DeleteRegKey HKLM', uninstall)

    def test_version_sources_must_match(self):
        version = build_windows.checked_version()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "installer").mkdir()
            (root / "hellopinghe").mkdir()
            (root / "installer" / "installer.py").write_text('VERSION = "9.1.0"\n', encoding="utf-8")
            (root / "hellopinghe" / "updater.py").write_text('APP_VERSION = "9.1.1"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "不一致"):
                build_windows.checked_version(root)

    def test_candidate_never_overwrites_official_package(self):
        with tempfile.TemporaryDirectory() as folder:
            dist = Path(folder)
            self.assertTrue(build_windows.output_path("1.2.7", dist).stem.endswith("candidate"))
            official = dist / "PingheLauncherLite-2.0.0-Setup.exe"
            official.touch()
            self.assertEqual(build_windows.output_path("2.0.0", dist).stem,
                             "PingheLauncherLite-2.0.0-Setup-candidate")
            self.assertEqual(build_windows.output_path("2.0.1", dist).name,
                             "PingheLauncherLite-2.0.1-Setup.exe")

    def test_pyinstaller_spec_is_one_file(self):
        spec = (ROOT / "PingheLauncherLite.spec").read_text(encoding="utf-8")
        self.assertIn("exe = EXE(", spec)
        self.assertIn("a.binaries", spec)
        self.assertIn("a.datas", spec)
        self.assertNotRegex(spec, r"(?m)^\s*coll\s*=\s*COLLECT\(")

    @unittest.skipUnless(os.environ.get("PHLL_TEST_NSIS_DRY_RUN") == "1",
                         "设置 PHLL_TEST_NSIS_DRY_RUN=1 后可选运行构建入口 dry-run")
    def test_optional_build_dry_run(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_windows.py"), "--dry-run"],
                                cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"Setup(?:-candidate(?:-\d+)?)?\.exe")


if __name__ == "__main__":
    unittest.main()
