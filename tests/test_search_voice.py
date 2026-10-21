"""已定搜索、按住说话、「在对话里说」提醒的测试（10-21）。

python3 -m unittest discover -s tests -v
只用标准库；起一个临时 decide 服务，再起两个假服务（语音识别、对话模型）顶替真接口，跑完就删。
"""
import io
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
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "test-key-0123456789abcdef0123456789"  # gitleaks:allow (test-only key)
FAKE = {"llm": [], "asr": []}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/asr":
            FAKE["asr"].append((self.headers.get("Content-Type"), raw))
            out = {"text": "language Chinese<asr_text>背景再暗一点"}
        else:
            j = json.loads(raw)
            FAKE["llm"].append((self.headers.get("Authorization"), j))
            seq = int(j["messages"][1]["content"].rsplit("：", 1)[1]) if "编号" in j["messages"][1]["content"] else 0
            out = {"choices": [{"message": {"content": json.dumps({"seqs": [seq, 99999]})}}]}
        b = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def silence_wav(sec=1.0):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * sec))
    return buf.getvalue()


class Base(unittest.TestCase):
    extra_env = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="decide-sv-")
        with open(os.path.join(cls.tmp, "key"), "w") as f:
            f.write(KEY)
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        env = dict(os.environ, DECIDE_HOME=cls.tmp, DECIDE_PORT=str(cls.port), DECIDE_KEY=KEY,
                   DECIDE_KEY_FILE=os.path.join(cls.tmp, "key"), DECIDE_MEDIA=os.path.join(cls.tmp, "media"),
                   DECIDE_CONFIG=os.path.join(cls.tmp, "none.json"), DECIDE_INTAKE_CONF=os.path.join(cls.tmp, "no-intake.json"))
        for k in ("DECIDE_ASR_URL", "DECIDE_LLM_URL", "DECIDE_LLM_MODEL", "DECIDE_LLM_KEY_FILE"):
            env.pop(k, None)
        env.update(cls.env_for(cls.tmp))
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
    def env_for(cls, tmp):
        return {}

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, path, method="GET", body=None, raw=None, ctype=None):
        h = {"X-Decide-Key": KEY}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        if raw is not None:
            data = raw
            h["Content-Type"] = ctype or "application/octet-stream"
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def card(self, title, choice="a", note="", project="sv", session="sess-sv"):
        body = {"project": project, "kind": "choose", "title": title, "session": session,
                "options": [{"id": "a", "label": "通过"}, {"id": "b", "label": "返修", "style": "bad"}]}
        code, j = self.req("/api/cards", "POST", body)
        self.assertEqual(code, 201, j)
        if choice:
            code, a = self.req(f"/api/cards/{j['id']}/answer", "POST", {"choice": [choice], "note": note})
            self.assertEqual(code, 200, a)
        return j


