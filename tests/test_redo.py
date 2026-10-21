"""已定历史返修测试（10-21）：定过的卡点「返修」→ 记成「返修 + 意见」、原答案留底、重新交给推卡的会话。

python3 -m unittest discover -s tests -v
只用标准库；起一个临时 decide 服务，跑完就删。
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "test-key-0123456789abcdef0123456789"  # gitleaks:allow (test-only key)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load_cli():
    loader = importlib.machinery.SourceFileLoader("decide_cli", os.path.join(ROOT, "bin", "decide"))
    spec = importlib.util.spec_from_loader("decide_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class RedoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="decide-redo-")
        with open(os.path.join(cls.tmp, "key"), "w") as f:
            f.write(KEY)
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        env = dict(os.environ, DECIDE_HOME=cls.tmp, DECIDE_PORT=str(cls.port), DECIDE_KEY=KEY,
                   DECIDE_KEY_FILE=os.path.join(cls.tmp, "key"), DECIDE_MEDIA=os.path.join(cls.tmp, "media"),
                   DECIDE_CONFIG=os.path.join(cls.tmp, "none.json"), DECIDE_INTAKE_CONF=os.path.join(cls.tmp, "no-intake.json"))
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server", "decide_server.py")], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.base + "/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("服务没起来")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, path, method="GET", body=None):
        h = {"X-Decide-Key": KEY}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def card(self, **kw):
        body = {"project": "rv", "kind": "choose", "title": "挑一版", "session": "sess-rv",
                "options": [{"id": "a", "label": "A 版"}, {"id": "b", "label": "B 版"}], **kw}
        code, j = self.req("/api/cards", "POST", body)
        self.assertEqual(code, 201, j)
        return j["id"]

    def listen(self):
        return self.req("/api/listen?session=sess-rv&timeout=1")[1]["cards"]

    def test_redo_answered_card(self):
        cid = self.card()
        self.assertEqual(self.req(f"/api/cards/{cid}/redo", "POST", {"note": "x"})[0], 409)  # 还在待定
        code, _ = self.req(f"/api/cards/{cid}/answer", "POST", {"choice": ["a"], "note": "字小一点"})
        self.assertEqual(code, 200)
        self.assertEqual([c["id"] for c in self.listen()], [cid])
        self.req("/api/ack", "POST", {"ids": [cid]})
        self.assertEqual(self.listen(), [])
        self.assertEqual(self.req(f"/api/cards/{cid}/redo", "POST", {"note": "   "})[0], 400)  # 意见是空的
        code, j = self.req(f"/api/cards/{cid}/redo", "POST", {"note": "背景再暗一点", "device": "Mac"})
        self.assertEqual(code, 200, j)
        a = j["answer"]
        self.assertEqual(j["status"], "answered")
        self.assertFalse(j["acked"])
        self.assertTrue(a["redo"])
        self.assertEqual((a["labels"], a["note"], a["device"]), (["返修"], "背景再暗一点", "Mac"))
        self.assertEqual((a["prev"]["choice"], a["prev"]["labels"], a["prev"]["note"]), (["a"], ["A 版"], "字小一点"))
        got = self.listen()
        self.assertEqual([c["id"] for c in got], [cid])  # 重新交给推卡的会话
        line = load_cli().fmt_card(got[0])
        self.assertIn("返修", line)
        self.assertIn("背景再暗一点", line)
        self.assertIn("原来选的：A 版", line)
        self.req("/api/ack", "POST", {"ids": [cid]})
        # 连着返修两次：prev 只留上一次，不套娃
        code, j = self.req(f"/api/cards/{cid}/redo", "POST", {"note": "还是太亮"})
        self.assertEqual(code, 200)
        self.assertEqual(j["answer"]["prev"]["note"], "背景再暗一点")
        self.assertNotIn("prev", j["answer"]["prev"])

    def test_redo_cancelled_card(self):
        cid = self.card()
        self.assertEqual(self.req(f"/api/cards/{cid}/cancel", "POST", {})[0], 200)
        code, j = self.req(f"/api/cards/{cid}/redo", "POST", {"note": "还是要改"})
        self.assertEqual(code, 200, j)
        self.assertEqual(j["answer"]["prev_status"], "cancelled")
        self.assertIsNone(j["answer"]["prev"])
        self.assertIn("原来选的：已撤销", load_cli().fmt_card(j))
        self.req("/api/ack", "POST", {"ids": [cid]})

    def test_redo_perm_card_refused(self):
        code, j = self.req("/api/cards", "POST", {"project": "rv", "kind": "perm", "title": "允许运行 ls？", "session": "sess-rv"})
        if code != 201:
            self.skipTest(f"建不了授权卡：{j}")
        cid = j["id"]
        _, c = self.req(f"/api/cards/{cid}")
        self.assertEqual(self.req(f"/api/cards/{cid}/answer", "POST", {"choice": [c["options"][0]["id"]], "ack": True})[0], 200)
        self.assertEqual(self.req(f"/api/cards/{cid}/redo", "POST", {"note": "x"})[0], 409)


if __name__ == "__main__":
    unittest.main()
