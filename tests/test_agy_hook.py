"""Antigravity 会话识别与 PreInvocation / Stop 钩子协议测试。"""
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
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "test-key-0123456789abcdef0123456789"  # gitleaks:allow (test-only key)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AgyHookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="decide-agy-")
        with open(os.path.join(cls.tmp, "key"), "w") as f:
            f.write(KEY)
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        env = dict(
            os.environ,
            DECIDE_HOME=cls.tmp,
            DECIDE_PORT=str(cls.port),
            DECIDE_KEY=KEY,
            DECIDE_KEY_FILE=os.path.join(cls.tmp, "key"),
            DECIDE_MEDIA=os.path.join(cls.tmp, "media"),
            DECIDE_INTAKE_CONF=os.path.join(cls.tmp, "no-intake.json"),
        )
        cls.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "server", "decide_server.py")],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
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

    def run_cli(self, args, stdin_obj=None, extra_env=None):
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": self.tmp,
            "DECIDE_BASE": self.base,
            "DECIDE_KEY": KEY,
        }
        if extra_env:
            env.update(extra_env)
        inp = json.dumps(stdin_obj).encode() if stdin_obj is not None else None
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "decide")] + args,
            input=inp,
            capture_output=True,
            env=env,
            timeout=15,
        )
        return r.returncode, r.stdout.decode("utf-8").strip(), r.stderr.decode("utf-8").strip()

    def test_agy_session_and_hooks(self):
        conv_id = "f8cdd84e-17fe-4236-9452-87d4cf51aa71"
        # 1. 在 ANTIGRAVITY_CONVERSATION_ID 下推卡，自动绑定 session
        rc, out, err = self.run_cli(
            ["q", "测试副屏中继？", "好::继续", "改::重来", "--rec", "1", "-p", "agytest"],
            extra_env={"ANTIGRAVITY_CONVERSATION_ID": conv_id},
        )
        self.assertEqual(rc, 0, err)
        cid = out.splitlines()[0].strip()

        # 2. 未挂监听时触发 Antigravity Stop 钩子 -> 应返回 {"decision": "continue", ...}
        rc, out, err = self.run_cli(
            ["hook", "stop"],
            stdin_obj={"conversationId": conv_id, "terminationReason": "NO_TOOL_CALL"},
        )
        self.assertEqual(rc, 0, err)
        j = json.loads(out)
        self.assertEqual(j.get("decision"), "continue")
        self.assertIn("decide listen", j.get("reason", ""))

        # 3. 用户在板上答完卡 -> PreInvocation (hook prompt) 自动返回 injectSteps 并 ack
        card_info = json.loads(
            urllib.request.urlopen(
                urllib.request.Request(f"{self.base}/api/cards/{cid}", headers={"X-Decide-Key": KEY}),
                timeout=5,
            ).read()
        )
        first_oid = card_info["options"][0]["id"]
        req = urllib.request.Request(
            f"{self.base}/api/cards/{cid}/answer",
            data=json.dumps({"choice": [first_oid], "note": "用漫画风", "at": time.time() - 60}).encode(),
            method="POST",
            headers={"X-Decide-Key": KEY, "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5).read()

        rc, out, err = self.run_cli(
            ["hook", "prompt"],
            stdin_obj={"conversationId": conv_id, "invocationNum": 1},
        )
        self.assertEqual(rc, 0, err)
        j = json.loads(out)
        steps = j.get("injectSteps", [])
        self.assertEqual(len(steps), 1)
        sys_msg = steps[0]["systemMessage"]["systemMessage"]
        self.assertIn("用漫画风", sys_msg)

        # 4. 再次调用 Stop 钩子 -> 已 ack 且无待定卡，返回空对象 {} 放行
        rc, out, err = self.run_cli(
            ["hook", "stop"],
            stdin_obj={"conversationId": conv_id, "terminationReason": "NO_TOOL_CALL"},
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out), {})


if __name__ == "__main__":
    unittest.main()
