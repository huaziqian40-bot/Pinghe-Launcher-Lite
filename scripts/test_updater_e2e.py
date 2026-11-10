# -*- coding: utf-8 -*-
"""PHL Lite 更新流程的离线集成自检：后台下载、校验、用户同意后才安装。

旧测试只认裸 exe + copy /Y，会把已停用的不安全安装方式误判为正确；
这里用 NSIS Setup 假载荷验证安装边界，不访问真实网络/用户目录。
"""
import hashlib
import io
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hellopinghe import updater  # noqa: E402


class FakeResp:
    def __init__(self, body: bytes, url: str):
        self.stream = io.BytesIO(body)
        self.headers = {"Content-Length": str(len(body))}
        self.status = 200
        self.url = url

    def read(self, n=-1):
        return self.stream.read(n)

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.stream.close()


def reset():
    with updater._lock:
        updater._generation += 1
        updater._entry = None
        updater._skipped.clear()
        updater._worker = None
        updater._worker_generation = -1
        updater._retry_queued = False
        updater._state.clear()
        updater._state.update(stage="idle", version="", current=updater.APP_VERSION,
                              notes="", percent=None)


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print("  ✓ " + message)


def run_case(bad=False):
    with tempfile.TemporaryDirectory(prefix="phll-update-e2e-") as name:
        reset()
        root = Path(name)
        target = root / "PingheLauncherLite.exe"
        target.write_bytes(b"old application")
        payload = b"fake NSIS Setup bytes" * 500
        url = "https://phix.ing/updates/phl-lite/PingheLauncherLite-9.9.9-Setup.exe"
        entry = {"ok": True, "latest_version": "9.9.9", "release_notes": "测试更新",
                 "url": url, "sha256": "0" * 64 if bad else hashlib.sha256(payload).hexdigest(),
                 "size": len(payload)}
        with mock.patch.object(updater, "_update_dir", return_value=root), \
             mock.patch.object(updater, "_check_remote", return_value=entry), \
             mock.patch.object(updater.urllib.request, "urlopen", return_value=FakeResp(payload, url)), \
             mock.patch.object(updater, "_notify_ui", return_value=True), \
             mock.patch.object(updater, "_notify_ui_progress"):
            result = updater._probe(None, False, True)
            check(result["status"] == "new", "检查发现真正更高版本")
            updater._worker.join(5)
            state = updater.get_update_status()
            if bad:
                check(state["stage"] == "error", "坏包校验失败，不伪装成已下载")
                check(not updater._cache_paths(False)[0].exists(), "坏包不保留缓存")
                with mock.patch.object(sys, "frozen", True, create=True), \
                     mock.patch.object(sys, "executable", str(target)), \
                     mock.patch.object(updater, "_launch_updater") as install:
                    check(updater.apply_update() is False, "坏包绝不启动安装")
                    install.assert_not_called()
            else:
                check(state["stage"] == "downloaded" and state["percent"] == 100,
                      "下载完成并核验 SHA256，才进入待安装")
                with mock.patch.object(sys, "frozen", True, create=True), \
                     mock.patch.object(sys, "executable", str(target)), \
                     mock.patch.object(updater.subprocess, "Popen") as popen:
                    check(updater.apply_update() is True, "用户点击后才启动 NSIS 安装脚本")
                    script = (root / "phl_updater.bat").read_text(encoding="utf-8")
                    check("PID eq %PARENT_PID%" in script and " /S /D=" in script,
                          "安装脚本精确等待本 PID 并调用 NSIS")
                    check("copy /Y" not in script, "不再裸覆盖 MSI/NSIS 管理的 exe")
                    popen.assert_called_once()
        reset()


def main():
    run_case()
    run_case(bad=True)
    reset()
    with mock.patch.object(sys, "frozen", False, create=True), \
         mock.patch.object(updater, "_check_remote") as remote:
        updater.check_for_update_async()
        check(updater.apply_update() is False, "源码运行不自动下载或替换")
        remote.assert_not_called()
    print("更新流程全部通过 ✅")


if __name__ == "__main__":
    main()
