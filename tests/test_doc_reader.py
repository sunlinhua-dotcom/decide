"""稿件阅读器测试：选项自带的剧本 / 文案稿图拆成一行行文字（折行接回、分出标题 / 镜头 / 台词 / 注），
.md 稿直接读；服务端收下选项全文、已上板的卡能补全文。

python3 -m unittest discover -s tests -v
只用标准库；服务端部分起一个临时 decide 服务，跑完就删。
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
_ld = importlib.machinery.SourceFileLoader("decide_cli_doc", os.path.join(ROOT, "bin", "decide"))
dc = importlib.util.module_from_spec(importlib.util.spec_from_loader(_ld.name, _ld))
_ld.exec_module(dc)

SCRIPT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 900">
<rect width="1080" height="900" fill="#fff"/>
<text x="60" y="70" font-size="27" fill="#111">《雨伞》样片 · 竖版 30 秒</text>
<text x="60" y="120" font-size="20" fill="#333">一句话：下雨天他总把伞往她那边歪，自己湿了半边肩膀；这次他买了一把大</text>
<text x="60" y="150" font-size="20" fill="#333">伞。</text>
<text x="60" y="210" font-size="17" fill="#888">01  0:00–0:10</text>
<text x="60" y="245" font-size="19" fill="#222">傍晚的公交站，雨下得很大。他撑开伞，往她那边歪了一下，</text>
<text x="60" y="275" font-size="19" fill="#222">自己的左肩露在外面。</text>
<text x="60" y="310" font-size="19" fill="#2b5d8a">台词：「你那边湿了。」「没事。」</text>
<text x="60" y="345" font-size="15" fill="#999">注：只拍背影和伞。</text>
<text x="60" y="400" font-size="17" fill="#888">02  0:10–0:30</text>
<text x="60" y="435" font-size="19" fill="#222">第二天下雨，他撑开一把很大的伞，两个人都在伞下。</text>
<text x="60" y="470" font-size="19" fill="#2b5d8a">字幕：一把伞，两个人都不湿。</text>
</svg>"""

FLOW = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 200">
<defs><marker id="a"/></defs>
<rect x="10" y="10" width="100" height="40"/><text x="20" y="35" font-size="14">收件</text>
<line x1="110" y1="30" x2="200" y2="30" marker-end="url(#a)"/>
<rect x="200" y="10" width="100" height="40"/><text x="210" y="35" font-size="14">分拣</text>
</svg>"""


class SvgDoc(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write(self, name, text):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_script_svg_becomes_lines(self):
        doc = dc.svg_doc(self.write("s.svg", SCRIPT))
        self.assertEqual(doc["title"], "《雨伞》样片 · 竖版 30 秒")
        roles = [r for r, _ in doc["lines"]]
        self.assertEqual(roles, ["lead", "s", "p", "say", "n", "s", "p", "say"])
        self.assertTrue(doc["lines"][0][1].endswith("这次他买了一把大伞。"))  # 折行接回一句
        self.assertEqual(doc["lines"][2][1], "傍晚的公交站，雨下得很大。他撑开伞，往她那边歪了一下，自己的左肩露在外面。")

    def test_flowchart_stays_image(self):
        self.assertIsNone(dc.svg_doc(self.write("f.svg", FLOW)))

    def test_markdown_doc(self):
        doc = dc.text_doc(self.write("s.md", "# 《雨伞》\n\n一句话：他把伞往她那边歪。\n\n## 01  0:00–0:10\n\n傍晚的公交站。\n\n台词：「没事。」\n\n> 只拍背影。\n"))
        self.assertEqual(doc["title"], "《雨伞》")
        self.assertEqual([r for r, _ in doc["lines"]], ["lead", "s", "p", "say", "n"])

    def test_markdown_table(self):
        md = ("# 《雨伞》\n\n| 镜 | 时间 | 画面 | 台词 / 字幕 | 备注 |\n|---|---|---|---|---|\n"
              "| 1 | 0:00–0:10 | 傍晚的公交站。 | 「没事。」 | 只拍背影 |\n| 2 | 0:10–0:20 | 雨停了。 |  |  |\n")
        doc = dc.text_doc(self.write("t.md", md))
        self.assertEqual(doc["lines"], [["s", "01  0:00–0:10"], ["p", "傍晚的公交站。"], ["say", "「没事。」"], ["n", "只拍背影"],
                                        ["s", "02  0:10–0:20"], ["p", "雨停了。"]])


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerDocs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        with open(os.path.join(cls.tmp, "key"), "w") as f:
            f.write(KEY)
        cls.port = free_port()
        env = dict(os.environ, DECIDE_HOME=cls.tmp, DECIDE_PORT=str(cls.port), DECIDE_KEY=KEY,
                   DECIDE_KEY_FILE=os.path.join(cls.tmp, "key"), DECIDE_MEDIA=os.path.join(cls.tmp, "media"),
                   DECIDE_CONFIG=os.path.join(cls.tmp, "none.json"), DECIDE_INTAKE_CONF=os.path.join(cls.tmp, "no-intake.json"))
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server", "decide_server.py")], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cls.port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def api(self, method, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", json.dumps(body).encode() if body is not None else None,
                                     {"Content-Type": "application/json", "X-Decide-Key": KEY}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_doc_kept_and_patchable(self):
        doc = {"title": "《雨伞》", "lines": [["lead", "一句话：他把伞往她那边歪。"], ["say", "台词：「没事。」"]]}
        code, r = self.api("POST", "/api/cards", {"project": "qtest", "kind": "ask", "title": "两稿挑哪个", "multi": True,
                                                  "options": [{"id": "a", "label": "《雨伞》", "doc": doc},
                                                              {"id": "b", "label": "《晴天》"}]})
        self.assertIn(code, (200, 201), r)
        code, card = self.api("GET", f"/api/cards/{r['id']}")
        self.assertEqual(card["options"][0]["doc"], doc)
        self.assertNotIn("doc", card["options"][1])
        code, card = self.api("POST", f"/api/cards/{r['id']}/docs", {"docs": {"b": {"title": "《晴天》", "lines": [["p", "出太阳了。"]]}}})
        self.assertEqual(code, 200, card)
        self.assertEqual(card["options"][1]["doc"]["lines"], [["p", "出太阳了。"]])
        code, _ = self.api("POST", f"/api/cards/{r['id']}/docs", {"docs": {"b": {"lines": [["bogus", "x"]]}}})
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
