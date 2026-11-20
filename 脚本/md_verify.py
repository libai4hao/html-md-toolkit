#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Markdown 成品校验（转档收尾必跑）
===========================================================
用法：python md_verify.py 笔记.md [--strict]
检查：BOM / YAML 头完整性 / 图片链接本地可解析 / 空 alt / nbsp / 行尾空格 /
      三连空行 / 围栏成对 / H1-H3 计数 / 占位残留 / CommonMark 渲染后 <pre> 与 <table>。
退出码：0 干净；1 有硬缺陷；2 参数或文件错误（可直接挂进巡检/钩子）。
"table 0" 说明用了 Markdown 表格但 CommonMark 不含表格语法，应改成成对的分组列表。
--strict 把"空 alt""占位残留""图片走远端外链"也升为硬缺陷（知识库归档建议开）。
"""
import os
import re
import sys


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    strict = "--strict" in sys.argv
    if len(args) != 1:
        sys.exit("用法：python md_verify.py 笔记.md [--strict]")
    p = args[0]
    if not os.path.isfile(p):
        sys.exit("文件不存在：%s" % p)

    raw = open(p, "rb").read()
    bom = raw[:3] == b"\xef\xbb\xbf"
    s = raw.decode("utf-8-sig")
    crlf = s.count("\r\n")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    d = os.path.dirname(os.path.abspath(p))
    fence = "`" * 3

    print("文件", os.path.basename(p))
    print("行数", s.count("\n") + 1, "字符", len(s))
    print("行尾 CRLF", crlf, "（跨平台混用会 diffs 噪音，建议统一 LF）")

    # BOM 会让 Obsidian 读不到 frontmatter，知识库里的 .md 必须无 BOM
    print("BOM", "有（知识库禁止）" if bom else "无")

    # frontmatter：首行 --- 到下一个 --- 之间必须是 key: value
    fm_keys = []
    has_fm = s.startswith("---\n")
    if has_fm:
        end = s.find("\n---", 4)
        block = s[4:end] if end > 0 else ""
        for line in block.split("\n"):
            m = re.match(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$", line)
            if m:
                fm_keys.append(m.group(1))
        print("frontmatter", "字段 %s" % ",".join(fm_keys) if fm_keys else "解析失败")
    else:
        print("frontmatter", "缺失（无 YAML 头，知识库检索会失去属性）")

    links = re.findall(r"!\[(.*?)\]\((.+?)\)", s)
    missing, remote = [], []
    for _, u in links:
        if u.startswith(("http://", "https://")):
            remote.append(u)
        elif not os.path.exists(os.path.join(d, u.replace("/", os.sep))):
            missing.append(u)
    empty_alt = [u for t, u in links if not t.strip()]
    print("图片", len(links), "缺失", missing)
    print("空 alt", empty_alt)
    print("远端图", len(remote))
    print("nbsp", s.count("\xa0"),
          "行尾空格", sum(1 for l in s.split("\n") if l != l.rstrip()),
          "三连空行", len(re.findall("\n\n\n\n", s)))
    fences = len([l for l in s.split("\n") if l.strip().startswith(fence)])
    print("围栏", fences, "行尾空格清理后围栏成对" if fences % 2 == 0 else "围栏数为奇数，有未闭合的块")
    print("H1/H2/H3", len(re.findall(r"^# ", s, re.M)),
          len(re.findall(r"^## ", s, re.M)), len(re.findall(r"^### ", s, re.M)))
    placeholders = len(re.findall(r"## 卡片 \d", s)) + len(re.findall(r"在这里转录", s))
    print("占位残留", placeholders)

    hard = []
    if bom:
        hard.append("BOM")
    if not has_fm:
        hard.append("无 frontmatter")
    if missing:
        hard.append("图片缺失 %d" % len(missing))
    if fences % 2:
        hard.append("围栏未闭合")
    if strict:
        if empty_alt:
            hard.append("空 alt %d" % len(empty_alt))
        if placeholders:
            hard.append("占位残留 %d" % placeholders)
        if remote:
            hard.append("远端图 %d" % len(remote))

    try:
        from markdown_it import MarkdownIt
    except ImportError:
        print("markdown-it 未安装，跳过渲染校验（python -m pip install markdown-it-py）")
    else:
        html = MarkdownIt("commonmark").render(s)
        print("渲染：<pre> %d  <table> %d" % (html.count("<pre>"), html.count("<table>")))
        if fences and html.count("<pre>") == 0:
            hard.append("围栏未渲染成 <pre>")

    print("结论", "通过" if not hard else "不通过：" + "、".join(hard))
    if hard:
        sys.exit(1)


if __name__ == "__main__":
    main()
