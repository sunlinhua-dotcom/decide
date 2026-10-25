"""字多的说明图拆成网页文字：两栏完成卡（列表包在 <g> 里的也算）、没框的方案卡都拆，流程图照原样当图；配图只给三节以上的。

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

# 两栏卡里的编号列表包在只管字号的 <g> 里，冒号收尾的一句折成两行；嵌了照片的照原样当图
GROUPED = HEAD + """
<text x="48" y="62" font-size="30" font-weight="700">两种做法：人一步步操作，还是交给助手跑完再拍板</text>
<rect x="48" y="96" width="540" height="520" fill="#fff" stroke="#d9d4ca"/>
<text x="76" y="140" font-size="23" font-weight="700" fill="#6b6b6b">手动做（人在操作）</text>
<g font-size="19" fill="#2b2b2b">
<text x="76" y="186">1. 人决定这里要改什么</text>
<text x="76" y="224">2. 人写指令，工具出一版</text>
<text x="76" y="262">3. 人把几版拼成最终稿</text>
</g>
<text x="76" y="400" font-size="19" fill="#6b6b6b">像请了个打字员：</text>
<text x="76" y="432" font-size="19" fill="#6b6b6b">每个字都得你口述。</text>
<rect x="612" y="96" width="540" height="520" fill="#fff" stroke="#c0392b"/>
<text x="640" y="140" font-size="23" font-weight="700" fill="#c0392b">交给助手（人只拍板）</text>
<g font-size="19" fill="#2b2b2b">
<text x="640" y="186">1. 人给一句话和素材</text>
<text x="640" y="224">2. 助手自己找出要改的地方</text>
<text x="640" y="262">3. 人在决策板上点通过或返修</text>
</g>
<text x="48" y="672" font-size="20">旧版的毛病：观众以为是在教手动做法。</text>
<text x="48" y="712" font-size="20"><tspan fill="#d33f2a">推荐</tspan> 新版：反转改成助手自己看出来的。</text>
</svg>"""
PHOTO = GROUPED.replace("</svg>", '<image x="700" y="300" width="300" height="200" href="data:image/jpeg;base64,AAAA"/></svg>')

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

# 几个小节的框和字全包在一个只管字号字色的 <g> 里：外壳不算一节，照样按框拆
SHELL = HEAD + """
<text x="40" y="54" font-size="28" font-weight="700">新片脚本：先讲处境和账，最后才亮出产品</text>
<g font-size="18" fill="#222">
<rect x="40" y="84" width="360" height="440" fill="#fff" stroke="#c9c5b8"/>
<text x="60" y="122" font-size="21" font-weight="700">① 钩子（0–6 秒）</text>
<text x="60" y="160">· 先说观众以为的事，再拿一个反例打掉</text>
<text x="60" y="192">· 画面只留一个人和一句客户原话</text>
<rect x="420" y="84" width="360" height="440" fill="#fff" stroke="#c9c5b8"/>
<text x="440" y="122" font-size="21" font-weight="700">② 时间账（6–30 秒）</text>
<text x="440" y="160">· 每天省下多少分钟，用后台真实记录说话</text>
<text x="440" y="192">· 估算的数字在画面上写明怎么算的</text>
<rect x="800" y="84" width="360" height="440" fill="#fff" stroke="#c9c5b8"/>
<text x="820" y="122" font-size="21" font-weight="700">③ 亮出产品（30–40 秒）</text>
<text x="820" y="160">· 产品最后才出场，只拍它解决的那一步</text>
<text x="820" y="192">· 结尾一句话收住，不念功能清单</text>
</g>
<text x="40" y="580" font-size="20">有两处要你拍板：估算数字能不能上画面</text>
<text x="40" y="620" font-size="20"><tspan fill="#d33f2a">推荐</tspan>：按这个结构写稿。</text>
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

    def test_boxed_lists_inside_plain_groups(self):
        p = self.plan(GROUPED)
        self.assertEqual([s["t"] for s in p["secs"]], ["手动做（人在操作）", "交给助手（人只拍板）"])
        self.assertEqual(p["secs"][0]["lines"], ["1. 人决定这里要改什么", "2. 人写指令，工具出一版", "3. 人把几版拼成最终稿",
                                                 "像请了个打字员：每个字都得你口述。"])
        self.assertEqual(len(p["secs"][1]["lines"]), 3)
        self.assertEqual(len(p["foot"]), 2)

    def test_embedded_photo_stays_image(self):
        self.assertIsNone(self.plan(PHOTO))

    def test_sections_wrapped_in_one_style_group(self):
        p = self.plan(SHELL)
        self.assertEqual(p["title"], "新片脚本：先讲处境和账，最后才亮出产品")
        self.assertEqual([s["t"] for s in p["secs"]], ["① 钩子（0–6 秒）", "② 时间账（6–30 秒）", "③ 亮出产品（30–40 秒）"])
        self.assertTrue(all(len(s["lines"]) == 2 for s in p["secs"]))
        self.assertEqual(len(p["foot"]), 2)

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
