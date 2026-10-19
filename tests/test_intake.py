"""派活入口测试：网页传附件 → 交件，分别走「本机目录」和「网关」两条路。

python3 -m unittest discover -s tests -v
只用标准库；起一个临时 decide 服务和一个假网关，跑完就删。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "test-key-0123456789abcdef0123456789"  # gitleaks:allow (test-only key)
GW_TOKEN = "gw-test-token-0123456789"  # gitleaks:allow (test-only token)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeGateway(BaseHTTPRequestHandler):
    """最小的假网关：收文件、收任务、第二次查询时报完成。"""
    files, jobs, calls = {}, {}, []

    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        if self.headers.get("Authorization") != "Bearer " + GW_TOKEN:
            return self.reply(401, {"error": "bad token"})
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if self.path == "/v1/files":
            fid = "f%d" % (len(self.files) + 1)
            self.files[fid] = (unquote(self.headers.get("X-Filename") or ""), body)
            return self.reply(201, {"file_id": fid, "name": self.files[fid][0], "size": len(body)})
        if self.path == "/v1/jobs":
            j = json.loads(body)
            self.calls.append(j)
            if j["params"].get("text") == "boom":
                return self.reply(400, {"error": "boom refused"})
            jid = "j%d" % (len(self.jobs) + 1)
            self.jobs[jid] = {"n": 0, "req": j}
            return self.reply(202, {"job_id": jid, "status": "queued"})
        self.reply(404, {"error": "no"})

    def do_GET(self):
        jid = self.path.split("?")[0].rsplit("/", 1)[-1]
        job = self.jobs.get(jid)
        if not job:
            return self.reply(404, {"error": "no job"})
        job["n"] += 1
        if job["n"] < 2:
            return self.reply(200, {"job_id": jid, "status": "running"})
        return self.reply(200, {"job_id": jid, "status": "done", "result": {"inbox_id": "inbox-" + jid}})


class IntakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="decide-intake-")
        with open(os.path.join(cls.tmp, "key"), "w") as f:
            f.write(KEY)
        cls.gw = ThreadingHTTPServer(("127.0.0.1", 0), FakeGateway)
        threading.Thread(target=cls.gw.serve_forever, daemon=True).start()
        cls.conf = os.path.join(cls.tmp, "intake.json")
        cls.inbox = os.path.join(cls.tmp, "inbox")
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        env = dict(os.environ, DECIDE_HOME=cls.tmp, DECIDE_PORT=str(cls.port), DECIDE_KEY=KEY,
                   DECIDE_KEY_FILE=os.path.join(cls.tmp, "key"), DECIDE_MEDIA=os.path.join(cls.tmp, "media"),
                   DECIDE_CONFIG=os.path.join(cls.tmp, "none.json"), DECIDE_INTAKE_CONF=cls.conf)
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
        cls.gw.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def set_conf(self, conf):
        with open(self.conf, "w") as f:
            json.dump(conf, f)

    def req(self, path, method="GET", body=None, data=None, headers=None):
        h = {"X-Decide-Key": KEY, **(headers or {})}
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

    def upload(self, name, content):
        return self.req("/api/intake/file", "POST", data=content,
                        headers={"X-Filename": urllib.request.quote(name), "Content-Type": "application/octet-stream"})

    def wait_status(self, iid, want, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            _, j = self.req("/api/intake")
            row = next((r for r in j["recent"] if r["id"] == iid), None)
            if row and row["status"] in want:
                return row
            time.sleep(0.2)
        self.fail(f"{iid} 没等到 {want}")

    def test_1_off_without_config(self):
        if os.path.exists(self.conf):
            os.remove(self.conf)
        code, j = self.req("/api/intake")
        self.assertEqual(code, 200)
        self.assertFalse(j["enabled"])
        self.assertEqual(self.req("/api/intake", "POST", {"text": "x"})[0], 404)
        self.assertFalse(self.req("/api/state")[1]["intake"])

    def test_2_dir_mode(self):
        self.set_conf({"dir": self.inbox})
        self.assertTrue(self.req("/api/state")[1]["intake"])
        code, f = self.upload("../客户 截图.png", b"\x89PNGfake")
        self.assertEqual(code, 201)
        self.assertEqual(f["name"], "客户 截图.png")
        self.assertEqual(self.req("/api/intake", "POST", {})[0], 400)
        self.assertEqual(self.req("/api/intake", "POST", {"files": ["../../etc"]})[0], 400)
        key = "0123456789abcdef"
        code, row = self.req("/api/intake", "POST", {"key": key, "text": "把第三镜调亮", "chat": "片三群", "note": "片三",
                                                     "files": [f["id"]]})
        self.assertEqual(code, 200)
        self.assertEqual(row["id"], key)
        row = self.wait_status(key, {"queued", "failed"})
        self.assertEqual(row["status"], "queued", row)
        d = os.path.join(self.inbox, row["ref"])
        with open(os.path.join(d, "item.json"), encoding="utf-8") as fh:
            item = json.load(fh)
        self.assertEqual((item["via"], item["text"], item["chat"], item["note"]), ("decide", "把第三镜调亮", "片三群", "片三"))
        self.assertEqual(item["files"], ["01-客户 截图.png"])
        with open(os.path.join(d, "files", item["files"][0]), "rb") as fh:
            self.assertEqual(fh.read(), b"\x89PNGfake")
        self.assertFalse([n for n in os.listdir(self.inbox) if n.startswith(".tmp-")])
        # 交出去以后临时附件删掉；同一个 key 重发不重复交件
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "intake-up", f["id"])))
        code, again = self.req("/api/intake", "POST", {"key": key, "text": "把第三镜调亮"})
        self.assertEqual((code, again["status"], again["ref"]), (200, "queued", row["ref"]))
        self.assertEqual(len([n for n in os.listdir(self.inbox) if not n.startswith(".")]), 1)

    def test_3_gateway_mode(self):
        self.set_conf({"gateway": f"http://127.0.0.1:{self.gw.server_address[1]}", "token": GW_TOKEN})
        _, a = self.upload("a.mp4", b"video-bytes")
        _, b = self.upload("说明.pdf", b"pdf-bytes")
        _, row = self.req("/api/intake", "POST", {"text": "客户原话", "files": [a["id"], b["id"]]})
        row = self.wait_status(row["id"], {"queued", "failed"})
        self.assertEqual(row["status"], "queued", row)
        self.assertTrue(row["ref"].startswith("inbox-j"))
        call = FakeGateway.calls[-1]
        self.assertEqual(call["capability"], "route.intake")
        self.assertEqual(call["params"], {"text": "客户原话"})
        self.assertEqual(call["idempotency_key"], "decide-" + row["id"])
        names = sorted(FakeGateway.files[x][0] for x in call["inputs"]["files"])
        self.assertEqual(names, ["a.mp4", "说明.pdf"])
        self.assertEqual([f["name"] for f in row["files"]], ["a.mp4", "说明.pdf"])

    def test_4_gateway_failure_keeps_files_and_retries(self):
        self.set_conf({"gateway": f"http://127.0.0.1:{self.gw.server_address[1]}", "token": GW_TOKEN})
        _, f = self.upload("x.jpg", b"jpg")
        _, row = self.req("/api/intake", "POST", {"text": "boom", "files": [f["id"]]})
        row = self.wait_status(row["id"], {"failed", "queued"})
        self.assertEqual(row["status"], "failed")
        self.assertIn("boom refused", row["error"])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "intake-up", f["id"])))
        # 换回能用的配置后重试
        self.set_conf({"dir": self.inbox})
        code, r2 = self.req(f"/api/intake/{row['id']}/retry", "POST", {})
        self.assertEqual(code, 200)
        r2 = self.wait_status(row["id"], {"queued", "failed"})
        self.assertEqual(r2["status"], "queued", r2)
        self.assertEqual(self.req("/api/intake/zzzz/retry", "POST", {})[0], 404)

    def test_5_bad_token_reports_error(self):
        self.set_conf({"gateway": f"http://127.0.0.1:{self.gw.server_address[1]}", "token": "wrong-token-000000"})
        _, row = self.req("/api/intake", "POST", {"text": "hi"})
        row = self.wait_status(row["id"], {"failed", "queued"})
        self.assertEqual(row["status"], "failed")
        self.assertIn("401", row["error"])
        self.assertNotIn("wrong-token", json.dumps(self.req("/api/intake")[1]))


if __name__ == "__main__":
    unittest.main()
