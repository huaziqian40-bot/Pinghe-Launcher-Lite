# -*- coding: utf-8 -*-
"""更新器离线测试：发现更新、单飞预下载、完整性校验、用户同意边界。"""
import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hellopinghe import updater


class Response:
    def __init__(self, body, *, length=None, url="https://phix.ing/update/PingheLauncherLite-9.9.9-Setup.exe", status=200):
        self.stream = io.BytesIO(body)
        self.headers = {} if length is None else {"Content-Length": str(length)}
        self.url = url
        self.status = status

    def read(self, size=-1):
        return self.stream.read(size)

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.stream.close()


class UpdaterTest(unittest.TestCase):
    payload = b"mock NSIS installer bytes"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.dir_patch = mock.patch.object(updater, "_update_dir", return_value=root)
        self.progress_patch = mock.patch.object(updater, "_notify_ui_progress")
        self.notify_patch = mock.patch.object(updater, "_notify_ui", return_value=True)
        self.dir_patch.start()
        self.progress = self.progress_patch.start()
        self.notify = self.notify_patch.start()
        self.addCleanup(self.dir_patch.stop)
        self.addCleanup(self.progress_patch.stop)
        self.addCleanup(self.notify_patch.stop)
        with updater._lock:
            if updater._worker and updater._worker.is_alive():
                updater._generation += 1
                updater._worker.join(timeout=3)
            updater._worker = None
            updater._worker_generation = -1
            updater._retry_queued = False
            updater._entry = None
            updater._skipped.clear()
            updater._state.clear()
            updater._state.update(stage="idle", version="", current=updater.APP_VERSION,
                                  notes="", percent=None)

    def tearDown(self):
        with updater._lock:
            updater._generation += 1
        if updater._worker and updater._worker.is_alive():
            updater._worker.join(timeout=3)

    def release(self, **overrides):
        result = dict(ok=True, latest_version="9.9.9", release_notes="修复窗口问题",
                      url="https://phix.ing/update/PingheLauncherLite-9.9.9-Setup.exe",
                      sha256=hashlib.sha256(self.payload).hexdigest(), size=len(self.payload))
        result.update(overrides)
        return result

    def _probe(self, data=None, *, mac=False, notify=True):
        with mock.patch.object(updater, "_check_remote", return_value=data or self.release()):
            return updater._probe(None, mac, notify)

    def _downloaded(self, data=None, *, length=True, mac=False):
        data = data or self.release()
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(
                self.payload, length=len(self.payload) if length else None, url=data["url"])), \
             mock.patch.object(updater, "_check_remote", return_value=data):
            result = updater._probe(None, mac, True)
            updater._worker.join(timeout=3)
        self.assertEqual(updater.get_update_status()["stage"], "downloaded")
        return result

    def test_version_is_semver(self):
        self.assertEqual(updater.current_version(), updater.APP_VERSION)
        self.assertRegex(updater.APP_VERSION, r"^\d+\.\d+\.\d+$")

    def test_platform_specific_check_url(self):
        with mock.patch.object(updater.sys, "platform", "darwin"):
            self.assertIn("platform=mac", updater._check_url())
        with mock.patch.object(updater.sys, "platform", "win32"):
            self.assertIn("platform=win-nsis", updater._check_url())
            self.assertNotIn("platform=win&", updater._check_url())

    def test_check_remote_parses_json_and_bounded_request(self):
        data = self.release()
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(json.dumps(data).encode())) as open_url:
            self.assertEqual(updater._check_remote()["latest_version"], "9.9.9")
        self.assertIn("product=phl-lite", open_url.call_args.args[0].full_url)

    def test_status_returns_snapshot_not_mutable_global(self):
        state = updater.get_update_status()
        state["stage"] = "forged"
        self.assertEqual(updater.get_update_status()["stage"], "idle")

    def test_new_release_available_event_precedes_download(self):
        seen = []
        def callback(info):
            seen.append((info.copy(), updater.get_update_status()["stage"]))
            return True
        with mock.patch.object(updater, "_notify_ui", side_effect=callback), \
             mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(self.payload, length=len(self.payload))):
            result = self._probe()
            updater._worker.join(3)
        self.assertEqual(seen[0][0]["stage"], "available")
        self.assertEqual(seen[0][1], "available")
        self.assertEqual(result["status"], "new")
        self.assertEqual(updater.get_update_status()["stage"], "downloaded")

    def test_progress_known_length_reports_numbers(self):
        self._downloaded()
        events = [call.args[0] for call in self.progress.call_args_list]
        self.assertIn("downloading", events)
        self.assertIn("downloaded", events)
        self.assertEqual(updater.get_update_status()["percent"], 100)
        self.assertTrue(any(call.kwargs.get("percent") is not None
                            for call in self.progress.call_args_list if call.args[0] == "downloading"))

    def test_progress_unknown_length_remains_null_until_verified(self):
        self._downloaded(length=False)
        self.assertTrue(all(call.kwargs.get("percent") is None
                            for call in self.progress.call_args_list if call.args[0] == "downloading"))
        self.assertEqual(updater.get_update_status()["percent"], 100)

    def test_cache_stored_with_atomic_metadata(self):
        self._downloaded()
        package, meta = updater._cache_paths(False)
        self.assertEqual(package.read_bytes(), self.payload)
        self.assertEqual(json.loads(meta.read_text())["version"], "9.9.9")
        self.assertFalse(list(package.parent.glob("*.part*")))

    def test_same_release_reuses_valid_cache_without_network(self):
        self._downloaded()
        data = self.release()
        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=AssertionError("downloaded twice")):
            updater._download(data | {"version": "9.9.9", "notes": data["release_notes"]}, False, updater._generation)
        self.assertEqual(updater.get_update_status()["stage"], "downloaded")

    def test_manual_check_reports_cached_new_with_stage(self):
        self._downloaded()
        with mock.patch.object(updater, "_check_remote", side_effect=AssertionError("unneeded remote check")):
            result = updater.check_for_update_manual()
        self.assertEqual((result["status"], result["stage"], result["percent"]), ("new", "downloaded", 100))

    def test_manual_network_failure_is_not_up_to_date(self):
        with mock.patch.object(updater, "_check_remote", side_effect=OSError("private network path")):
            result = updater.check_for_update_manual()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["current"], updater.APP_VERSION)
        self.assertNotIn("private network path", result["error"])

    def test_manifest_ok_false_is_failed(self):
        self.assertEqual(self._probe(dict(ok=False))["status"], "failed")

    def test_invalid_latest_version_is_failed(self):
        for latest in ("", "v9.9.9", "bogus", None, "9.9.9-preview", 999):
            with self.subTest(latest=latest):
                self.assertEqual(self._probe(self.release(latest_version=latest))["status"], "failed")

    def test_equal_version_is_up_to_date(self):
        result = self._probe(self.release(latest_version=updater.APP_VERSION))
        self.assertEqual(result["status"], "up-to-date")
        self.notify.assert_not_called()

    def test_older_version_is_up_to_date_not_downgraded(self):
        self.assertEqual(self._probe(self.release(latest_version="0.0.1"))["status"], "up-to-date")
        self.notify.assert_not_called()

    def test_numeric_comparison_treats_9_9_10_newer_than_9_9_9(self):
        self.assertGreater(updater._version("9.9.10"), updater._version("9.9.9"))

    def test_skipped_config_version_not_announced(self):
        cfg = mock.Mock(skipped_update_version="9.9.9")
        with mock.patch.object(updater, "_check_remote", return_value=self.release()):
            self.assertEqual(updater._probe(cfg, False, True)["status"], "skipped")
            self.assertEqual(updater.check_for_update_manual(cfg)["status"], "skipped")
        self.notify.assert_not_called()

    def test_skip_clears_cached_release_and_state(self):
        self._downloaded()
        package, metadata = updater._cache_paths(False)
        updater.skip_update("9.9.9")
        self.assertEqual(updater.get_update_status()["stage"], "idle")
        self.assertFalse(package.exists())
        self.assertFalse(metadata.exists())
        self.assertEqual(self._probe()["status"], "skipped")

    def test_skip_other_release_preserves_current_state(self):
        self._downloaded()
        updater.skip_update("9.9.10")
        self.assertEqual(updater.get_update_status()["stage"], "downloaded")

    def test_skip_stops_inflight_read_without_publishing_download(self):
        entered, release = threading.Event(), threading.Event()
        class Slow(Response):
            def read(self, size=-1):
                entered.set()
                release.wait(3)
                return super().read(size)
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=Slow(self.payload, length=len(self.payload))):
            self._probe()
            self.assertTrue(entered.wait(3))
            updater.skip_update("9.9.9")
            release.set()
            updater._worker.join(3)
        self.assertEqual(updater.get_update_status()["stage"], "idle")
        self.assertFalse(updater._cache_paths(False)[0].exists())

    def test_apply_during_download_does_not_launch_or_refetch(self):
        entered, release = threading.Event(), threading.Event()
        class Slow(Response):
            def read(self, size=-1):
                entered.set()
                release.wait(3)
                return super().read(size)
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=Slow(self.payload, length=len(self.payload))), \
             mock.patch.object(updater, "_launch_updater") as launch, \
             mock.patch.object(sys, "frozen", True, create=True):
            self._probe()
            self.assertTrue(entered.wait(3))
            self.assertFalse(updater.apply_update())
            launch.assert_not_called()
            release.set()
            updater._worker.join(3)

    def test_apply_not_frozen_returns_false(self):
        self._downloaded()
        with mock.patch.object(sys, "frozen", False, create=True), mock.patch.object(updater, "_launch_updater") as launch:
            self.assertFalse(updater.apply_update())
        launch.assert_not_called()

    def test_apply_verified_cache_launches_once_and_never_downloads(self):
        self._downloaded()
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "executable", str(updater._cache_paths(False)[0])), \
             mock.patch.object(updater, "_launch_updater") as launch, \
             mock.patch.object(updater, "_check_remote", side_effect=AssertionError("refetched")):
            self.assertTrue(updater.apply_update())
        launch.assert_called_once()
        self.assertEqual(updater.get_update_status()["stage"], "installing")

    def test_apply_tampered_cache_fails_and_reports_error(self):
        self._downloaded()
        updater._cache_paths(False)[0].write_bytes(b"tampered")
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(updater, "_launch_updater") as launch:
            self.assertFalse(updater.apply_update())
        launch.assert_not_called()
        self.assertEqual(updater.get_update_status()["stage"], "error")

    def test_bad_hash_discards_cache_and_reports_friendly_error(self):
        bad = self.release(sha256="0" * 64)
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(self.payload, length=len(self.payload), url=bad["url"])):
            self._probe(bad)
            updater._worker.join(3)
        status = updater.get_update_status()
        self.assertEqual(status["stage"], "error")
        self.assertEqual(status["message"], updater.DOWNLOAD_ERROR)
        self.assertFalse(updater._cache_paths(False)[0].exists())

    def test_content_length_mismatch_rejected(self):
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(self.payload, length=1)):
            self._probe()
            updater._worker.join(3)
        self.assertEqual(updater.get_update_status()["stage"], "error")

    def test_manifest_size_mismatch_rejected(self):
        data = self.release(size=len(self.payload) + 1)
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(self.payload, url=data["url"])):
            self._probe(data)
            updater._worker.join(3)
        self.assertEqual(updater.get_update_status()["stage"], "error")

    def test_large_download_aborts_before_writing(self):
        with mock.patch.object(updater, "MAX_DOWNLOAD_BYTES", 5), \
             mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(self.payload)), \
             mock.patch.object(updater, "_check_remote", return_value=self.release(size=None)):
            updater._probe(None, False, True)
            updater._worker.join(3)
        self.assertEqual(updater.get_update_status()["stage"], "error")
        self.assertFalse(updater._cache_paths(False)[0].exists())

    def test_unsafe_urls_and_non_nsis_payload_rejected_before_download(self):
        for url in ("http://phix.ing/update/PingheLauncherLite-Setup.exe",
                    "file:///tmp/Setup.exe", "https://phix.ing/update/PingheLauncherLite.exe",
                    "https://user:password@phix.ing/update/Setup.exe"):
            with self.subTest(url=url):
                self.assertEqual(self._probe(self.release(url=url))["status"], "failed")

    def test_installer_filename_must_match_declared_version(self):
        data = self.release(url="https://phix.ing/update/PingheLauncherLite-8.8.8-Setup.exe")
        self.assertEqual(self._probe(data)["status"], "failed")

    def test_redirect_to_http_rejected(self):
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(
                self.payload, length=len(self.payload), url="http://phix.ing/Setup.exe")):
            self._probe()
            updater._worker.join(3)
        self.assertEqual(updater.get_update_status()["stage"], "error")

    def test_redirect_to_another_version_installer_rejected(self):
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(
                self.payload, length=len(self.payload),
                url="https://phix.ing/update/PingheLauncherLite-8.8.8-Setup.exe")):
            self._probe()
            updater._worker.join(3)
        self.assertEqual(updater.get_update_status()["stage"], "error")

    def test_invalid_sha_or_size_rejected(self):
        for value in (self.release(sha256="xyz"), self.release(size=-1), self.release(size=updater.MAX_DOWNLOAD_BYTES + 1)):
            self.assertEqual(self._probe(value)["status"], "failed")

    def test_manual_retry_after_error_reuses_version_and_starts_download(self):
        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=OSError("offline")):
            self._probe()
            updater._worker.join(3)
        self.assertEqual(updater.get_update_status()["stage"], "error")
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(self.payload, length=len(self.payload))):
            result = updater.check_for_update_manual()
            updater._worker.join(3)
        self.assertEqual(result["status"], "new")
        self.assertEqual(updater.get_update_status()["stage"], "downloaded")

    def test_duplicate_manual_check_does_not_spawn_duplicate_download(self):
        entered, release = threading.Event(), threading.Event()
        class Slow(Response):
            def read(self, size=-1):
                entered.set()
                release.wait(3)
                return super().read(size)
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=Slow(self.payload, length=len(self.payload))) as open_url:
            self._probe()
            self.assertTrue(entered.wait(3))
            self.assertEqual(updater.check_for_update_manual()["status"], "new")
            self.assertEqual(updater.check_for_update_manual()["status"], "new")
            release.set()
            updater._worker.join(3)
        self.assertEqual(open_url.call_count, 1)

    def test_windows_script_uses_pid_and_nsis_not_copy(self):
        setup = Path(self.temp.name) / "PingheLauncherLite-9.9.9-Setup.exe"
        setup.write_bytes(self.payload)
        target = Path(self.temp.name) / "PingheLauncherLite.exe"
        with mock.patch.object(updater.subprocess, "Popen") as popen:
            updater._launch_updater(setup, target)
        script = Path(popen.call_args.args[0][2]).read_text(encoding="utf-8")
        self.assertIn("PID eq %PARENT_PID%", script)
        self.assertIn('"' + str(setup) + '" /S /D=', script)
        self.assertIn("if errorlevel 1 exit /b 1", script)
        self.assertNotIn("copy /Y", script)
        self.assertNotIn("del /F", script)

    def test_windows_script_rejects_cmd_metacharacters(self):
        with mock.patch.object(updater.subprocess, "Popen") as popen:
            with self.assertRaises(ValueError):
                updater._launch_updater(Path('C:/downloads/a&b-Setup.exe'), Path('C:/app/PingheLauncherLite.exe'))
        popen.assert_not_called()

    def test_mac_download_cache_and_install_are_separate(self):
        data = self.release(url="https://phix.ing/update/PingheLauncherLite-9.9.9.zip")
        self._downloaded(data, mac=True)
        with mock.patch.object(updater, "_current_app_bundle", return_value="/Applications/Pinghe Launcher Lite.app"), \
             mock.patch.object(updater.subprocess, "run") as extract, \
             mock.patch.object(updater, "_find_app_bundle", return_value="/tmp/Pinghe Launcher Lite.app"), \
             mock.patch.object(updater, "_write_and_launch_swap_script") as swap:
            self.assertTrue(updater.apply_update_mac())
        extract.assert_called_once()
        swap.assert_called_once()
        self.assertEqual(updater.get_update_status()["stage"], "installing")

    def test_mac_tampered_cache_cannot_launch_swap(self):
        data = self.release(url="https://phix.ing/update/PingheLauncherLite-9.9.9.zip")
        self._downloaded(data, mac=True)
        updater._cache_paths(True)[0].write_bytes(b"broken")
        with mock.patch.object(updater, "_write_and_launch_swap_script") as swap:
            self.assertFalse(updater.apply_update_mac())
        swap.assert_not_called()

    def test_mac_swap_script_waits_pid_and_retains_old_on_failure(self):
        with mock.patch.object(updater.subprocess, "Popen"), mock.patch.object(updater, "_mac_notify"):
            updater._write_and_launch_swap_script("/Applications/Lite.app", "/tmp/Lite.app",
                                                  Path(self.temp.name), "9.9.9")
        script = (Path(self.temp.name) / "swap.sh").read_text(encoding="utf-8")
        for token in ('kill -0 "$PID"', 'mv "$TARGET" "$TRASH"', 'mv "$TRASH" "$TARGET"',
                      'ditto "$NEW" "$TARGET"', 'xattr -dr', 'codesign --sign -', '/usr/bin/open'):
            self.assertIn(token, script)

    def test_mac_only_accepts_zip_payload(self):
        self.assertEqual(self._probe(self.release(), mac=True)["status"], "failed")

    def test_startup_async_prefetches_without_installing(self):
        finished = threading.Event()
        def progress(stage, **_):
            if stage == "downloaded":
                finished.set()
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(updater, "_check_remote", return_value=self.release()), \
             mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(self.payload, length=len(self.payload))), \
             mock.patch.object(updater, "_launch_updater") as install, \
             mock.patch.object(updater, "_notify_ui_progress", side_effect=progress):
            updater.check_for_update_async()
            self.assertTrue(finished.wait(3))
        self.assertEqual(updater.get_update_status()["stage"], "downloaded")
        install.assert_not_called()

    def test_source_mode_async_check_does_not_contact_network(self):
        with mock.patch.object(sys, "frozen", False, create=True), mock.patch.object(updater, "_check_remote") as remote:
            updater.check_for_update_async()
            updater.check_for_update_mac_async()
        remote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
