#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信公众号"图片型图文消息"抓取与 Markdown 骨架生成
===========================================================
适用：mp.weixin.qq.com 上那种"17 张图讲透 XX"的图文消息——正文不在 #js_content 里，
而是 17 张 1080x1440 的图，挂在 window.cgiDataNew.picture_page_info_list 里，靠 JS 渲染。

用法：
  python wechat_picture_page.py "https://mp.weixin.qq.com/s/xxxx" --out "笔记.md"
  python wechat_picture_page.py rendered.html --out "笔记.md"          # 已经渲染好的 DOM
  python wechat_picture_page.py "URL" --drop "1,17" --overwrite        # 丢掉封面与关注引导卡

做的事：
  1. URL 输入先用 Edge/Chrome 无头 --dump-dom 渲染出真实 DOM（正文是 JS 填进去的，curl 拿到的是空壳）
  2. 按"顶层对象"解析 picture_page_info_list，取出每张卡的 cdn_url / 宽高 / is_qr_code
     （直接正则抓 cdn_url 会把 live_photo 等嵌套缩略图一起抓进来，17 张会变成 35 张）
  3. 下载图片到 <md 目录>/assets/<md 名>/NN.jpg，Markdown 用相对路径引用，离线可读
  4. 生成带 YAML 头、导语（取 og:description）、每卡一节占位标题的骨架 md + manifest.json

脚本到骨架为止：图里的文字要人（视觉模型）逐张转录进占位小节，这是唯一不能机械化的一步。
渲染与解析的坑点见同目录 SOP《富文本HTML转Markdown》§9。
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.40(0x1800282a) NetType/WIFI Language/zh_CN")
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


# ---------------------------------------------------------------- 渲染
def find_browser(explicit=None):
    if explicit:
        return explicit
    for p in BROWSERS:
        if os.path.exists(p):
            return p
    return None


