#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTML转Markdown 工具链回归测试（标准库 unittest，无需 pytest）
===========================================================
运行：
  python -m unittest discover -s tests -v
  python tests/test_pipeline.py -v
  python tests/test_pipeline.py -v -k seventeen      # 3.13 支持 -k

覆盖：图片型图文消息的顶层对象切分 / 嵌套 cdn_url 去重 / 导语分组 / YAML 引号 /
      骨架生成 / --no-download 自动建目录 / --dry-run 不写盘 / md_verify 退出码 /
      分支 A（富文本 HTML）端到端。
真实夹具：WXPIC_FIXTURE（3.7MB 渲染 DOM）、HTMLMD_FIXTURE（分支 A 的 html）存在时自动跑。
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "脚本")
sys.path.insert(0, SCRIPTS)

import wechat_picture_page as wpp  # noqa: E402

FIXTURE = os.environ.get("WXPIC_FIXTURE", "")
FIXTURE_A = os.environ.get("HTMLMD_FIXTURE", "")


def wt(path, text, enc="utf-8"):
    with open(path, "w", encoding=enc, newline="\n") as f:
        f.write(text)


def wb(path, data):
    with open(path, "wb") as f:
        f.write(data)


def rd(path):
    with open(path, "rb") as f:
        return f.read()


def make_html(cards=3, desc="导语第一段\\x0a- 要点一\\x0a- 要点二\\x0a① 步骤一\\x0a#限流 #测试"):
    objs = []
    for i in range(1, cards + 1):
        objs.append(
            "{cdn_url: 'https://cdn.test/%d.jpg', width: '1080', height: '1440', "
            "is_qr_code: '%d', create_time: '2026-11-04 10:00', "
            "live_photo: {cdn_url: 'https://cdn.test/%d-live.jpg', width: '360'}, "
            "format_info: {cdn_url: 'https://cdn.test/%d-fmt.jpg'}}"
            % (i, 1 if i == cards else 0, i, i)
        )
    return (
        "<html><head>"
        '<meta property="og:title" content="测试: 17 图讲透限流"/>'
        '<meta property="og:url" content="https://mp.weixin.qq.com/s/TEST"/>'
        '<meta property="og:site_name" content="微信公众号"/>'
        '<meta property="og:description" content="%s"/>'
        "</head><body>"
        '<div id="js_wx_follow_nickname"><span>测试号</span></div>'
        "<script>window.cgiDataNew = {create_time: '2026-11-05 09:03', "
        "album_id: '1', title: '后端图解', picture_page_info_list: [%s]};</script>"
        "</body></html>" % (desc, ", ".join(objs))
    )


class SplitTest(unittest.TestCase):
    def test_top_level_objects(self):
        self.assertEqual(len(wpp.split_objects(wpp.cut_block(make_html(3)))), 3)

    def test_nested_braces_in_string(self):
        objs = wpp.split_objects("[{a: 'x{y}z', b: {c: '1'}}, {a: '2'}]")
        self.assertEqual(len(objs), 2)
        self.assertIn("x{y}z", objs[0])

    def test_top_level_cdn_wins_over_nested(self):
        html = ("<script>x = {picture_page_info_list: "
                "[{live_photo: {cdn_url: 'https://nested/x.jpg'}, cdn_url: 'https://top/x.jpg'}]}</script>")
        self.assertEqual(wpp.parse_cards(html)[0]["url"], "https://top/x.jpg")


class DescTest(unittest.TestCase):
    def test_blocks_and_tags(self):
        blocks, tags = wpp.split_desc("导语\\x0a- 甲\\x0a- 乙\\x0a① 第一步\\x0a#限流 #后端")
        self.assertEqual(tags, ["限流", "后端"])
        self.assertEqual([b[0] for b in blocks], ["para", "bullet", "bullet", "ordered"])
        self.assertEqual(wpp.render_blocks(blocks)[2:4], ["- 甲", "- 乙"])

    def test_ordered_renumbered_not_double(self):
        blocks, _ = wpp.split_desc("① 一\\x0a② 二")
        self.assertEqual(wpp.render_blocks(blocks)[:2], ["1. 一", "2. 二"])


class FrontmatterTest(unittest.TestCase):
    def test_yaml_quoting(self):
        self.assertTrue(wpp.yq("限流: 17 图").startswith('"'))
        self.assertEqual(wpp.yq("普通标题"), "普通标题")
        self.assertEqual(wpp.yq(""), '""')
        self.assertTrue(wpp.yq("true").startswith('"'))
        self.assertEqual(wpp.yq("限流：全角冒号不加引号"), "限流：全角冒号不加引号")

    def test_build_md(self):
        info = {"url": "https://mp.weixin.qq.com/s/TEST"}
        cards = [{"n": 1, "url": "https://cdn/1.jpg", "path": os.path.join("tmp", "01.jpg")},
                 {"n": 2, "url": "https://cdn/2.jpg", "path": None}]
        md = wpp.build_md(info, cards, "assets/x", "标题: 带半角冒号", "作者", "来源",
                          "2026-11-05", ["限流", "高可用"], [("para", "导语")])
        self.assertTrue(md.startswith('---\ntitle: "标题: 带半角冒号"'))
        self.assertIn("![](assets/x/01.jpg)", md)
        self.assertIn("![](https://cdn/2.jpg)", md)
        self.assertIn("## 卡片 02", md)


