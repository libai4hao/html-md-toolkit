#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
富文本 HTML 转干净 Markdown（通用版，针对微信公众号等"另存为"页面）
===============================================================
用法：
  python html_to_md.py 文章.html
  python html_to_md.py 文章.html -o 文章.md --title "自定义标题" --verify --check-images
  python html_to_md.py 文章.html --images none --no-frontmatter --overwrite

依赖：beautifulsoup4、markdownify
  python -m pip install beautifulsoup4 markdownify

设计要点（详见同目录 SOP）：
  1. 先定位正文容器，再做任何处理，避免把页面外壳（导航/页脚）当正文
  2. <pre> 内的换行靠 <br>，必须先转成 "\\n"，否则代码会压成一行
  3. 图片真实地址在 data-src，必须写回 src，否则输出空链接 ![]()
  4. 页脚只能删"最内层"匹配块，删外层会把整篇正文一起删掉
  5. 只含行内文本的 <section> 要改名为 <p>，否则整段粘成一块
"""

import argparse
import json
import os
import re
import sys

try:
    from bs4 import BeautifulSoup
    from markdownify import MarkdownConverter
except ImportError:
    sys.exit("缺少依赖：请先执行 python -m pip install beautifulsoup4 markdownify")

BLOCK_TAGS = {
    "p", "div", "ul", "ol", "li", "table", "tr", "td", "th", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "section",
    "figure", "hr", "dl",
}
DROP_TAGS = ["script", "style", "iframe", "noscript", "svg", "form", "button"]
IMG_ATTRS = ("data-src", "data-original", "src")


# ---------------------------------------------------------------- 基础读写
def read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise SystemExit("无法识别文件编码（试过 utf-8 / gb18030）：" + path)


def meta(soup, *keys):
    for k in keys:
        tag = soup.find("meta", attrs={"property": k}) or soup.find("meta", attrs={"name": k})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


# ---------------------------------------------------------------- 正文定位
def find_content(soup, content_id=None):
    if content_id:
        node = soup.find(id=content_id)
        if node:
            return node, "id=%s" % content_id
    for cls in ("rich_media_content", "article-content", "article", "post-content",
                "content", "main-content"):
        node = soup.find(class_=cls)
        if node:
            return node, "class=%s" % cls
    for name in ("article", "main"):
        node = soup.find(name)
        if node and len(node.get_text(strip=True)) > 200:
            return node, "tag=%s" % name
    # 兜底：取正文最长的 div
    best, best_len = None, -1
    for div in soup.find_all("div"):
        t = len(div.get_text(strip=True))
        if t > best_len:
            best, best_len = div, t
    if best is None:
        raise SystemExit("未能定位正文容器，请用 --content-id 指定（如 js_content）")
    return best, "最长文本 div（%d 字）" % best_len


# ---------------------------------------------------------------- 页脚清理
def kill_footer(content, keywords, tail_ratio=0.75):
    """只删文档尾部命中关键词的最内层块，重复执行直到清干净。"""
    removed = []
    while True:
        blocks = content.find_all(["section", "div", "pre", "footer", "aside"])
        total = len(blocks)
        if total == 0:
            break
        start = int(total * tail_ratio)
        hit = None
        for idx, blk in enumerate(blocks):
            if idx < start:
                continue
            txt = blk.get_text(" ", strip=True)
            if any(k in txt for k in keywords):
                hit = blk          # 保留最后一个（最内层）
        if hit is None:
            break
        removed.append(hit.get_text(" ", strip=True)[:40])
        hit.decompose()
    return removed


# ---------------------------------------------------------------- 结构修复
def fix_pre(content):
    """<pre> 里用 <br>/<p> 断行，必须先还原为 \\n 再取文本。"""
    count = 0
    for pre in content.find_all("pre"):
        count += 1
        for br in pre.find_all("br"):
            br.replace_with("\n")
        for p in pre.find_all("p"):
            p.insert_after("\n")
            p.unwrap()
    return count


def normalize_inline_sections(content):
    """只含行内内容（内部没有块级标签）的 section 改名为 p。"""
    n = 0
    for sec in content.find_all("section"):
        if not [k for k in sec.children if getattr(k, "name", None) in BLOCK_TAGS]:
            sec.name = "p"
            n += 1
    return n


def prev_text_alt(el, root=None, limit=30):
    """取图片之前最近的一段文字作 alt；不跨越正文容器边界，避免抓到页面外壳文案。"""
    cur = el
    for _ in range(4):
        sib = cur.find_previous_sibling()
        while sib is not None:
            if getattr(sib, "name", None) in ("pre", "table", "ul", "ol"):
                sib = sib.find_previous_sibling()
                continue
            t = re.sub(r"\s+", " ", sib.get_text(" ", strip=True))
            if t:
                t = t.strip("：:，,。.！!？?、（）()【】[]\"'“”‘’ ")
                return t[:limit]
            sib = sib.find_previous_sibling()
        parent = cur.parent
        if parent is None or parent is root:
            break
        cur = parent
    return ""


def fix_images(content, mode, auto_alt, cover_url):
    kept, dropped = [], {"gif": 0, "cover": 0, "other": 0}
    for img in content.find_all("img"):
        src = ""
        for attr in IMG_ATTRS:
            if img.get(attr):
                src = img[attr].strip()
                break
        low = src.lower()
        if mode == "none" or not src:
            img.decompose()
            dropped["other"] += 1
            continue
        if mode == "content" and ("mmbiz_gif" in low or low.split("?")[0].endswith(".gif")):
            img.decompose()
            dropped["gif"] += 1
            continue
        if mode == "content" and cover_url and src.split("?")[0] == cover_url.split("?")[0]:
            img.decompose()
            dropped["cover"] += 1
            continue
        # content 模式：正文之前没有任何文字上下文的图，视为头图/公众号名片
        if mode == "content" and not img.get("alt") and not prev_text_alt(img, content):
            img.decompose()
            dropped["cover"] += 1
            continue
        img["src"] = src
        img.attrs.pop("data-src", None)
        if not img.get("alt") and auto_alt:
            img["alt"] = prev_text_alt(img, content)
        kept.append(src)
    return kept, dropped


# ---------------------------------------------------------------- 转换与后处理
class CleanConverter(MarkdownConverter):
    def convert_pre(self, el, text, parent_tags):
        raw = el.get_text()
        lang = "sql" if re.search(r"CREATE\s+TABLE|SELECT\s", raw, re.I) and "mysql>" not in raw else "text"
        lines = [ln.rstrip() for ln in raw.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n\n```%s\n%s\n```\n\n" % (lang, "\n".join(lines))


def postprocess(md):
    md = md.replace("\xa0", " ")
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = re.sub(r"(\d)(rows? in set)", r"\1 \2", md)      # mysql 客户端输出常见粘连：1row in set
    # 只修正文行里成对的残留反引号，绝对不能碰 ``` 围栏
    fixed = []
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            fixed.append(line)
        else:
            fixed.append(re.sub(r"``(?!`)", "`", line))
    return "\n".join(fixed).strip()


YAML_TRICKY = set(':#{}[],&*!|>\'"%@`+-?')
YAML_BOOLISH = {"true", "false", "yes", "no", "on", "off", "null", "y", "n"}


def yq(s):
    """YAML 标量按需加引号：标题里带冒号/井号/引号时，不加引号会让 frontmatter 解析失败。"""
    s = "" if s is None else str(s).strip()
    if not s:
        return '""'
    need = (s[0] in YAML_TRICKY or ": " in s or " #" in s or s.endswith(":")
            or s.lower() in YAML_BOOLISH or re.search(r'[\n\r"\\]', s))
    if need:
        return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return s


def build_frontmatter(title, author, source, url):
    if not title:
        return ""
    lines = ["---", "title: %s" % yq(title)]
    if author:
        lines.append("author: %s" % yq(author))
    if source:
        lines.append("source: %s" % yq(source))
    if url:
        lines.append("url: %s" % yq(url))
    lines.append("---")
    return "\n".join(lines) + "\n\n# %s\n\n" % title


# ---------------------------------------------------------------- 校验
def verify(original_text, md_text, check_images=False, images=None):
    def squash(s):
        return re.sub(r"[`#>*\-\s]", "", s)

    orig_lines = [l for l in original_text.split("\n") if l.strip()]
    md_squashed = squash(md_text)
    missed = [l for l in orig_lines if squash(l) and squash(l) not in md_squashed]
    report = {"原文非空行": len(orig_lines), "未匹配行": len(missed), "缺失样例": [m[:60] for m in missed[:5]]}

    suspicious = [l for l in md_text.split("\n") if "``" in l and not l.lstrip().startswith("```")]
    report["可疑反引号行"] = len(suspicious)

    if check_images and images:
        import urllib.request
        ok = 0
        for u in images:
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    if r.status == 200:
                        ok += 1
            except Exception:
                pass
        report["图片可访问"] = "%d/%d" % (ok, len(images))
    return report


# ---------------------------------------------------------------- 主流程
def convert(src, out, args):
    html, enc = read_text(src)
    soup = BeautifulSoup(html, "html.parser")
    content, how = find_content(soup, args.content_id)

    for tag in content.find_all(DROP_TAGS):
        tag.decompose()

    title = args.title or meta(soup, "og:title", "twitter:title")
    author = args.author or meta(soup, "author", "og:article:author", "twitter:creator")
    url = args.url or meta(soup, "og:url")
    source = args.source or meta(soup, "og:site_name")
    cover = meta(soup, "og:image")

    footer_hits = kill_footer(content, [k for k in args.footer_keywords.split(",") if k], args.footer_ratio)
    pre_count = fix_pre(content)
    imgs, dropped = (fix_images(content, args.images, args.alt == "auto", cover)
                     if args.images != "none" else ([], {"gif": 0, "cover": 0, "other": 0}))
    if args.images == "none":
        for img in content.find_all("img"):
            img.decompose()
    sec_count = normalize_inline_sections(content)

    conv = CleanConverter(heading_style="ATX", bullets="-", strong_em_symbol="*",
                          escape_asterisks=False, escape_underscores=False, escape_misc=False)
    body = postprocess(conv.convert_soup(content))

    structure = {
        "h1": len(re.findall(r"(?m)^# ", body)),
        "h2": len(re.findall(r"(?m)^## ", body)),
        "h3": len(re.findall(r"(?m)^### ", body)),
        "列表": len(re.findall(r"(?m)^(?:- |\d+\. )", body)),
        "代码块": body.count("```") // 2,
        "图片": len(re.findall(r"!\[[^\]]*\]\(", body)),
    }

    md = body
    if not args.no_frontmatter and title:
        md = build_frontmatter(title, author, source, url) + body + "\n"

    original_text = content.get_text("\n")
    if args.verify or args.check_images:
        rep = verify(original_text, body, args.check_images, imgs)
    else:
        rep = None

    info = {
        "源文件": src,
        "编码": enc,
        "正文容器": how,
        "页脚移除": footer_hits or "无",
        "代码块": pre_count,
        "图片保留/丢弃": "%d / %d（gif %d，封面 %d）" % (len(imgs), sum(dropped.values()), dropped["gif"], dropped["cover"]),
        "section→p": sec_count,
        "结构": structure,
        "校验": rep,
        "输出": None if args.dry_run else out,
    }
    return md, info


def main():
    ap = argparse.ArgumentParser(description="富文本 HTML 转干净 Markdown")
    ap.add_argument("input", help="源 HTML 文件")
    ap.add_argument("-o", "--output", help="输出 md（默认同名 .md）")
    ap.add_argument("--title", help="标题，默认取 og:title")
    ap.add_argument("--author", help="作者，默认取 meta author")
    ap.add_argument("--source", help="来源栏目，默认取 og:site_name")
    ap.add_argument("--url", help="原文链接，默认取 og:url")
    ap.add_argument("--content-id", default="js_content", help="正文容器 id（默认 js_content，找不到会自动兜底）")
    ap.add_argument("--footer-keywords", default="如有文章对你有帮助,关注公众号,长按识别,点击关注,点赞是最大的支持",
                    help="页脚关键词，逗号分隔")
    ap.add_argument("--footer-ratio", type=float, default=0.75, help="只在文档尾部该比例之后找页脚（默认 0.75）")
    ap.add_argument("--images", choices=["content", "all", "none"], default="content",
                    help="图片策略：content=保留内容图丢 gif/封面；all=全保留；none=全丢")
    ap.add_argument("--alt", choices=["auto", "none"], default="auto", help="图片 alt 是否用前一段文字自动生成")
    ap.add_argument("--no-frontmatter", action="store_true", help="不生成 YAML 头与 H1")
    ap.add_argument("--verify", action="store_true", help="逐行核对原文覆盖率")
    ap.add_argument("--check-images", action="store_true", help="校验图片 URL 是否可访问")
    ap.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的输出")
    ap.add_argument("--dry-run", action="store_true", help="只输出统计，不写文件")
    args = ap.parse_args()

    out = args.output or os.path.splitext(args.input)[0] + ".md"
    if os.path.exists(out) and not (args.overwrite or args.dry_run):
        sys.exit("目标已存在，加 --overwrite 才会覆盖：%s" % out)

    md, info = convert(args.input, out, args)

    if not args.dry_run:
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(md + "\n")

    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
