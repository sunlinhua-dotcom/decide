"""登录不锁定测试：连错多少次都只回 403、不回 429；改密码文件后不重启就能用新密码登录。

python3 -m unittest discover -s tests -v
只用标准库；起一个临时 decide 服务，跑完就删。
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
KEY = "test-key-0123456789abcdef0123456789"  # gitleaks:allow (test-only key)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LoginNoLock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        cls.pw = os.path.join(cls.home, "password")
        with open(cls.pw, "w") as f:
            f.write("111111\n")
        with open(os.path.join(cls.home, "key"), "w") as f:
            f.write(KEY)
        cls.port = free_port()
        env = dict(os.environ, DECIDE_HOME=cls.home, DECIDE_PORT=str(cls.port), DECIDE_KEY=KEY,
                   DECIDE_KEY_FILE=os.path.join(cls.home, "key"), DECIDE_PASS_FILE=cls.pw,
                   DECIDE_MEDIA=os.path.join(cls.home, "media"), DECIDE_CONFIG=os.path.join(cls.home, "none.json"),
                   DECIDE_INTAKE_CONF=os.path.join(cls.home, "no-intake.json"))
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
        shutil.rmtree(cls.home, ignore_errors=True)

    def login(self, p):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/login", json.dumps({"p": p}).encode(),
                                     {"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req, timeout=10).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_no_lockout_and_live_password(self):
        codes = [self.login("000000") for _ in range(7)]
        self.assertEqual(codes, [403] * 7)
        self.assertEqual(self.login("111111"), 200)
        with open(self.pw, "w") as f:
            f.write("222222\n")
        self.assertEqual(self.login("111111"), 403)
        self.assertEqual(self.login("222222"), 200)


if __name__ == "__main__":
    unittest.main()
