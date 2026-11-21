"""路由题门禁测试：会话里推「用哪台 / 判官 / 去油」这类题直接拒，--must-ask 和常驻程序放行。

python3 -m unittest discover -s tests -v
只用标准库，不连服务。
"""
import argparse
import importlib.machinery
import importlib.util
import os
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ld = importlib.machinery.SourceFileLoader("decide_cli", os.path.join(ROOT, "bin", "decide"))
dc = importlib.util.module_from_spec(importlib.util.spec_from_loader("decide_cli", _ld))
_ld.exec_module(dc)


def q(title, sub=None, must=None):
    return argparse.Namespace(question=title, sub=sub, must_ask=must)


def blocked(a):
    try:
        dc.route_gate(a)
    except SystemExit as e:
        return e.code
    return None


class RouteGate(unittest.TestCase):
    def test_blocks_routing_in_session(self):
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1"}):
            for t in ("这批 H3 开不开去油腻？", "看这 6 张图用哪个判官", "ffmpeg 用哪台跑",
                      "封面走哪条通道出", "这活谁来做", "SS305 搜哪些平台？", "这张网页开不开 Taste Skill v2？"):
                self.assertEqual(blocked(q(t)), 64, t)

    def test_lets_real_decisions_through(self):
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1"}):
            for t in ("已完成 · 霓虹夜驰（判官 5.5→7.6），下一步？", "四台 Spark v2 · 口播关去油重出，口型这版行吗？",
                      "发布 · 这次发哪些平台", "N14 温控旋钮特写 · 这版能进成片吗"):
                self.assertIsNone(blocked(q(t)), t)
            self.assertIsNone(blocked(q("封面用哪条通道出", must="302 要花钱")))

    def test_daemons_not_gated(self):
        with mock.patch.dict(os.environ, {"CLAUDECODE": ""}):
            self.assertIsNone(blocked(q("GrokBot 代办：交给谁办？")))


if __name__ == "__main__":
    unittest.main()
