# -*- coding: utf-8 -*-
"""PHL Lite updater 端到端集成测试。

验证完整流程（不依赖真实网络，用 FakeResp 模拟下载）：
  1. 好包：下载 → SHA256 匹配 → 生成 updater.bat → 启动替换 → 返回 True
  2. 坏包：下载 → SHA256 不匹配 → 删除文件 → 不启动替换 → 返回 False（回滚兜底）
"""
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hellopinghe import updater


class FakeResp:
    """模拟 urllib 响应对象（支持 with 与 read(n)）。"""

    def __init__(self, data: bytes):
        self._d = data

    def read(self, n=-1):
        if n is None or n < 0:
            d, self._d = self._d, b""
            return d
        chunk, self._d = self._d[:n], self._d[n:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _prep(prefix: str):
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    upd_dir = tmp / ".updates"
    upd_dir.mkdir()
    target = tmp / "app.exe"
    target.write_bytes(b"old-version")
    return tmp, upd_dir, target


def test_good_package_applies():
    tmp, upd_dir, target = _prep("upd-good-")
    payload = os.urandom(8192)
    sha = hashlib.sha256(payload).hexdigest()

    with mock.patch.object(sys, "frozen", True, create=True), \
         mock.patch.object(sys, "executable", str(target)), \
         mock.patch.object(updater, "_update_dir", return_value=upd_dir), \
         mock.patch.object(updater.urllib.request, "urlopen", return_value=FakeResp(payload)), \
         mock.patch.object(updater.subprocess, "Popen") as popen:
        ok = updater._apply_update({"url": "https://x/fake.exe", "sha256": sha})

    assert ok is True, f"好包应替换成功，得到 {ok}"
    bat = upd_dir / "phl_updater.bat"
    assert bat.exists(), "updater.bat 应生成"
    text = bat.read_text(encoding="utf-8")
    assert "tasklist /FI" in text, "bat 应轮询进程退出"
    assert "copy /Y" in text, "bat 应覆盖 exe"
    assert 'start ""' in text, "bat 应重启新版本"
    assert popen.called, "应启动 updater"
    print("  ✅ 好包：下载 → SHA256 匹配 → 生成替换脚本 → 启动替换")


def test_bad_package_rejected():
    tmp, upd_dir, target = _prep("upd-bad-")
    payload = os.urandom(8192)
    wrong_sha = "0" * 64

    with mock.patch.object(sys, "frozen", True, create=True), \
         mock.patch.object(sys, "executable", str(target)), \
         mock.patch.object(updater, "_update_dir", return_value=upd_dir), \
         mock.patch.object(updater.urllib.request, "urlopen", return_value=FakeResp(payload)), \
         mock.patch.object(updater.subprocess, "Popen") as popen:
        ok = updater._apply_update({"url": "https://x/fake.exe", "sha256": wrong_sha})

    assert ok is False, "坏包必须被拒绝"
    assert not (upd_dir / "phl_lite_new.exe").exists(), "坏包下载文件应被删除"
    assert not (upd_dir / "phl_updater.bat").exists(), "坏包不应生成替换脚本"
    assert not popen.called, "坏包不应启动 updater"
    print("  ✅ 坏包：SHA256 不匹配 → 丢弃 → 不替换（回滚兜底生效）")


def test_not_frozen_skips():
    """源码运行时（非 PyInstaller 冻结）不应尝试替换。"""
    with mock.patch.object(sys, "frozen", False, create=True):
        ok = updater._apply_update({"url": "https://x/y.exe", "sha256": "a" * 64})
    assert ok is False, "非冻结环境应跳过"
    print("  ✅ 非冻结环境：跳过自动替换（开发时不误动）")


if __name__ == "__main__":
    test_good_package_applies()
    test_bad_package_rejected()
    test_not_frozen_skips()
    print("\n全部通过 ✅")
