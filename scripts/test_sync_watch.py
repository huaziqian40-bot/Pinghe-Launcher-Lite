# -*- coding: utf-8 -*-
"""长轮询（sync/watch）在客户端这一侧的测试。

不联网：用一个假的传输层，检查
  1. `PhixClient.watch` 真的打到 `/sync/watch`，光标被正确编码；
  2. 超时给得比服务端挂起时间长（否则会在服务端刚要返回时被本地掐断）；
  3. `start_auto_sync` 起的是**长轮询线程**，不再只是"每 N 分钟一轮"；
  4. 长轮询失败会退避重试，而且**绝不让异常炸掉后台线程**；
  5. 服务端说变了 → 立刻跑一轮同步。
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hellopinghe import cloudsync as cs          # noqa: E402
from hellopinghe import phixsession as ps        # noqa: E402


class WatchRequestTest(unittest.TestCase):
    """`PhixClient.watch` 的请求形状与超时。"""

    def _client(self):
        return cs.PhixClient("https://phix.ing", token="tok")

    def test_calls_the_watch_route(self):
        c = self._client()
        seen = {}

        def fake(method, path, payload=None, token=None, timeout=None):
            seen.update(method=method, path=path, timeout=timeout)
            return {"ok": True, "changed": False, "cursor": "1:"}

        with mock.patch.object(cs.PhixClient, "_req", side_effect=fake):
            c.watch("")
        self.assertEqual(seen["method"], "GET")
        self.assertEqual(seen["path"], "/sync/watch", "没带光标时不该拼 query")

    def test_encodes_the_cursor(self):
        c = self._client()
        seen = {}

        def fake(method, path, payload=None, token=None, timeout=None):
            seen.update(path=path)
            return {}

        with mock.patch.object(cs.PhixClient, "_req", side_effect=fake):
            c.watch("3:2026-09-29T12:00:00+00:00")
        self.assertTrue(seen["path"].startswith("/sync/watch?cursor="))
        # 冒号、加号这些必须转义，否则 query 会被解析错
        self.assertNotIn(" ", seen["path"])
        self.assertIn("%3A", seen["path"], "冒号要编码")

    def test_client_timeout_is_longer_than_server_hold(self):
        """客户端超时必须**比服务端挂起时间长** —— 反过来会变成一连串假失败。"""
        self.assertGreater(cs.WATCH_TIMEOUT_SECONDS, cs.WATCH_HOLD_SECONDS)
        c = self._client()
        seen = {}

        def fake(method, path, payload=None, token=None, timeout=None):
            seen["timeout"] = timeout
            return {}

        with mock.patch.object(cs.PhixClient, "_req", side_effect=fake):
            c.watch("x")
        self.assertEqual(seen["timeout"], cs.WATCH_TIMEOUT_SECONDS)


class WatchLoopTest(unittest.TestCase):
    """自动同步现在应该是"长轮询 + 兜底间隔"，而不是只靠那个间隔。"""

    def _session(self, watch_result):
        s = ps.PhixSession()
        s._stop = False
        s.dek = b"0" * 32
        client = mock.Mock()
        client.watch.side_effect = watch_result
        s.client = client
        return s, client

    def test_watch_loop_syncs_when_the_server_says_changed(self):
        s, client = self._session([
            {"changed": True, "cursor": "1:a"},
            StopIteration,
        ])
        calls = []
        with mock.patch.object(ps.PhixSession, "sync",
                               side_effect=lambda *a, **k: calls.append(1)):
            s.start_auto_sync()
            for _ in range(100):
                if calls:
                    break
                time.sleep(0.02)
            s.stop_auto_sync()
        self.assertTrue(calls, "服务端说变了就应该立刻同步一次")
        self.assertEqual(s._watch_cursor, "1:a", "光标要记下来给下一次用")

    def test_watch_loop_survives_failures_and_retries(self):
        """长轮询失败绝不能炸掉后台线程；退避之后要接着挂。"""
        s, client = self._session([
            RuntimeError("离线"),
            {"changed": False, "cursor": "2:b"},
            StopIteration,
        ])
        with mock.patch.object(ps, "time") as fake_time:
            fake_time.sleep = lambda *_a, **_k: None
            s.start_auto_sync()
            for _ in range(200):
                if client.watch.call_count >= 3:
                    break
                time.sleep(0.01)
            s.stop_auto_sync()
        self.assertGreaterEqual(client.watch.call_count, 2, "失败之后要接着重试")

    def test_busy_server_retry_after_is_honoured(self):
        """服务端名额满了会**立刻**打回一个 changed=false + retry_after。

        这跟"正常挂满 25 秒超时"长得一模一样（都是 changed=False），意思却相反：
        超时该**立刻**再挂上（长轮询接力），被打回则必须先等一下 ——
        否则客户端就变成拿热循环打服务器，比不挂还糟。所以单独守住这条。
        """
        s, client = self._session([
            {"changed": False, "cursor": "3:c", "retry_after": 0.5},
            StopIteration,
        ])
        slept = []
        with mock.patch.object(ps, "time") as fake_time:
            fake_time.time = time.time
            fake_time.sleep = lambda d=0, *a, **k: slept.append(d)
            s.start_auto_sync()
            for _ in range(200):
                if client.watch.call_count >= 2:
                    break
                time.sleep(0.01)
            s.stop_auto_sync()
        self.assertIn(0.5, slept, "被服务端打回时必须按 retry_after 等一下再挂")
        self.assertEqual(s._watch_cursor, "3:c", "被打回也不能丢掉光标")

    def test_timeout_without_retry_after_reissues_immediately(self):
        """真超时（没有 retry_after）就该立刻再挂 —— 这才是长轮询接力，
        也是这个机制能一直挂着的原因。别把它和"被打回"搞混。"""
        s, client = self._session([
            {"changed": False, "cursor": "4:d"},
            {"changed": False, "cursor": "4:d"},
            StopIteration,
        ])
        slept = []
        with mock.patch.object(ps, "time") as fake_time:
            fake_time.time = time.time
            fake_time.sleep = lambda d=0, *a, **k: slept.append(d)
            s.start_auto_sync()
            for _ in range(200):
                if client.watch.call_count >= 3:
                    break
                time.sleep(0.01)
            s.stop_auto_sync()
        self.assertEqual(slept, [], "超时后不该睡，要马上接着挂")

    def test_start_auto_sync_reports_watching(self):
        s, client = self._session([{"changed": False, "cursor": ""}])
        with mock.patch.object(ps, "load_config", return_value={"auto_sync": True}):
            s.start_auto_sync()
            time.sleep(0.1)
            status = s.status()
            s.stop_auto_sync()
        self.assertIn("watching", status, "状态里要能看出走的是长轮询")
        self.assertEqual(status["watch_cursor"], "")

    def test_auto_sync_can_be_turned_off(self):
        s, client = self._session([{"changed": False, "cursor": ""}])
        with mock.patch.object(ps, "load_config", return_value={"auto_sync": False}):
            s.start_auto_sync()
        self.assertIsNone(s._watch_thread, "关掉自动同步就不该起长轮询线程")
        self.assertFalse(client.watch.called)


if __name__ == "__main__":
    unittest.main()
