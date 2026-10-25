"""派活台（paihuotai/）冒烟测试：空 HOME 下 route305 / 飞书桥能起来、能打印状态和帮助。"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R305 = os.path.join(ROOT, "paihuotai", "route305", "route305.py")
BRIDGE = os.path.join(ROOT, "paihuotai", "feishu", "bridge.py")


class PaihuotaiSmokeTest(unittest.TestCase):
    def run_py(self, *args):
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if not k.startswith(("ROUTE305_", "CCPAIHUO_"))}
            env["HOME"] = home
            return subprocess.run([sys.executable, *args], env=env, capture_output=True, text=True,
                                  timeout=60, stdin=subprocess.DEVNULL)

    def test_route305_help(self):
        r = self.run_py(R305, "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        for cmd in ("scan", "ask", "deliver", "wait", "agentdesk"):
            self.assertIn(cmd, r.stdout)

    def test_route305_status_empty_home(self):
        r = self.run_py(R305, "status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("代办台", r.stdout)

    def test_bridge_usage(self):
        r = self.run_py(BRIDGE, "help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ccpaihuo run", r.stdout)


if __name__ == "__main__":
    unittest.main()