class OffTest(Base):
    """语音、按意思找都没配：前端藏起话筒，接口回 404；字面搜索照常能用。"""

    def test_state_flags(self):
        _, st = self.req("/api/state")
        self.assertFalse(st["voice"])
        self.assertFalse(st["search_ai"])
        self.assertEqual(st["chat_wait"], 900)

    def test_voice_off(self):
        self.assertEqual(self.req("/api/voice", "POST", raw=silence_wav(), ctype="audio/wav")[0], 404)

    def test_search_lexical(self):
        a = self.card("顶栏改成项目色块", note="不要紫色和绿色")
        b = self.card("片二 N24 毛巾架电线走向", choice="b", note="插头要一模一样")
        self.card("这张还在待定", choice=None)
        code, j = self.req("/api/search?q=" + urllib.request.quote("毛巾架"))
        self.assertEqual(code, 200)
        self.assertFalse(j["ai"])
        self.assertEqual([c["id"] for c in j["cards"]][:1], [b["id"]])
        self.assertTrue(j["cards"][0]["hits"])
        # 意见里的词也搜得到；待定卡不出现
        j = self.req("/api/search?q=" + urllib.request.quote("紫色"))[1]
        self.assertEqual([c["id"] for c in j["cards"]], [a["id"]])
        j = self.req("/api/search?q=" + urllib.request.quote("待定"))[1]
        self.assertEqual(j["cards"], [])
        # 只命中一半字也找得到（「顶栏颜色」→「顶栏改成项目色块」）
        j = self.req("/api/search?q=" + urllib.request.quote("顶栏颜色"))[1]
        self.assertIn(a["id"], [c["id"] for c in j["cards"]])
        # 编号直达
        j = self.req(f"/api/search?q={b['seq']}")[1]
        self.assertEqual(j["cards"][0]["id"], b["id"])
        # 空查询
        self.assertEqual(self.req("/api/search?q=")[1]["cards"], [])
        # 没配模型：按意思找回 404
        self.assertEqual(self.req("/api/search?q=x&ai=1")[0], 404)

    def test_chat_wait(self):
        c = self.card("在对话里说的卡", choice=None)
        d = self.card("在对话里说的卡二", choice=None)
        code, j = self.req("/api/chat", "POST", {"ids": [c["id"], d["id"]], "device": "Mac"})
        self.assertEqual(code, 200, j)
        a = self.req(f"/api/cards/{c['id']}")[1]["answer"]
        self.assertTrue(a["chat"] and a["wait"])
        self.assertNotIn("heard", a)
        # 顺延：记下下次提醒时间
        code, j = self.req("/api/chatwait", "POST", {"ids": [c["id"]], "act": "snooze"})
        self.assertEqual((code, j["n"]), (200, 1))
        a = self.req(f"/api/cards/{c['id']}")[1]["answer"]
        self.assertGreater(a["remind_at"], time.time() + 800)
        self.assertEqual(self.req("/api/chatwait", "POST", {"ids": [c["id"]], "act": "x"})[0], 400)
        # 用户在那个会话里发了消息：两张都停表；别的会话不受影响
        e = self.card("别的会话的卡", choice=None, session="sess-other")
        self.req("/api/chat", "POST", {"ids": [e["id"]]})
        self.assertEqual(self.req("/api/heard", "POST", {"session": "sess-sv"})[1]["heard"], 2)
        self.assertTrue(self.req(f"/api/cards/{d['id']}")[1]["answer"]["heard"])
        self.assertNotIn("heard", self.req(f"/api/cards/{e['id']}")[1]["answer"])
        self.assertEqual(self.req("/api/heard", "POST", {"session": "sess-sv"})[1]["heard"], 0)
        # 板上「我去说」
        self.assertEqual(self.req("/api/chatwait", "POST", {"ids": [e["id"]], "act": "done"})[1]["n"], 1)
        self.assertTrue(self.req(f"/api/cards/{e['id']}")[1]["answer"]["heard"])
        # Claude 在对话里清卡（ack）：用户已经在对话里说了，不计时
        f = self.card("Claude 清的卡", choice=None)
        self.req("/api/chat", "POST", {"ids": [f["id"]], "ack": True})
        a = self.req(f"/api/cards/{f['id']}")[1]["answer"]
        self.assertNotIn("wait", a)
        self.assertTrue(a["heard"])

    def test_chat_said(self):
        # 卡片弹出后用户已经在那个会话里发过消息：点「在对话里说」直接算说过，不计时
        g = self.card("弹出后已经说了", choice=None, session="sess-said")
        self.req("/api/heard", "POST", {"session": "sess-said"})
        code, j = self.req("/api/chat", "POST", {"ids": [g["id"]], "device": "Mac"})
        self.assertEqual((code, j["said"]), (200, [g["seq"]]))
        a = self.req(f"/api/cards/{g['id']}")[1]["answer"]
        self.assertNotIn("wait", a)
        self.assertTrue(a["heard"] and a["said"])
        # 消息在卡片弹出之前：照旧计时，交给 Claude 看对话决定要不要停表
        h = self.card("弹出前说过", choice=None, session="sess-said")
        code, j = self.req("/api/chat", "POST", {"ids": [h["id"]]})
        self.assertEqual(j["said"], [])
        a = self.req(f"/api/cards/{h['id']}")[1]["answer"]
        self.assertTrue(a["wait"])
        self.assertNotIn("said", a)
        # 从没发过消息的会话：计时
        k = self.card("没说过", choice=None, session="sess-never")
        self.assertEqual(self.req("/api/chat", "POST", {"ids": [k["id"]]})[1]["said"], [])


class OnTest(Base):
    """语音、按意思找都接上假服务：验证请求格式和结果处理。"""

    @classmethod
    def env_for(cls, tmp):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        fp = os.path.join(tmp, "llm_key")
        with open(fp, "w") as f:
            f.write("sk-test-not-real")  # gitleaks:allow (test-only key)
        u = f"http://127.0.0.1:{cls.fake.server_address[1]}"
        return {"DECIDE_ASR_URL": u + "/asr", "DECIDE_ASR_MODEL": "qwen3-asr",
                "DECIDE_LLM_URL": u + "/llm", "DECIDE_LLM_MODEL": "chat-model", "DECIDE_LLM_KEY_FILE": fp}

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.fake.shutdown()

    def test_state_flags(self):
        _, st = self.req("/api/state")
        self.assertTrue(st["voice"])
        self.assertTrue(st["search_ai"])

    def test_voice(self):
        code, j = self.req("/api/voice", "POST", raw=silence_wav(), ctype="audio/wav")
        self.assertEqual(code, 200, j)
        self.assertEqual(j["text"], "背景再暗一点")  # 去掉了 language 前缀
        ctype, body = FAKE["asr"][-1]
        self.assertIn("multipart/form-data", ctype)
        self.assertIn(b"qwen3-asr", body)
        self.assertEqual(self.req("/api/voice", "POST", raw=b"", ctype="audio/webm")[0], 400)

    def test_search_ai(self):
        a = self.card("吊顶面板比例")
        code, j = self.req("/api/search?ai=1&q=" + urllib.request.quote(f"编号：{a['seq']}"))
        self.assertEqual(code, 200, j)
        self.assertTrue(j["ai"])
        self.assertEqual([c["id"] for c in j["cards"]], [a["id"]])  # 不存在的编号被丢掉
        auth, body = FAKE["llm"][-1]
        self.assertEqual(auth, "Bearer sk-test-not-real")
        self.assertEqual(body["model"], "chat-model")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertIn("吊顶面板比例", body["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
