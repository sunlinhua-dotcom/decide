"""字多的说明图拆成网页文字：两栏完成卡、没框的方案卡都拆，流程图照原样当图；配图只给三节以上的。

python3 -m unittest discover -s tests -v
只用标准库。
"""
import importlib.machinery
import importlib.util
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_cli():
    loader = importlib.machinery.SourceFileLoader("decide_cli_plan", os.path.join(ROOT, "bin", "decide"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


HEAD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 660"><rect width="1200" height="660" fill="#f7f6f2"/>'

# 两栏完成卡：一句话折成两行，续行缩进
BOXED = HEAD + """
<text x="40" y="54" font-size="30">新版排版已上线：两件做完、两件待定</text>
<rect x="40" y="80" width="545" height="490" fill="#fff" stroke="#c9c5b8"/>
<text x="60" y="118" font-size="22">做完了（已验证）</text>
<text x="60" y="158" font-size="18">· 回归：待定卡 63 / 63，审阅页 54 / 54</text>
<text x="76" y="188" font-size="18">单元测试也全部通过：没有新的失败</text>
<text x="60" y="222" font-size="18">· 线上文件和本地一致，哈希逐个核对过</text>
<rect x="615" y="80" width="545" height="490" fill="#fff" stroke="#c9c5b8"/>
<text x="635" y="118" font-size="22">还剩（等你定）</text>
<text x="635" y="158" font-size="18">· 流程图仍按图片显示，字小时要点开</text>
<text x="651" y="188" font-size="18">放大看，下一版再考虑拆成文字</text>
<text x="635" y="222" font-size="18">· 选项自带的小图这次没有动</text>
<text x="40" y="620" font-size="20"><tspan fill="#d33f2a">推荐</tspan>：先收工，下次遇到再改。</text>
</svg>"""

# 没框的方案卡：加粗大字是小标题；最后隔一大段空的那行是结尾的话
BARE = HEAD + """
<text x="40" y="54" font-size="27" font-weight="600">说明图字太小：原因和改法</text>
<text x="40" y="100" font-size="21" font-weight="600">原因</text>
<g font-size="18">
<text x="40" y="134">· 说明图整张当图片缩放，1280 屏上只剩 7 px</text>
<text x="40" y="164">· 拆成网页文字只给三节以上且 400 字的图</text>
</g>
<text x="40" y="214" font-size="21" font-weight="600">改法</text>
<g font-size="18">
<text x="40" y="248">1. 字多的说明图都拆成网页文字，正文放大</text>
<text x="40" y="280">2. 拆出的文字和卡里的视频一起排，放不下框内滚动</text>
<text x="40" y="312">3. 回归加图上实际字号检查，桌面小于 12 px 判失败</text>
</g>
<text x="40" y="368" font-size="18">代价：约 1 小时，回归全过再上线</text>
<text x="40" y="430" font-size="19" fill="#d33f2a" font-weight="600">推荐：按这个改</text>
</svg>"""

# 流程图：框里写短句、框之间有箭头
FLOW = HEAD + """
<text x="40" y="52" font-size="26">首版怎么做（数据从哪来、到哪去）</text>
<g font-size="18">
<rect x="40" y="84" width="200" height="64"/><text x="60" y="123">微博 · 抖音两个源已通</text>
<rect x="290" y="84" width="200" height="140"/><text x="300" y="140">热榜抓取服务部署在内网</text>
<text x="300" y="168">不改上游源码只读快照</text>
<rect x="540" y="84" width="380" height="140"/><text x="560" y="116">我们自己写的排序与过滤</text>
<text x="560" y="148">快照、清洗、聚类、规则过滤</text>
<text x="560" y="174">再交给模型判断能不能玩</text>
</g>
<path d="M240 116 H286" stroke="#888"/><path d="M490 154 H536" stroke="#888"/>
</svg>"""


class SvgPlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = load_cli()
        cls.tmp = tempfile.mkdtemp(prefix="decide-plan-")

    def plan(self, svg):
        p = os.path.join(self.tmp, "x.svg")
        with open(p, "w", encoding="utf-8") as f:
            f.write(svg)
        return self.cli.svg_plan(p)

    def test_boxed_two_panels_join_wrapped_lines(self):
        p = self.plan(BOXED)
        self.assertEqual([s["t"] for s in p["secs"]], ["做完了（已验证）", "还剩（等你定）"])
        self.assertEqual(p["secs"][0]["lines"][0], "· 回归：待定卡 63 / 63，审阅页 54 / 54 单元测试也全部通过：没有新的失败")
        self.assertEqual(p["secs"][1]["lines"][0], "· 流程图仍按图片显示，字小时要点开放大看，下一版再考虑拆成文字")
        self.assertTrue(p["foot"][0].startswith("推荐"))
        self.assertFalse(self.cli.plan_pics(p))

    def test_bare_card_splits_on_headings(self):
        p = self.plan(BARE)
        self.assertEqual(p["title"], "说明图字太小：原因和改法")
        self.assertEqual([s["t"] for s in p["secs"]], ["原因", "改法"])
        self.assertEqual(len(p["secs"][1]["lines"]), 3)
        self.assertEqual(p["foot"], ["代价：约 1 小时，回归全过再上线", "推荐：按这个改"])

    def test_flowchart_stays_image(self):
        self.assertIsNone(self.plan(FLOW))


if __name__ == "__main__":
    unittest.main()
