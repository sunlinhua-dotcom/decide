"""端到端冒烟测试：起一个临时服务，用 bin/decide 推卡、在 API 上作答、拿回答案，再看网页和媒体能不能出。

python3 -m unittest discover -s tests -v
只用标准库；数据放临时目录，跑完就删。
"""
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "bin", "decide")
KEY = "test-key-0123456789abcdef0123456789"  # gitleaks:allow (test-only key)


def write_png(path, w, h):
    """不靠任何库写一张 RGB 渐变 PNG。"""
    rows = b"".join(b"\0" + b"".join(bytes((x * 4 % 256, y * 5 % 256, 128)) for x in range(w)) for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def image_tool():
    """和 bin/decide 的判断一致：macOS 用 sips，其他系统有 Pillow 用 Pillow，都没有就原图直传。"""
    if shutil.which("sips"):
        return "sips"
    try:
        import PIL.Image  # noqa: F401
        return "pil"
    except ImportError:
        return None


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

    def cli_as(self, session, *args, stdin=None):
        env = dict(self.env, DECIDE_SESSION=session)
        return subprocess.run([sys.executable, CLI, *args], env=env, input=stdin, capture_output=True, text=True, timeout=60)

    def test_hooks_deliver_missed_answers(self):
        s = "hook-missed"
        cid = self.cli_as(s, "q", "Which road?", "North", "South", "-p", "smoke").stdout.split()[0]
        # Nobody is listening: the Stop hook blocks once and asks for a listener.
        r = self.cli_as(s, "hook", "stop", stdin="{}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["decision"], "block")
        self.assertEqual(self.cli_as(s, "hook", "stop", stdin='{"stop_hook_active": true}').stdout, "")
        card = json.loads(self.req(f"/api/cards/{cid}")[2])
        self.req(f"/api/cards/{cid}/answer", "POST", {"choice": [card["options"][1]["id"]], "note": "the coast road"})
        # The answer reaches the session with the next prompt, exactly once.
        r = self.cli_as(s, "hook", "prompt", stdin="{}")
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("South", ctx)
        self.assertIn("the coast road", ctx)
        self.assertEqual(self.cli_as(s, "hook", "prompt", stdin="{}").stdout, "")
        state = json.loads(self.req("/api/session?session=" + s)[2])
        self.assertEqual(state["pending"], [])
        self.assertEqual(state["unacked"], [])

    def test_hook_quiet_while_wait_watches(self):
        s = "hook-watched"
        cid = self.cli_as(s, "q", "Keep going?", "Yes", "No", "-p", "smoke").stdout.split()[0]
        self.cli_as(s, "wait", cid, "--timeout", "1")
        self.assertEqual(self.cli_as(s, "hook", "stop", stdin="{}").stdout, "")
        self.assertTrue(json.loads(self.req("/api/session?session=" + s)[2])["pending"][0]["watched"])
        self.cli_as(s, "cancel", cid)

    def test_hook_silent_without_config(self):
        env = dict(self.env, DECIDE_KEY="", DECIDE_SESSION="hook-none")
        r = subprocess.run([sys.executable, CLI, "hook", "stop"], env=env, input="{}", capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_listen_timeout_names_pending_cards(self):
        s = "hook-timeout"
        cid = self.cli_as(s, "q", "Tea or coffee?", "Tea", "Coffee", "-p", "smoke").stdout.split()[0]
        seq = json.loads(self.req(f"/api/cards/{cid}")[2])["seq"]
        r = self.cli_as(s, "listen", "--timeout", "1")
        self.assertEqual(r.returncode, 124)
        self.assertIn(f"{seq:02d}", r.stdout)
        self.assertIn(cid, self.cli_as(s, "ls", "--pending", "-p", "smoke").stdout)
        self.cli_as(s, "cancel", cid)

    def test_three_choices_need_a_recommendation(self):
        r = self.cli("q", "Which venue?", "Hall", "Park", "Roof", "-p", "smoke")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--rec", r.stderr)
        r = self.cli("q", "Which venue?", "Hall", "Park", "Roof", "-p", "smoke", "--rec", "2")
        self.assertEqual(r.returncode, 2)  # the recommended option says nothing about why
        r = self.cli("q", "Which venue?", "Hall::indoors", "Park::shade, near parking", "Roof::wind", "-p", "smoke", "--rec", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        card = json.loads(self.req(f"/api/cards/{r.stdout.split()[0]}")[2])
        self.assertEqual([o.get("rec", False) for o in card["options"]], [False, True, False])
        self.assertEqual(self.cli("q", "Which venue?", "Hall", "Park", "Roof", "-p", "smoke", "--no-rec").returncode, 0)

    def test_pick_takes_rec(self):
        imgs = []
        for i in range(3):
            path = os.path.join(self.tmp, f"p{i}.png")
            write_png(path, 40, 30)
            imgs.append(path)
        r = self.cli("pick", "-p", "smoke", "-t", "Cover shot", "--choice", f"A::{imgs[0]}::busy", "--choice",
                     f"B::{imgs[1]}::cleanest, face in focus", "--choice", f"C::{imgs[2]}::dark", "--rec", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        card = json.loads(self.req(f"/api/cards/{r.stdout.split()[0]}")[2])
        self.assertEqual([o.get("rec", False) for o in card["options"]], [False, True, False])
        self.assertEqual([m["label"] for m in card["media"]], ["A", "B", "C"])

    def test_svg_rec_word_turns_red(self):
        svg = os.path.join(self.tmp, "rec.svg")
        with open(svg, "w", encoding="utf-8") as f:
            f.write('<svg viewBox="0 0 300 100"><text y="30" fill="#222">方案 A（推荐）</text>'
                    '<text y="70" fill="#d33f2a">推荐先修</text></svg>')
        r = self.cli("q", "Which plan?", "A", "B", "-p", "smoke", "--svg", svg)
        self.assertEqual(r.returncode, 0, r.stderr)
        card = json.loads(self.req(f"/api/cards/{r.stdout.split()[0]}")[2])
        body = self.req(card["media"][0]["src"])[2].decode()
        self.assertIn('<tspan fill="#d33f2a" font-weight="700">推荐</tspan>', body)
        self.assertIn('<tspan fill="#e08a00" font-weight="700">推荐</tspan>', body)

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

    def test_done_card_links(self):
        svg = os.path.join(self.tmp, "done.svg")
        with open(svg, "w") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 200"><text x="10" y="40">Shipped</text>'
                    '<text x="10" y="80">Site: app.example.com, status 200</text></svg>')
        r = self.cli("q", "Done, what next?", "Stop", "Fix::say what", "-p", "smoke", "--svg", svg,
                     "--link", "Docs::https://docs.example.com/guide")
        self.assertEqual(r.returncode, 0, r.stderr)
        cid = r.stdout.split()[0]
        card = json.loads(self.req(f"/api/cards/{cid}")[2])
        self.assertEqual([l["url"] for l in card["links"]], ["https://docs.example.com/guide", "https://app.example.com"])
        self.assertEqual(card["links"][0]["t"], "Docs")
        code, _, _ = self.req(f"/api/cards/{cid}/links", "POST", {"links": [{"t": "x", "url": "javascript:alert(1)"}]})
        self.assertEqual(code, 400)
        self.assertEqual(self.cli("link", cid, "--clear").returncode, 0)
        self.assertNotIn("links", json.loads(self.req(f"/api/cards/{cid}")[2]))

    def test_all_digit_card_id(self):
        # Card ids are 8 hex chars; about 2% are all digits and must not be read as a sequence number.
        code, _, _ = self.req("/api/cards", "POST", {"project": "smoke", "id": "12345678", "kind": "ask", "title": "Digits",
                                                     "options": [{"id": "a", "label": "A"}], "links": [{"t": "x", "url": "https://example.com"}]})
        self.assertEqual(code, 201)
        r = self.cli("link", "12345678", "--clear")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("links", json.loads(self.req("/api/cards/12345678")[2]))
        self.assertEqual(self.cli("cancel", "12345678").returncode, 0)

    def test_note_only_answer(self):
        cid = self.cli("q", "Which way?", "Left", "Right", "-p", "smoke").stdout.split()[0]
        code, _, _ = self.req(f"/api/cards/{cid}/answer", "POST", {"choice": ["note"], "note": ""})
        self.assertEqual(code, 400)
        code, _, body = self.req(f"/api/cards/{cid}/answer", "POST", {"choice": ["note"], "note": "neither, go up"})
        self.assertEqual(code, 200, body)
        card = json.loads(self.req(f"/api/cards/{cid}")[2])
        self.assertEqual(card["status"], "answered")
        self.assertEqual(card["answer"]["note"], "neither, go up")

    def test_only_dense_svg_becomes_web_text(self):
        def plan_svg(path, n, lines):
            parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 900">',
                     '<text x="40" y="50" font-size="30">Plan for the next release</text>']
            for i in range(n):
                y = 90 + i * 260
                parts.append(f'<g><rect x="40" y="{y}" width="1100" height="240" fill="#fff" stroke="#333"/>'
                             f'<text x="60" y="{y + 40}" font-size="22" font-weight="700">Step {i + 1} heading</text>')
                parts += [f'<text x="60" y="{y + 80 + j * 34}" font-size="18">{lines}</text>' for j in range(4)]
                parts.append("</g>")
            with open(path, "w") as f:
                f.write("".join(parts) + "</svg>")
        simple, dense = os.path.join(self.tmp, "simple.svg"), os.path.join(self.tmp, "dense.svg")
        plan_svg(simple, 2, "one short line here")
        plan_svg(dense, 3, "a longer sentence that explains what changes in this step and why")
        for path, want in ((simple, False), (dense, True)):
            r = self.cli("q", "Plan ok?", "Yes", "No", "-p", "smoke", "--svg", path)
            self.assertEqual(r.returncode, 0, r.stderr)
            card = json.loads(self.req(f"/api/cards/{r.stdout.split()[0]}")[2])
            self.assertEqual(bool(card.get("plan")), want, path)
            if want:
                self.assertEqual(len(card["plan"]["secs"]), 3)
                self.assertFalse(any(s.get("gen") for s in card["plan"]["secs"]))  # 没配出图命令就不挂占位

    def test_image_review_card(self):
        png = os.path.join(self.tmp, "frame.png")
        write_png(png, 64, 48)
        r = self.cli("ask", "-p", "smoke", "-t", "Shot 1 · test frame", "--after", png, "--crash", "blurry", "--fix", "sharper")
        self.assertEqual(r.returncode, 0, r.stderr)
        card = json.loads(self.req(f"/api/cards/{r.stdout.split()[0]}")[2])
        self.assertEqual(card["kind"], "review")
        self.assertEqual([o["label"] for o in card["options"]], ["通过", "返修"])
        m = card["media"][0]
        self.assertEqual(m["kind"], "img")
        if image_tool():  # 出了 web / 缩略 / 原图三档，带尺寸
            self.assertEqual((m["w"], m["h"]), (64, 48))
            paths = [m["src"], m["thumb"], m["orig"]]
        else:
            self.assertNotIn("thumb", m)
            paths = [m["src"]]
        for src in paths:
            code, headers, body = self.req(src)
            self.assertEqual(code, 200, src)
            self.assertTrue(headers.get("Content-Type", "").startswith("image/"), src)
            self.assertGreater(len(body), 50, src)

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