class CliTest(unittest.TestCase):
    def run_main(self, argv):
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["wechat_picture_page.py"] + argv), redirect_stdout(buf):
            try:
                wpp.main()
            except SystemExit as e:
                return e.code, buf.getvalue()
        return 0, buf.getvalue()

    def test_no_download_creates_out_dir_without_bom(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "page.html")
            wt(src, make_html(3))
            out = os.path.join(td, "deep", "nested", "笔记.md")
            code, output = self.run_main([src, "-o", out, "--no-download"])
            self.assertEqual(code, 0, output)
            self.assertTrue(os.path.exists(out))
            self.assertFalse(rd(out).startswith(b"\xef\xbb\xbf"))
            self.assertEqual(json.loads(output)["失败"], [])

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "page.html")
            wt(src, make_html(3))
            out = os.path.join(td, "笔记.md")
            code, output = self.run_main([src, "-o", out, "--dry-run"])
            self.assertEqual(code, 0, output)
            self.assertFalse(os.path.exists(out))
            self.assertFalse(os.path.exists(os.path.join(td, "assets")))
            self.assertIn("跳过（dry-run", output)

    def test_qr_card_auto_dropped_and_reported(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "page.html")
            wt(src, make_html(3))
            out = os.path.join(td, "笔记.md")
            code, output = self.run_main([src, "-o", out, "--no-download"])
            self.assertEqual(code, 0, output)
            report = json.loads(output)
            self.assertEqual(report["自动跳过（二维码）"], [3])
            self.assertEqual(report["保留"], 2)
            self.assertEqual(report["卡片总数"], 3)


class VerifyTest(unittest.TestCase):
    def run_verify(self, path, strict=False):
        cmd = [sys.executable, os.path.join(SCRIPTS, "md_verify.py"), path]
        if strict:
            cmd.append("--strict")
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8").returncode

    def test_clean_md_passes(self):
        with tempfile.TemporaryDirectory() as td:
            wb(os.path.join(td, "a.jpg"), b"x")
            p = os.path.join(td, "n.md")
            wt(p, "---\ntitle: 测试\n---\n\n# 测试\n\n![](a.jpg)\n\n```lua\nx = 1\n```\n")
            self.assertEqual(self.run_verify(p), 0)

    def test_crlf_tolerated(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "n.md")
            wt(p, "---\ntitle: 测试\n---\n\n正文\n")
            data = rd(p).replace(b"\n", b"\r\n")
            wb(p, data)
            self.assertEqual(self.run_verify(p), 0)

    def test_missing_image_fails(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "n.md")
            wt(p, "---\ntitle: 测试\n---\n\n![](nope.jpg)\n")
            self.assertEqual(self.run_verify(p), 1)

    def test_bom_fails(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "n.md")
            wt(p, "---\ntitle: 测试\n---\n\n正文\n", "utf-8-sig")
            self.assertEqual(self.run_verify(p), 1)

    def test_strict_flags_placeholder(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "n.md")
            wt(p, "---\ntitle: 测试\n---\n\n## 卡片 03\n\n<!-- 在这里转录图内文字 -->\n")
            self.assertEqual(self.run_verify(p, strict=True), 1)


class BranchATest(unittest.TestCase):
    """分支 A：普通富文本 HTML（js_content 里是真文字），走 html_to_md.py。"""

    def test_synthetic_rich_text(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "a.html")
            out = os.path.join(td, "a.md")
            wt(src, '<html><head><meta property="og:title" content="A 测试: 冒号"/>'
                    '<meta property="og:url" content="https://x/y"/></head><body>'
                    '<div id="js_content"><h2>小标题</h2><p>正文段落</p>'
                    "<pre><code>SELECT 1;</code></pre></div></body></html>")
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "html_to_md.py"), src, "-o", out],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            md = rd(out).decode("utf-8")
            self.assertTrue(md.startswith('---\ntitle: "A 测试: 冒号"'), md[:80])
            self.assertIn("```", md)
            self.assertIn("SELECT 1;", md)
            self.assertFalse(rd(out).startswith(b"\xef\xbb\xbf"))

    @unittest.skipUnless(os.path.isfile(FIXTURE_A), "无分支 A 真实 html")
    def test_real_rich_text_dry_run(self):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "html_to_md.py"), FIXTURE_A, "--dry-run"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class RealFixtureTest(unittest.TestCase):
    """真实夹具用环境变量提供，别人 clone 后不设变量即自动跳过。"""

    @unittest.skipUnless(os.path.isfile(FIXTURE), "未设置 WXPIC_FIXTURE，跳过真实 DOM 用例")
    def test_cards_parse(self):
        html = rd(FIXTURE).decode("utf-8", errors="replace")
        cards = wpp.parse_cards(html)
        self.assertGreaterEqual(len(cards), 2, "至少应解析出 2 张卡")
        self.assertEqual(len({c["url"] for c in cards}), len(cards), "cdn_url 必须互不重复")
        pt = wpp.publish_time(html)
        self.assertIn(len(pt), (0, 10), "发布时间应为空或 YYYY-MM-DD")
        self.assertIsInstance(wpp.page_info(html)["nickname"], str)

    @unittest.skipUnless(os.path.isfile(FIXTURE), "无真实渲染 DOM 夹具")
    def test_end_to_end_no_download(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "笔记.md")
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "wechat_picture_page.py"),
                                FIXTURE, "-o", out, "--no-download"],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            md = rd(out).decode("utf-8")
            self.assertIn("## 卡片 03", md)
            self.assertFalse(rd(out).startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