def render(url, browser_path, budget=12000):
    """无头浏览器渲染后 dump 整个 DOM。临时 profile 无论成败都要删。"""
    profile = tempfile.mkdtemp(prefix="wxpic_")
    cmd = [browser_path, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--user-data-dir=" + profile, "--virtual-time-budget=%d" % budget, "--dump-dom", url]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    html = proc.stdout.decode("utf-8", errors="replace")
    if len(html) < 20000:
        err = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise SystemExit("渲染结果过短（%d 字节，退出码 %s），浏览器可能被拦、参数不支持或页面未加载完：%s\n%s"
                         % (len(html), proc.returncode, url, err[-3:] if err else ""))
    return html, "无头浏览器 dump-dom（virtual-time-budget=%ds）" % int(budget / 1000)


# ---------------------------------------------------------------- 解析
def meta(html, *keys):
    for k in keys:
        m = re.search(r'<meta[^>]+(?:property|name)="%s"[^>]+content="([^"]*)"' % re.escape(k), html)
        if m:
            return m.group(1).strip()
        m = re.search(r'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="%s"' % re.escape(k), html)
        if m:
            return m.group(1).strip()
    return ""


def cut_block(html, key="picture_page_info_list"):
    """取 picture_page_info_list 的数组字面量（括号配平，跳过字符串里的括号）。"""
    m = re.search(re.escape(key) + r"[\"']?\s*:\s*\[", html)
    if not m:
        raise SystemExit("页面里没有 picture_page_info_list —— 不是图片型图文消息，请改用 html_to_md.py")
    start = html.index("[", m.start())
    depth, quote, prev = 0, None, ""
    for i in range(start, len(html)):
        ch = html[i]
        if quote:
            if ch == quote and prev != "\\":
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
        prev = ch
    raise SystemExit("picture_page_info_list 数组没闭合，页面可能被截断")


def split_objects(block):
    """把数组字面量切成顶层对象（跳过嵌套 {}，字符串里的花括号不算）。"""
    objs, depth, cur, quote, prev = [], 0, None, None, ""
    for ch in block[1:-1]:
        if quote:
            if cur is not None:
                cur.append(ch)
            if ch == quote and prev != "\\":
                quote = None
        elif ch in "\"'":
            quote = ch
            if cur is not None:
                cur.append(ch)
        elif ch == "{":
            if depth == 0:
                cur = []
            depth += 1
            if cur is not None:
                cur.append(ch)
        elif ch == "}":
            if cur is not None:
                cur.append(ch)
            depth -= 1
            if depth == 0 and cur is not None:
                objs.append("".join(cur))
                cur = None
        elif depth > 0 and cur is not None:
            cur.append(ch)
        prev = ch
    return objs


def strip_nested(obj):
    """去掉对象里的嵌套子对象，只留顶层键值，避免 live_photo 里的 cdn_url 抢先命中。"""
    body = obj[1:-1] if obj.startswith("{") and obj.endswith("}") else obj
    out, depth, quote, prev = [], 0, None, ""
    for ch in body:
        if quote:
            if depth == 0:
                out.append(ch)
            if ch == quote and prev != "\\":
                quote = None
        elif ch in "\"'":
            quote = ch
            if depth == 0:
                out.append(ch)
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
        prev = ch
    return "".join(out)


def parse_cards(html):
    cards = []
    for n, o in enumerate(split_objects(cut_block(html)), 1):
        top = strip_nested(o)
        url = re.search(r"cdn_url:\s*'([^']+)'", top)
        w = re.search(r"width:\s*'(\d+)'", top)
        h = re.search(r"height:\s*'(\d+)'", top)
        qr = re.search(r"is_qr_code:\s*'(\d+)'", top)
        ct = re.search(r"create_time:\s*'([^']+)'", top)
        if not url:
            continue
        cards.append({
            "n": n,
            "url": url.group(1),
            "width": int(w.group(1)) if w else 0,
            "height": int(h.group(1)) if h else 0,
            "qr": bool(qr and qr.group(1) == "1"),
            "create_time": ct.group(1) if ct else "",
        })
    return cards


def page_info(html):
    info = {
        "title": meta(html, "og:title"),
        "url": meta(html, "og:url"),
        "site": meta(html, "og:site_name"),
        "description": meta(html, "og:description", "description"),
    }
    # 公众号昵称：id=js_wx_follow_nickname 的 div 内嵌子元素，正则抓会抓到脚本里的同名变量
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        sys.exit("缺少依赖：python -m pip install beautifulsoup4")
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find(id="js_wx_follow_nickname")
    info["nickname"] = node.get_text(strip=True) if node else ""
    if not info["nickname"]:
        info["nickname"] = meta(html, "og:article:author", "author")
    m = re.search(r"album_id: '[^']+',\s*\n?\s*title: '([^']+)'", html)
    info["album"] = m.group(1) if m else ""
    return info


def publish_time(html):
    """发布时间在 cgiDataNew 根对象上（不在每张卡片里）。"""
    m = re.search(r"create_time:\s*'(\d{4}-\d{2}-\d{2})", html)
    return m.group(1) if m else ""


# ---------------------------------------------------------------- 下载
def download(cards, adir, keep_remote=False, timeout=30):
    if keep_remote:
        for c in cards:
            c["path"] = None
        return cards
    os.makedirs(adir, exist_ok=True)
    opener = urllib.request.build_opener()
    opener.addheaders = [("User-Agent", UA), ("Referer", "https://mp.weixin.qq.com/")]
    for c in cards:
        ext = "png" if "wx_fmt=png" in c["url"] else "jpg"
        path = os.path.join(adir, "%02d.%s" % (c["n"], ext))
        try:
            with opener.open(c["url"], timeout=timeout) as r:
                data = r.read()
            if len(data) < 1000:
                raise ValueError("响应过小，可能被拦")
            with open(path, "wb") as f:
                f.write(data)
            c["path"], c["bytes"] = path, len(data)
        except Exception as e:
            c["path"], c["bytes"], c["error"] = None, 0, str(e)
    return cards


# ---------------------------------------------------------------- Markdown 骨架
ORDERED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"


def split_desc(desc):
    """og:description 里用字面量 \\x0a 分行，逐行判定段落 / 项目符号 / 序号 / 话题标签。"""
    lines = [p.strip() for p in re.split(r"\\x0a|\n", desc or "") if p.strip()]
    blocks, tags = [], []
    for l in lines:
        if re.fullmatch(r"(#[^#\s]+\s*)+", l):
            tags = re.findall(r"#([^#\s]+)", l)
            continue
        if l.startswith(("✅", "- ", "•")):
            blocks.append(("bullet", l.lstrip("✅-•· ").strip()))
        elif l[0] in ORDERED:
            blocks.append(("ordered", l[1:].strip()))
        else:
            blocks.append(("para", l))
    return blocks, tags


def render_blocks(blocks):
    out, i = [], 0
    while i < len(blocks):
        kind, text = blocks[i]
        if kind in ("bullet", "ordered"):
            j = i
            while j < len(blocks) and blocks[j][0] == kind:
                j += 1
            for k, (_, t) in enumerate(blocks[i:j], 1):
                out.append(("- " if kind == "bullet" else "%d. " % k) + t)
            out.append("")
            i = j
        else:
            out.append(text)
            out.append("")
            i += 1
    return out


YAML_TRICKY = set(':#{}[],&*!|>\'"%@`+-?')
YAML_BOOLISH = {"true", "false", "yes", "no", "on", "off", "null", "y", "n"}


def yq(s):
    """YAML 标量按需加引号：含冒号、井号、引号、括号或以保留字符开头时不加引号会解析错。"""
    s = "" if s is None else str(s).strip()
    if not s:
        return '""'
    need = (s[0] in YAML_TRICKY or ": " in s or " #" in s or s.endswith(":")
            or s.lower() in YAML_BOOLISH or re.search(r'[\n\r"\\]', s))
    if need:
        return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return s


def build_md(info, cards, assets_rel, title, author, source, published, tags, blocks):
    fm = ["---", "title: %s" % yq(title)]
    if author:
        fm.append("author: %s" % yq(author))
    if source:
        fm.append("source: %s" % yq(source))
    if info["url"]:
        fm.append("url: %s" % yq(info["url"]))
    if published:
        fm.append("published: %s" % yq(published))
    fm.append("tags: [%s]" % ", ".join(yq(t) for t in tags))
    fm.append("---")
    out = ["\n".join(fm), "", "# %s" % title, ""]
    out.extend(render_blocks(blocks))

    for c in cards:
        out.append("## 卡片 %02d" % c["n"])
        out.append("")
        if c["path"]:
            rel = os.path.join(assets_rel, os.path.basename(c["path"])).replace(os.sep, "/")
            out.append("![](%s)" % rel)
        else:
            out.append("![](%s)" % c["url"])
        out.append("")
        out.append("<!-- 在这里转录图内文字；本卡原图见上方 -->")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="微信公众号图片型图文消息 → Markdown 骨架")
    ap.add_argument("input", help="文章 URL，或已渲染的 html 文件")
    ap.add_argument("-o", "--out", help="输出 md 路径（默认 <标题>.md，落在当前目录）")
    ap.add_argument("--browser", help="浏览器可执行文件路径，默认自动找 Edge/Chrome")
    ap.add_argument("--budget", type=int, default=12000, help="渲染等待预算，毫秒（默认 12000）")
    ap.add_argument("--drop", default="", help="要跳过的卡片序号，逗号分隔，如 1,17（封面/关注引导卡）")
    ap.add_argument("--keep-html", action="store_true", help="把渲染出的 DOM 存到输出目录旁，便于复查")
    ap.add_argument("--no-download", action="store_true", help="不下载图片，md 里直接引用微信 CDN 地址")
    ap.add_argument("--tags", default="", help="YAML tags，逗号分隔")
    ap.add_argument("--author", default="", help="覆盖作者/公众号名")
    ap.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的输出")
    ap.add_argument("--dry-run", action="store_true", help="只解析并打印报告，不写文件")
    args = ap.parse_args()

    is_url = args.input.lower().startswith(("http://", "https://"))
    if is_url:
        browser = find_browser(args.browser)
        if not browser:
            sys.exit("找不到 Edge/Chrome，请用 --browser 指定，或先把页面另存/渲染成 html 再传文件")
        html, how = render(args.input, browser, args.budget)
    else:
        with open(args.input, "rb") as f:
            raw = f.read()
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                html, how = raw.decode(enc), "本地 html（%s）" % enc
                break
            except UnicodeDecodeError:
                continue
        else:
            sys.exit("无法识别文件编码：" + args.input)

    info = page_info(html)
    cards = parse_cards(html)
    total_cards = len(cards)
    drop = {int(x) for x in args.drop.split(",") if x.strip().isdigit()}
    auto_drop = [c["n"] for c in cards if c["qr"] and c["n"] not in drop]
    kept = [c for c in cards if c["n"] not in drop and c["n"] not in auto_drop]
    title = info["title"] or os.path.splitext(os.path.basename(args.input))[0]
    out = args.out or re.sub(r'[\\/:*?"<>|]', "-", title) + ".md"

    if not args.dry_run and os.path.exists(out) and not args.overwrite:
        sys.exit("目标已存在，加 --overwrite 才会覆盖：%s" % out)

    md_dir = os.path.dirname(os.path.abspath(out))
    stem = os.path.splitext(os.path.basename(out))[0]
    assets_rel = os.path.join("assets", stem)
    if args.dry_run:
        for c in kept:
            c["path"] = None
        cards = kept
    else:
        cards = download(kept, os.path.join(md_dir, assets_rel), args.no_download)

    author = args.author or info["nickname"]
    source = "微信公众号「%s」" % info["nickname"] if info["nickname"] else info["site"]
    if info["album"]:
        source += "（%s）" % info["album"]
    published = publish_time(html)
    blocks, desc_tags = split_desc(info["description"])
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] or desc_tags

    md = build_md(info, cards, assets_rel, title, author, source, published, tags, blocks)

    failed = [] if (args.dry_run or args.no_download) else [c["n"] for c in cards if not c.get("path")]
    downloaded = sum(1 for c in cards if c.get("path"))
    if args.dry_run:
        download_state = "跳过（dry-run 未下载）"
    elif args.no_download:
        download_state = "跳过（--no-download，引用远端）"
    else:
        download_state = "%d/%d" % (downloaded, len(kept))
    report = {
        "输入": args.input,
        "获取方式": how,
        "标题": title,
        "作者": author,
        "栏目": info["album"],
        "发布": published,
        "卡片总数": total_cards,
        "保留": len(kept),
        "跳过": sorted(drop),
        "自动跳过（二维码）": auto_drop,
        "图片下载": download_state,
        "失败": failed,
        "资产目录": None if (args.no_download or args.dry_run) else os.path.join(md_dir, assets_rel),
        "输出": None if args.dry_run else out,
        "下一步": "逐张读图转录到各 '## 卡片 NN' 小节，再把占位标题改成卡片真实标题",
    }

    if not args.dry_run:
        os.makedirs(md_dir, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
        if args.keep_html:
            with open(os.path.splitext(out)[0] + ".rendered.html", "w", encoding="utf-8") as f:
                f.write(html)
        with open(os.path.splitext(out)[0] + ".manifest.json", "w", encoding="utf-8") as f:
            json.dump({"info": info, "cards": cards, "auto_drop": auto_drop}, f, ensure_ascii=False, indent=1)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failed:
        sys.exit(2)


if __name__ == "__main__":
    main()
