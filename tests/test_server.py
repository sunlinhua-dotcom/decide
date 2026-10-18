"""端到端冒烟测试：起一个临时服务，用 bin/decide 推卡、在 API 上作答、拿回答案，再看网页和媒体能不能出。

python3 -m unittest discover -s tests -v
只用标准库；数据放临时目录，跑完就删。
"""
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
CLI = os.path.join(ROOT, "bin", "decide")
KEY = "test-key-0123456789abcdef0123456789"  # gitleaks:allow (test-only key)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="decide-test-")
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.env = dict(os.environ, DECIDE_HOME=cls.tmp, DECIDE_PORT=str(cls.port), DECIDE_KEY=KEY,
                       DECIDE_PASSWORD="hunter2", DECIDE_BASE=cls.base, DECIDE_CONFIG=os.path.join(cls.tmp, "none.json"),
                       DECIDE_SESSION="test-session", DECIDE_SSH="", DECIDE_GEN_CMD="")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server", "decide_server.py")], env=cls.env,
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

    def req(self, path, method="GET", body=None, key=KEY, headers=None):
        h = dict(headers or {})
        if key:
            h["X-Decide-Key"] = key
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, dict(e.headers), e.read()

    def cli(self, *args):
        return subprocess.run([sys.executable, CLI, *args], env=self.env, capture_output=True, text=True, timeout=60)

    def test_question_round_trip(self):
        r = self.cli("q", "Ship it?", "Yes::ready", "No::needs work", "-p", "smoke", "--rec", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        cid = r.stdout.splitlines()[0].strip()
        code, _, body = self.req(f"/api/cards/{cid}")
        card = json.loads(body)
        self.assertEqual(card["status"], "pending")
        self.assertEqual([o["label"] for o in card["options"]], ["Yes", "No"])
        self.assertTrue(card["options"][0].get("rec"))
        code, _, _ = self.req(f"/api/cards/{cid}/answer", "POST", {"choice": [card["options"][0]["id"]], "note": "go"})
        self.assertEqual(code, 200)
        w = self.cli("wait", cid, "--grace", "0", "--timeout", "10")
        self.assertEqual(w.returncode, 0, w.stderr)
        self.assertIn("Yes", w.stdout)
        self.assertIn("go", w.stdout)

    def test_listen_gets_answers_for_this_session(self):
        cid = self.cli("q", "Pick a color", "Red", "Blue", "-p", "smoke").stdout.split()[0]
        card = json.loads(self.req(f"/api/cards/{cid}")[2])
        self.req(f"/api/cards/{cid}/answer", "POST", {"choice": [card["options"][1]["id"]]})
        r = self.cli("listen", "--grace", "0", "--timeout", "10")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Blue", r.stdout)

    def test_svg_upload_and_media_range(self):
        svg = os.path.join(self.tmp, "a.svg")
        with open(svg, "w") as f:
            f.write('<svg viewBox="0 0 200 100"><script>alert(1)</script><rect width="200" height="100" fill="#ccc"/></svg>')
        r = self.cli("q", "Which layout?", "A", "B", "-p", "smoke", "--svg", svg)
        self.assertEqual(r.returncode, 0, r.stderr)
        card = json.loads(self.req(f"/api/cards/{r.stdout.split()[0]}")[2])
        src = card["media"][0]["src"]
        code, headers, body = self.req(src, key=None, headers={"Cookie": f"decide_k={KEY}", "Range": "bytes=0-9"})
        self.assertEqual(code, 206)
        self.assertEqual(len(body), 10)
        self.assertIn("sandbox", headers.get("Content-Security-Policy", ""))
        full = self.req(src)[2].decode()
        self.assertNotIn("<script", full)

    def test_web_needs_login(self):
        code, _, body = self.req("/", key=None)
        self.assertEqual(code, 200)
        self.assertIn(b'id="pw"', body)  # 登录页
        code, _, body = self.req("/p/smoke", key=None, headers={"Cookie": f"decide_k={KEY}"})
        self.assertIn(b"app.js", body)  # 登录后是网页本体
        code, _, _ = self.req("/icon.svg", key=None)
        self.assertEqual(code, 200)
        code, _, _ = self.req("/api/state", key=None)
        self.assertEqual(code, 401)
        code, _, _ = self.req("/media/../../etc/passwd", key=KEY)
        self.assertIn(code, (400, 404))

    def test_password_login_sets_cookie(self):
        code, headers, _ = self.req("/login", "POST", {"p": "hunter2"}, key=None)
        self.assertEqual(code, 200)
        self.assertIn("decide_k=", headers.get("Set-Cookie", ""))
        self.assertNotIn("Secure", headers.get("Set-Cookie", ""))  # 本机 http 不加 Secure


if __name__ == "__main__":
    unittest.main()
