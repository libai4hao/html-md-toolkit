# -*- coding: utf-8 -*-
"""HTML → 标准 Markdown 去广告工具（单站点版式适配器）。

白名单优先 + 多形态兜底：白名单零命中时按形态（问答 / 表单 / 通用文章 / 知乎搜索结果）抽取正文，
支持 `.mht` / `.mhtml` 容器解包。

三条不变原则：
  * 不脑补：文件名与内容不符时只按实际内容转换，并在注记里如实说明；
  * 白名单优先：只认 CSS class 命中的结构化区块，其余界面上的一切视为噪声丢弃；
  * 可复现：同一源文件再跑一次产出等价，默认不写盘，要写盘先备份。

用法：见 README.md。零第三方依赖，仅用 Python 标准库；坑位记录见 docs/PITFALLS-形态兜底.md。
"""

import argparse
import email
import email.policy
import html.parser
import os
import re
import shutil
import sys
import urllib.parse

# ---------------------------------------------------------------------------
# 过滤规则（换站点时重探 DOM，改这里的常量即可；不做多站点 profile —— YAGNI）
# ---------------------------------------------------------------------------
SECTION_CLASS = 'home-wrap-box__title'
RECENT_ITEM_CLASS = 'recent-carouse-list__item'
REPLAY_ITEM_CLASS = 'wrap-box-list__item'

FIELD_MAP = {
    'item-head-content__title': 'head_title',
    'item-head-content__info': 'speaker',
    'item-head-content__time': 'head_time',
    'list-item-content__title': 'content_title',
    'list-item-content__time': 'content_time',
    'box-list-item__title': 'r_title',
    'box-list-item__desc': 'r_desc',
    'box-list-item__time': 'r_time',
}
ITEM_FIELDS = tuple(FIELD_MAP.values())

DROP_TAGS = {'script', 'style', 'noscript'}
DROP_TEXT_HINTS = ('精品课', '二维码', '公众号', '微信小程序', '商务合作',
                   'ICP', '观看人数', '直播回放')

TOPIC_KEYWORDS = ('AQS', 'AbstractQueuedSynchronizer', 'ReentrantLock', '锁',
                  '队列', '线程', '同步器', 'Condition', '公平', 'Semaphore',
                  'CountDownLatch')
FILENAME_HINTS = TOPIC_KEYWORDS + ('并发', '专题', '底层')

RESIDUAL_MARKERS = ('<script', '<style', '<div', '</div', 'data-v-',
                    '<img', '<span', '<a ', '<br', '<p>', '<table',
                    '<ul', '<li', 'onclick=', 'window.__NUXT__')

_VOID_TAGS = {'br', 'img', 'input', 'meta', 'link', 'hr', 'source', 'area',
              'base', 'col', 'embed', 'param', 'track', 'wbr'}

# 斜杠留白时要豁免的 URL（http/https/ftp/file 与 www. 开头）。
_URL_RE = re.compile(r'(?:[a-zA-Z][a-zA-Z0-9+.-]*://|www\.)[^\s|]+')


# ---------------------------------------------------------------------------
# 纯函数：编码检测
# ---------------------------------------------------------------------------
def detect_decode(raw):
    """返回 (text|None, codec)。UTF-8（含 BOM）优先 → gb18030 兜底 → not-text。

    NUL 字节视为二进制文件，不参与文本解码（对齐 encoding-fix 的判定思想）。
    """
    if b'\x00' in raw:
        return None, 'not-text'
    if raw.startswith(b'\xef\xbb\xbf'):
        raw = raw[3:]
    for codec in ('utf-8', 'gb18030'):
        try:
            return raw.decode(codec), codec
        except UnicodeDecodeError:
            continue
    return None, 'not-text'


# ---------------------------------------------------------------------------
# 纯函数：MHTML（.mht / .mhtml）识别与解包
# ---------------------------------------------------------------------------
def is_mhtml(raw):
    """MIME multipart 容器特征：有 MIME-Version 且有 Content-Type: multipart。"""
    head = raw[:4096].decode('latin-1', 'replace').lower()
    return 'mime-version' in head and 'content-type: multipart' in head


def unpack_mhtml(raw):
    """用标准库 email 解析 MHTML，返回首个 text/html 部件解码后的字符串。

    找不到 html 部件（或根本不是 MIME）时返回 ''，由调用方回退到普通解码。
    """
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception:
        return ''
    if not msg.is_multipart():
        return ''
    for part in msg.walk():
        if part.get_content_type() != 'text/html':
            continue
        payload = part.get_payload(decode=True) or b''
        charset = part.get_content_charset() or 'utf-8'
        try:
            return payload.decode(charset, 'replace')
        except LookupError:
            return payload.decode('utf-8', 'replace')
    return ''


# ---------------------------------------------------------------------------
# 纯函数：文件名 ↔ 内容一致性预检
# ---------------------------------------------------------------------------
def _visible_text(html):
    """去掉 script/style 片段与全部标签，只留可见文本（用于关键词计数）。"""
    cleaned = re.sub(r'<script\b[^>]*>.*?</script\s*>', ' ', html,
                     flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r'<style\b[^>]*>.*?</style\s*>', ' ', cleaned,
                     flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
    return cleaned


def consistency_check(filename, html):
    """文件名暗示专题但可见正文命中 0 个关键词 → mismatch=True（告警但不阻断）。

    CSS/JS 里的 Lock / Node / CAS 是假命中，已被 _visible_text 剥掉。
    返回 {'mismatch': bool, 'warn': str, 'keyword_hits': int}。
    """
    visible = _visible_text(html)
    keyword_hits = sum(visible.count(k) for k in TOPIC_KEYWORDS)
    name_has_topic = any(h in filename for h in FILENAME_HINTS)
    mismatch = bool(name_has_topic and keyword_hits == 0)
    warn = ('文件名与内容不符：文件名暗示专题内容，但可见正文命中 0 个专题关键词；'
            '按源文件实际内容转换，不脑补正文。') if mismatch else ''
    return {'mismatch': mismatch, 'warn': warn, 'keyword_hits': keyword_hits}


# ---------------------------------------------------------------------------
# 纯函数：DOM 白名单提取（stdlib html.parser，流式、无依赖）
# ---------------------------------------------------------------------------
def _text(pieces):
    """拼接文本片段、折叠空白、去首尾。"""
    return re.sub(r'\s+', ' ', ''.join(pieces)).strip()


def _clean_time(raw):
    """去掉「直播时间：」前缀，并在日期与时刻之间补一个空格。"""
    s = re.sub(r'直播时间\s*[：:]\s*', '', raw)
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(r'(\d{1,2}月\d{1,2}日)\s*(\d{1,2}:\d{2})', r'\1 \2', s)
    return s.strip()


class _SectionParser(html.parser.HTMLParser):
    """按 class 白名单收集两张表；其余文本（导航/页脚/角标）一律不收。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []            # [(tag, field, item_kind)]
        self.page_title = []
        self.section_titles = []
        self.recent = []
        self.replay = []
        self.categories = []
        self._cur = None           # 当前条目的 {field: [text, ...]}
        self._cur_kind = None
        self._section_buf = []

    @staticmethod
    def _classes(attrs):
        for key, value in attrs:
            if key == 'class' and value:
                return set(value.split())
        return set()

    def _field(self, tag, classes):
        if tag == 'title':
            return 'page_title'
        if SECTION_CLASS in classes:
            return 'section_title'
        if RECENT_ITEM_CLASS in classes:
            return '__recent_item__'
        if REPLAY_ITEM_CLASS in classes:
            return '__replay_item__'
        for cls, key in FIELD_MAP.items():
            if cls in classes:
                return key
        return None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        field = self._field(tag, self._classes(attrs))
        if tag in DROP_TAGS:
            field = None
        item = None
        if field == '__recent_item__':
            item, field = ('recent', {f: [] for f in ITEM_FIELDS}), None
        elif field == '__replay_item__':
            item, field = ('replay', {f: [] for f in ITEM_FIELDS}), None
        elif field == 'section_title':
            self._section_buf = []
        # item 跟随栈帧保存：嵌套条目各持一个收集器，闭合时只 finalize 自己那一层，
        # 避免单例 _cur 被内层覆盖导致外层条目静默丢行。
        self.stack.append((tag, field, item))

    def handle_startendtag(self, tag, attrs):
        # <br/> 之类自闭合标签不入栈，避免污染栈平衡。
        pass

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        idx = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                idx = i
                break
        if idx is None:
            return
        popped = self.stack[idx:]
        del self.stack[idx:]
        for _, field, item in reversed(popped):
            if item:
                self._finalize_item(item[0], item[1])
            elif field == 'section_title':
                title = _text(self._section_buf)
                if title:
                    self.section_titles.append(title)
                self._section_buf = []

    def handle_data(self, data):
        if any(t in DROP_TAGS for t, _, _ in self.stack):
            return
        field = None
        for _, f, _ in reversed(self.stack):
            if f:
                field = f
                break
        # 推广词只用于拦截「不属于任何白名单字段」的游离噪声节点。
        # 不能对白名单字段内的 data 做子串丢弃，否则主题里的「精品课：…」会被吞成空串。
        if field is None and any(h in data for h in DROP_TEXT_HINTS):
            return
        if field == 'page_title':
            self.page_title.append(data)
        elif field == 'section_title':
            self._section_buf.append(data)
        elif field:
            # 落到最近一层 item 收集器（内层优先），而不是全局单例。
            for _, _, item in reversed(self.stack):
                if item and field in item[1]:
                    item[1][field].append(data)
                    break

    def _finalize_item(self, kind, cur):
        get = lambda key: _text(cur.get(key, []))
        if kind == 'recent':
            head_title = get('head_title')
            speaker = get('speaker')
            head_time = _clean_time(get('head_time'))
            content_title = get('content_title')
            content_time = _clean_time(get('content_time'))
            if head_title:
                # 有 head 的两场：主题/讲师/时间取自 head，content_title 是课程分类。
                topic = head_title
                spk = speaker or '—'
                tm = head_time or content_time
                category = content_title
            else:
                # 无 head 的两场：主题/时间取自 content，讲师缺省。
                topic, spk, tm, category = content_title, '—', content_time, ''
            self.recent.append({'topic': topic, 'speaker': spk, 'time': tm})
            if category:
                self.categories.append(category)
        else:
            self.replay.append({'topic': get('r_title'),
                                'desc': get('r_desc'),
                                'time': _clean_time(get('r_time'))})

    def result(self):
        raw_title = _text(self.page_title)
        title = re.sub(r'\s*-\s*', ' · ', raw_title).strip() if raw_title else '公开课直播'
        return {'title': title,
                'section_titles': self.section_titles,
                'recent': self.recent,
                'replay': self.replay,
                'categories': self.categories}


def parse_sections(html):
    """把 HTML 解析成结构化数据（title / section_titles / recent / replay / categories）。"""
    parser = _SectionParser()
    parser.feed(html)
    parser.close()
    return parser.result()


# ---------------------------------------------------------------------------
# 通用兜底：白名单零命中时按形态抽取（问答 / 表单 / 文章）
#
# 只在 parse_sections 的两张表都为空时才被调用 —— 白名单路径必须先行，
# 否则白名单页面会被兜底逻辑接管，基准等价立刻破。
# ---------------------------------------------------------------------------
# 容器级噪声：命中即整棵子树丢弃（class 子串匹配）。
NOISE_CLASS_HINTS = (
    'out-sourcing-ad', 'recommend-', 'nav', 'footer', 'opera', 'good-bad',
    'zan_cai', 'show-all', 'more_btn', 'open-member', 'account-management',
    'search-wrap', 'search-box', 'ani-search', 'menu-ani',
    'top-picture-container', 'form-header-setting-group', 'related', 'rs-con',
    'csdn-toolbar', 'csdn-side-toolbar', 'toolbar-circle', 'toolbar_to_feed',
    # P2 延伸清理：评论区、推荐列表、操作栏、页脚、相关文章
    'comment-list', 'comment-box', 'comment-edit-box', 'new-comment',
    'recommend-item-box', 'recommend-recommend', 'recommend-loading',
    'recommend-end', 'blog-expert-recommend', 'hot-word',
    'toolbox-list', 'left-toolbox', 'more-toolbox',
    'Comments-container', 'CommentItemV2', 'NestComment',
    'RelatedCommodities', 'Footer-item',
    'sgui-recommend',
    'new-opt-box', 'reward-box', 'qrcode-img-box', 'to-reward',
    'social-share', 'wechat-qrcode', 'share-poster', 'poster',
    'qr_code_pc', 'like_comment_primary',
    # v3：微信图文噪声（按 class 子串匹配，必须用 class 名而非 id）
    'skiptranslate', 'profile_container', 'rich_media_meta',
    # reward_area 是赞赏浮层根容器；不整棵丢弃时，段落合并会把
    # 「微信扫一扫赞赏作者」+「赞赏」拼成整串，精确噪声匹配随之失效。
    'reward_area',
)

GENERIC_DROP_TAGS = {'script', 'style', 'noscript', 'nav', 'footer', 'aside',
                     'button'}

# 块级噪声文本：这些独立块是 JS 动态浮层（举报分类/打赏/扫码）生成、
# 无稳定 class，只能按整块文本精确匹配丢弃。只在 article 形态生效。
ARTICLE_BLOCK_NOISE_TEXTS = frozenset({
    '扫一扫，手机浏览', '扫一扫', '微信扫一扫', '举报', '打赏',
    '内容涉黄', '政治相关', '内容抄袭', '涉嫌广告', '内容侵权',
    '侮辱谩骂', '样式问题', '其他', '包含不实信息', '涉及个人隐私',
    '诽谤', '搬家样式', '博文样式',
    '点赞', '收藏', '分享', '手机看', '更多', '上一篇', '下一篇',
    '海报', '分享海报说明', '评论', '手机看',
    '发送到看一看', '发送中', '关注该公众号', '推荐',
    # 公众号头部信息 + 尾部赞赏/扫码浮层
    '微信号', '功能介绍', '微信扫一扫赞赏作者', '长按二维码向我转账',
    '人赞赏', '赞赏', '已发送', 'gh_123',
    '您的支持，就是我的动力', '您的支持就是我的动力',
    '受苹果公司新规定的影响，微信iOS版的出色功能被关闭，可通过二维码转账支持公众号。',
    '前往“发现”-“看一看”浏览“朋友在看”',
})

# v3：article 段落级噪声（正则）与知乎搜索页识别（正则）
_GH_RE = re.compile(r'^gh_[A-Za-z0-9]+$')
_ARTICLE_PROMO_RE = re.compile(
    r'不要忘记点赞|点击以下链接购买|欢迎关注微信公众号|推荐一本.*经典书')
_SAVED_FROM_RE = re.compile(
    r'<!--\s*saved from url=\(\d+\)(https?://[^\s>]+)', re.I)
_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)
_TAG_RE = re.compile(r'<[^>]+>')

_QUESTION_ID_RE = re.compile(r'<h1[^>]*\bid\s*=\s*["\']questionTitle["\']', re.I)
_CLASS_ATTR_RE = re.compile(r'class\s*=\s*["\']([^"\']*)["\']', re.I)


def _shape_of(html):
    """按确定性判据识别形态：qa → form → search → article（顺序即优先级）。"""
    if _QUESTION_ID_RE.search(html):
        return 'qa'
    tokens = [c.split() for c in _CLASS_ATTR_RE.findall(html)]
    flat = {t for c in tokens for t in c}
    if 'best-answer' in flat:
        return 'qa'
    n_question = sum(1 for c in tokens if c == ['question'])
    n_title = sum(1 for c in tokens if any(t.startswith('question-title') for t in c))
    if n_question >= 2 and n_title >= 2:
        return 'form'
    if _is_zhihu_search(html):
        return 'search'
    return 'article'


def _squash(s):
    return re.sub(r'\s+', ' ', s).strip()


def _title_text(html):
    m = _TITLE_RE.search(html)
    return _squash(_TAG_RE.sub(' ', m.group(1))) if m else ''


def _source_url(source):
    # 形参不能用 html：会遮蔽模块级 import html.parser 绑定的 html 包
    m = _SAVED_FROM_RE.search(source)
    return html.unescape(m.group(1)) if m else ''


def _is_zhihu_search(html):
    title = _title_text(html)
    url = _source_url(html)
    page_signal = ('搜索结果' in title and '知乎' in title) or '/search?' in url
    card_signal = len(re.findall(r'ContentItem-title', html)) >= 2
    return page_signal and card_signal


def _is_article_noise(text):
    """article 段落级噪声统一判定：_article_blocks 与 result().paragraphs 共用。"""
    t = _squash(text)
    if not t:
        return True
    if t in ARTICLE_BLOCK_NOISE_TEXTS:
        return True
    if _GH_RE.match(t):
        return True
    return bool(_ARTICLE_PROMO_RE.search(t))


class _GenericParser(html.parser.HTMLParser):
    """栈式抽取器：每个栈帧带一个 role，闭合时按 role 收尾。"""

    def __init__(self, shape):
        super().__init__(convert_charrefs=True)
        self.shape = shape
        self.stack = []
        self.section = None
        self.question = []
        self.best = []
        self.others = []
        self.form_title = []
        self.form_subtitle = []
        self.fields = []
        self.heading = []
        self.paragraphs = []
        # P0/P1 新增：article 的有序块流，保留标题层级 / 列表 / 代码块顺序。
        self.blocks = []          # [{'type':...}, ...]，type ∈ heading/para/list/code
        self._list_stack = []     # 每层 {'ordered':bool,'items':[str,...]}

    @staticmethod
    def _classes(attrs):
        for key, value in attrs:
            if key == 'class' and value:
                return value.split()
        return []

    def _role(self, tag, classes, attrs):
        joined = ' '.join(classes)
        # 具体角色优先于噪声判定：表单标题本身就带 form-header 前缀。
        if tag == 'h1' and dict(attrs).get('id') == 'questionTitle':
            return 'qa_question'
        if 'form-header-title-content' in joined:
            return 'form_title'
        if 'form-header-subtitle' in joined:
            return 'form_subtitle'
        if tag == 'section':
            if 'out-sourcing-ad' in joined:
                return 'drop'
            if 'best-answer' in joined:
                return 'sec_best'
            if 'other-answer' in joined:
                return 'sec_others'
        if tag == 'li' and 'answer_item' in classes:
            return 'answer'
        if tag == 'p' and 'name' in classes:
            return 'author'
        if tag == 'h3':
            return 'answer_text'
        if tag == 'div' and classes == ['question']:
            return 'field'
        if 'question-title' in joined:
            return 'label'
        if 'question-subtitle' in joined:
            return 'note'
        if tag == 'textarea':
            return 'value'
        if 'required-span' in joined:
            return 'required'
        if tag == 'span' and 'icon-number' in joined:
            return 'no'
        if self.shape == 'article':
            if tag in ('h1', 'h2'):
                # 标题元素自身带噪音 class（如分享弹窗标题）→ 丢弃。
                if any(h in joined for h in NOISE_CLASS_HINTS):
                    return 'drop'
                return 'art_heading'
            if tag in ('h3', 'h4', 'h5', 'h6'):
                if any(h in joined for h in NOISE_CLASS_HINTS):
                    return 'drop'
                return 'art_subheading'
            if tag in ('p',):
                return 'art_para'
            if tag == 'li':
                return 'art_li'
            if tag in ('ul', 'ol'):
                return 'art_list'
            if tag == 'pre':
                return 'art_code'
        if any(h in joined for h in NOISE_CLASS_HINTS):
            return 'drop'
        return None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        classes = self._classes(attrs)
        role = self._role(tag, classes, attrs)
        if tag in GENERIC_DROP_TAGS or tag == 'header':
            role = 'drop'
        payload = None
        if role == 'answer':
            payload = {'author': [], 'text': []}
        elif role == 'field':
            payload = {'no': [], 'label': [], 'note': [], 'value': [],
                       'required': False}
        elif role == 'sec_best':
            self.section = 'best'
        elif role == 'sec_others':
            self.section = 'others'
        elif role == 'required' and self._field_frame():
            self._field_frame()[2]['required'] = True
        elif role == 'art_list' and self.shape == 'article':
            self._list_stack.append(
                {'ordered': (tag == 'ol'), 'items': []})
        elif role == 'art_code' and self.shape == 'article':
            payload = {'text': []}
        elif role == 'art_li':
            # li 文本落到最近一层列表；不在列表内的 li（异常结构）忽略。
            if not self._list_stack:
                role = None
        elif role in ('art_heading', 'art_subheading') and self.shape == 'article':
            # 记录标题层级，供渲染映射 #/##/###…
            payload = {'level': int(tag[1]) if tag[1].isdigit() else 1,
                       'text': []}
        elif role == 'art_para' and self.shape == 'article':
            # 单个 <p> 被双层 font/span 切成多个文本节点，先缓冲到 </p> 再合并。
            payload = {'text': []}
        self.stack.append((tag, role, payload))

    def handle_startendtag(self, tag, attrs):
        # 自闭合标签不进栈（<br/> <img/> 之类），保持栈平衡。
        pass

    def _field_frame(self):
        for frame in reversed(self.stack):
            if frame[1] == 'field':
                return frame
        return None

    def _answer_frame(self):
        for frame in reversed(self.stack):
            if frame[1] == 'answer':
                return frame
        return None

    def _list_frame(self):
        """返回当前最内层的列表收集器 dict（不在任何列表内时为 None）。"""
        return self._list_stack[-1] if self._list_stack else None

    def _code_frame(self):
        """返回当前最近的代码块栈帧（不在 pre 内时为 None）。

        代码常被 <span> 子节点包裹（语法高亮），文本落在 span 里而非 pre 自身，
        需要向上找到 pre 帧才能把 token 收集到同一个 code 块。
        """
        for frame in reversed(self.stack):
            if frame[1] == 'art_code':
                return frame
        return None

    def _frame_of(self, role):
        for frame in reversed(self.stack):
            if frame[1] == role:
                return frame
        return None

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        idx = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                idx = i
                break
        if idx is None:
            return
        if tag == 'li':
            # 微信代码块用 <li> 表达行边界（无字面 \n）；此时 pre 帧仍在栈内。
            code_frm = self._code_frame()
            if code_frm is not None and code_frm[2] is not None:
                code_frm[2]['text'].append('\n')
        popped = self.stack[idx:]
        del self.stack[idx:]
        for _, role, payload in reversed(popped):
            if role == 'answer' and payload:
                entry = {'author': _squash(''.join(payload['author'])),
                         'text': _squash(''.join(payload['text']))}
                (self.others if self.section == 'others' else self.best).append(entry)
            elif role == 'field' and payload:
                no = ''.join(payload['no']).strip().lstrip('0') or ''
                self.fields.append({'no': no,
                                    'label': _squash(''.join(payload['label'])),
                                    'note': _squash(''.join(payload['note'])),
                                    'value': _squash(''.join(payload['value'])),
                                    'required': payload['required']})
            elif role in ('sec_best', 'sec_others'):
                self.section = None
            elif role == 'art_list' and self.shape == 'article':
                # 闭合一层列表：出栈并作为一个 block 追加（保证嵌套顺序）。
                if self._list_stack:
                    lst = self._list_stack.pop()
                    if lst['items']:
                        self.blocks.append({'type': 'list', 'list': lst})
            elif role == 'art_code' and payload is not None:
                code = ''.join(payload['text'])
                self.blocks.append({'type': 'code', 'text': code})
            elif role == 'art_para' and payload is not None:
                # pre > ol > li > p 内的 <p> 文本已被 code 帧吃掉，缓冲为空 → 跳过。
                text = _squash(''.join(payload['text']))
                if text:
                    self.paragraphs.append(text)
                    self.blocks.append({'type': 'para', 'text': text})
            elif role == 'art_subheading' and payload is not None:
                text = _squash(''.join(payload['text']))
                if text:
                    self.blocks.append({'type': 'heading',
                                        'level': payload['level'],
                                        'text': text})
            elif role == 'art_heading' and payload is not None:
                text = _squash(''.join(payload['text']))
                if text:
                    self.heading.append(text)
                    # 标题去重：与首个 H1 相同的后续标题不再重复成块。
                    first = self.heading[0]
                    already = any(
                        b['type'] == 'heading' and b['text'] == text
                        for b in self.blocks)
                    if text == first and already:
                        pass
                    else:
                        self.blocks.append({'type': 'heading',
                                            'level': payload['level'],
                                            'text': text})

    def handle_data(self, data):
        if any(role == 'drop' for _, role, _ in self.stack):
            return
        # 语法高亮：代码 token 常被 <span> 包裹（span 自身 role=None），
        # 只要文本节点落在某个 <pre> 代码块祖先内，就归入该 code 块。
        if not data.strip() and self._code_frame() is not None:
            pass  # 空白也保留（代码缩进/换行有意义）
        code_frm = self._code_frame()
        if code_frm is not None and code_frm[2] is not None:
            code_frm[2]['text'].append(data)
            return
        for _, role, payload in reversed(self.stack):
            if role == 'qa_question':
                self.question.append(data)
                return
            if role == 'form_title':
                self.form_title.append(data)
                return
            if role == 'form_subtitle':
                self.form_subtitle.append(data)
                return
            if role == 'author':
                ans = self._answer_frame()
                if ans:
                    ans[2]['author'].append(data)
                return
            if role == 'answer_text':
                ans = self._answer_frame()
                if ans:
                    ans[2]['text'].append(data)
                return
            if role in ('label', 'note', 'value', 'no'):
                fld = self._field_frame()
                if fld and role in fld[2]:
                    fld[2][role].append(data)
                return
            if role in ('art_heading', 'art_subheading'):
                if payload is not None:
                    payload['text'].append(data)
                return
            if role == 'art_para':
                if payload is not None:
                    payload['text'].append(data)
                return
            if role == 'art_li':
                lst = self._list_frame()
                if lst is not None:
                    lst['items'].append(data)
                return
            if role == 'art_code':
                if payload is not None:
                    payload['text'].append(data)
                return
            if role in ('field', 'answer', 'required'):
                return

    def result(self):
        shape = self.shape
        if shape == 'qa':
            return {'shape': 'qa',
                    'qa': {'question': _squash(''.join(self.question)),
                           'best': self.best,
                           'others': self.others}}
        if shape == 'form':
            return {'shape': 'form',
                    'form': {'title': _squash(''.join(self.form_title)),
                             'subtitle': _squash(''.join(self.form_subtitle)),
                             'fields': self.fields}}

    def _article_blocks(self):
        """过滤块级噪声：独立的举报分类/打赏/扫码浮层块整块丢弃。"""
        out = []
        for b in self.blocks:
            t = b.get('type')
            if t == 'para':
                if _is_article_noise(b.get('text', '')):
                    continue
                out.append(b)
            elif t == 'list':
                items = b.get('list', {}).get('items', [])
                squashed = [_squash(x) for x in items]
                nonnoise = [x for x in squashed if not _is_article_noise(x)]
                # 全部条目都是噪声词（举报分类/操作栏）→ 整块丢
                if not nonnoise:
                    continue
                # 仅少数噪声词条目混入 → 保留并剔除这些条目
                if len(nonnoise) < len(squashed):
                    b = {'type': 'list',
                         'list': {'ordered': b.get('list', {}).get('ordered', False),
                                  'items': nonnoise}}
                out.append(b)
            else:
                out.append(b)
        return out

    def result(self):
        shape = self.shape
        if shape == 'qa':
            return {'shape': 'qa',
                    'qa': {'question': _squash(''.join(self.question)),
                           'best': self.best,
                           'others': self.others}}
        if shape == 'form':
            return {'shape': 'form',
                    'form': {'title': _squash(''.join(self.form_title)),
                             'subtitle': _squash(''.join(self.form_subtitle)),
                             'fields': self.fields}}
        return {'shape': 'article',
                'article': {'heading': _squash(''.join(self.heading)),
                            'paragraphs': [p for p in
                                           (_squash(x) for x in self.paragraphs)
                                           if not _is_article_noise(p)],
                            'blocks': self._article_blocks()}}


def parse_generic(html):
    """白名单零命中时的兜底抽取；形态由 _shape_of 确定性判定。"""
    shape = _shape_of(html)
    if shape == 'search':
        return _parse_zhihu_search(html)
    parser = _GenericParser(shape)
    parser.feed(html)
    parser.close()
    return parser.result()


class _ZhihuSearchParser(html.parser.HTMLParser):
    """白名单 zone 抽取知乎搜索结果卡：只收 title / summary / related 三类容器文本。

    zone 外的文本（导航 Tabs、按钮、页脚、头像、script/style 内容）一律不收。
    卡片边界用 div 深度计数：div.Card.SearchResult-Card 内含多层嵌套 div，
    必须等深度归零才收卡，不能见第一个 </div> 就收。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.related = []
        self.zone = None          # 'title' | 'summary' | 'related' | None
        self.card = None
        self._depth = 0
        self._rel_buf = None
        self._frames = []         # [(tag, zone_before, is_card, is_rel_li, in_card_div)]

    @staticmethod
    def _classes(attrs):
        for k, v in attrs:
            if k == 'class' and v:
                return v.split()
        return []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _VOID_TAGS:            # img/br/meta… 无 endtag，不能进栈
            return
        classes = self._classes(attrs)
        zone_before = self.zone
        is_card = is_rel_li = in_card_div = False
        if tag == 'div' and 'SearchResult-Card' in classes:
            self._depth = 1
            self.card = {'title': [], 'url': '', 'summary': []}
            is_card = True
        elif tag == 'div' and self._depth:
            self._depth += 1
            in_card_div = True
        if tag == 'h2' and 'ContentItem-title' in classes:
            self.zone = 'title'
        elif tag == 'span' and 'RichText' in classes:
            self.zone = 'summary'
        elif tag == 'div' and 'RelevantQuery' in classes:
            self.zone = 'related'
        elif self.zone == 'related' and tag == 'li':
            self._rel_buf = []
            is_rel_li = True
        if self.zone == 'title' and tag == 'a' and self.card is not None:
            href = dict(attrs).get('href') or ''
            if href:
                self.card['url'] = html.unescape(href)
        self._frames.append((tag, zone_before, is_card, is_rel_li, in_card_div))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_data(self, data):
        if self.zone == 'title' and self.card is not None:
            self.card['title'].append(data)
        elif self.zone == 'summary' and self.card is not None:
            self.card['summary'].append(data)
        elif self.zone == 'related' and self._rel_buf is not None:
            self._rel_buf.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if not self._frames or self._frames[-1][0] != tag:
            return
        _, zone_before, is_card, is_rel_li, in_card_div = self._frames.pop()
        if is_rel_li:
            text = _squash(''.join(self._rel_buf or []))
            if text:
                self.related.append(text)
            self._rel_buf = None
        if (is_card or in_card_div) and self._depth:
            self._depth -= 1
            if self._depth == 0 and self.card is not None:
                card = self.card
                title = _squash(''.join(card['title']))
                if title:            # 无标题的卡 = 相关搜索卡，跳过
                    self.items.append({'title': title,
                                       'url': card['url'],
                                       'summary': _squash(''.join(card['summary']))})
                self.card = None
        self.zone = zone_before


def _parse_zhihu_search(html):
    parser = _ZhihuSearchParser()
    parser.feed(html)
    parser.close()
    url = _source_url(html)
    query = ''
    if url:
        # parse_qs 自带 percent-decode（探针实测 q=2020%20内循环 → '2020 内循环'），
        # 不要再 unquote 一次，避免二次解码。
        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(url).query).get('q', [''])[0]
    if not query:
        query = _title_text(html).split(' - 搜索结果 - 知乎')[0]
        query = re.sub(r'^\(\d+ 封私信\s*/\s*\d+ 条消息\)\s*', '', query).strip()
    return {'shape': 'search',
            'search': {'query': query or '未知搜索词',
                       'source_url': url,
                       'items': parser.items,
                       'related': parser.related}}


# ---------------------------------------------------------------------------
# 纯函数：Markdown 渲染 + 残留检测
# ---------------------------------------------------------------------------
def _space_slashes(s):
    """把 `A/B` 规范成 `A / B`（与已交付 MD 的排版一致）。

    URL 必须整段豁免：`http://x.com/a/b` 若一起留白会变成 `http: / / x.com / a / b`，
    属于静默破坏数据。先用占位符把 URL 摘出来，处理完再换回。
    """
    saved = []

    def stash(match):
        saved.append(match.group(0))
        return '\x00#%d\x00' % (len(saved) - 1)

    s = _URL_RE.sub(stash, s)
    s = re.sub(r'\s*/\s*', ' / ', s)
    for i, url in enumerate(saved):
        s = s.replace('\x00#%d\x00' % i, url)
    return s


def escape_cell(s):
    """表格单元格：斜杠留白 → `|` 转义 → 折叠空白 → 去首尾。"""
    s = '' if s is None else str(s)
    s = _space_slashes(s).replace('|', r'\|')
    return re.sub(r'\s+', ' ', s).strip()


def render_markdown(data, source_label, note):
    """生成标准 Markdown：H1 + 引用注记 + 近期直播表 + 分类注记 + 往期回放表。"""
    titles = data.get('section_titles') or []
    recent_title = titles[0] if len(titles) > 0 else '近期直播'
    replay_title = titles[1] if len(titles) > 1 else '往期回放'

    lines = ['# ' + (data.get('title') or '公开课直播'),
             '',
             '> ' + (note or ('来源：' + source_label)),
             '',
             '## ' + recent_title,
             '',
             '| 主题 | 讲师 | 时间 |',
             '| --- | --- | --- |']
    for row in data.get('recent', []):
        lines.append('| {} | {} | {} |'.format(
            escape_cell(row.get('topic', '')),
            escape_cell(row.get('speaker', '')),
            escape_cell(row.get('time', ''))))
    lines.append('')

    categories = [c for c in data.get('categories', []) if c]
    if categories:
        if len(set(categories)) == 1:
            lines.append('> 前两场课程分类均为：' + _space_slashes(categories[0]).strip() + '。')
        else:
            lines.append('> 课程分类：' +
                         '；'.join(_space_slashes(c).strip() for c in categories) + '。')
        lines.append('')

    lines += ['## ' + replay_title,
              '',
              '| 主题 | 简介 | 时间 |',
              '| --- | --- | --- |']
    for row in data.get('replay', []):
        lines.append('| {} | {} | {} |'.format(
            escape_cell(row.get('topic', '')),
            escape_cell(row.get('desc', '')),
            escape_cell(row.get('time', ''))))
    return '\n'.join(lines) + '\n'


def _render_qa(data):
    qa = data['qa']
    lines = ['# ' + (qa.get('question') or '问答'), '']
    if qa.get('best'):
        lines += ['## 最佳答案', '']
        for entry in qa['best']:
            lines += _answer_block(entry, heading=None)
    if qa.get('others'):
        lines += ['## 其他回答', '']
        for entry in qa['others']:
            lines += _answer_block(entry, heading='### ')
    return '\n'.join(lines).rstrip('\n')


def _answer_block(entry, heading):
    lines = []
    text = entry.get('text') or ''
    if heading and entry.get('author'):
        lines += [heading + entry['author'], '']
    if text:
        lines += [text, '']
    return lines


def _render_form(data):
    form = data['form']
    lines = ['# ' + (form.get('title') or '表单'), '']
    if form.get('subtitle'):
        lines += ['> ' + form['subtitle'], '']
    lines += ['| 序号 | 字段 | 说明 | 已填值 |',
              '| --- | --- | --- | --- |']
    for f in form.get('fields', []):
        no = (f.get('no') or '') + ('*' if f.get('required') else '')
        lines.append('| {} | {} | {} | {} |'.format(
            escape_cell(no) or '—',
            escape_cell(f.get('label')) or '—',
            escape_cell(f.get('note')) or '—',
            escape_cell(f.get('value')) or '—'))
    return '\n'.join(lines)


def _render_article(data):
    art = data['article']
    h1 = art.get('heading') or '未命名文档'
    lines = ['# ' + h1, '']
    blocks = art.get('blocks') or []
    if blocks:
        # 有序块流：段落 / 标题（h2+）/ 列表 / 代码块，保留阅读顺序。
        for b in blocks:
            t = b['type']
            if t == 'para':
                text = _squash(b.get('text', ''))
                if text:
                    lines += [text, '']
            elif t == 'heading':
                text = b.get('text', '')
                level = b.get('level', 2)
                if text and b.get('text', '') != h1:
                    level = max(2, min(6, level))
                    lines += ['#' * level + ' ' + text, '']
            elif t == 'list':
                lst = b.get('list', {})
                ordered = lst.get('ordered', False)
                items = [i for i in (_squash(x) for x in lst.get('items', [])) if i]
                for idx, item in enumerate(items, 1):
                    marker = ('%d. ' % idx) if ordered else '- '
                    lines += [marker + item, '']
                if items:
                    lines += ['']
            elif t == 'code':
                code = b.get('text', '').strip('\n')
                if code.strip():
                    lines += ['```', code, '```', '']
    else:
        # 兜底：无块流时退回旧的段落列表渲染（向后兼容）。
        for p in art.get('paragraphs', []):
            lines += [p, '']
    return '\n'.join(lines).rstrip('\n')


def render_generic(data, note=None):
    """按形态渲染标准 Markdown（qa / form / article / search）。"""
    shape = data.get('shape')
    if shape == 'qa':
        body = _render_qa(data)
    elif shape == 'form':
        body = _render_form(data)
    elif shape == 'search':
        body = _render_search(data, note)
    else:
        body = _render_article(data)
    return body + '\n'


def residual_noise(md):
    """检测生成 MD 里的 HTML 残留；引用注记行（说明里会提广告词）跳过。"""
    hits = []
    for line in md.splitlines():
        if line.lstrip().startswith('>'):
            continue
        for marker in RESIDUAL_MARKERS:
            if marker in line and marker not in hits:
                hits.append(marker)
    return hits


# ---------------------------------------------------------------------------
# 编排 + CLI
# ---------------------------------------------------------------------------
def _build_note(filename, data, check):
    stem = os.path.splitext(filename)[0]
    parts = ['来源：`%s`。' % filename]
    if check['mismatch']:
        parts.append('文件名暗示「%s」专题，但源文件实际是「%s」列表页，'
                     '并不含任何 AQS / 并发编程正文（可见正文命中 0 个专题关键词）。'
                     % (stem, data.get('title') or '公开课直播'))
    parts.append('本文按源文件实际内容转换，并已去除导航菜单、课程推广角标（精品课）、'
                 '观看人数、二维码/公众号引导、分页控件、页脚版权、ICP 备案与商务合作信息等'
                 '广告与界面噪声。')
    return ''.join(parts)


def _search_note(filename, data, check):
    """搜索结果页的诚实注记；check 保留签名与 _build_note 对称，本形态不用。"""
    s = data['search']
    return '\n'.join([
        '来源：`%s`。' % filename,
        '本文件是知乎**搜索结果列表页**，并非文章正文；页面不含可转换的文章正文，'
        '下方严格按页面实际内容转换为结构化结果列表，不臆造文章内容。',
        '搜索词：「%s」；页面共 %d 条结果、%d 个相关搜索词；原页面：`%s`'
        % (s.get('query') or '未知搜索词',
           len(s.get('items', [])), len(s.get('related', [])),
           s.get('source_url') or '（源文件未记录）'),
        '保存文件名仅供参考：若与上列页面实际主题不一致，一律以页面实际内容为准。',
    ])


def _render_search(data, note=None):
    s = data['search']
    lines = ['# 知乎搜索结果：' + (s.get('query') or '未知搜索词'), '']
    if note:
        lines += ['> ' + line for line in note.splitlines()]
        lines.append('')
    for idx, item in enumerate(s.get('items', []), 1):
        lines.append('%d. [%s](%s)'
                     % (idx, item.get('title') or '', item.get('url') or ''))
        lines.append('')
        if item.get('summary'):
            lines += ['   ' + item['summary'], '']
    if s.get('related'):
        lines += ['## 相关搜索', '']
        for word in s['related']:
            lines += ['- ' + word]
        lines.append('')
    return '\n'.join(lines).rstrip('\n')


def _load_html(path):
    """读字节 → 解 MHTML（若是）→ 文本解码。返回 (html, codec, is_mht)。"""
    with open(path, 'rb') as f:
        raw = f.read()
    if is_mhtml(raw):
        html = unpack_mhtml(raw)
        if html:
            return html, 'mhtml', True
    text, codec = detect_decode(raw)
    if text is None:
        raise ValueError('不是可解码的文本文件：%s（%s）' % (path, codec))
    return text, codec, False


def _render_any(html, filename, check):
    """白名单优先；两表皆空才走 generic 兜底。返回 (md, 形态名, 白名单数据)。"""
    data = parse_sections(html)
    if data['recent'] or data['replay']:
        md = render_markdown(data, filename, _build_note(filename, data, check))
        return md, '直播列表', data
    generic = parse_generic(html)
    label = {'qa': '问答', 'form': '表单', 'article': '通用文章',
             'search': '知乎搜索结果'}[generic['shape']]
    note = (_search_note(filename, generic, check)
            if generic['shape'] == 'search' else None)
    return render_generic(generic, note), label, data


def process_file(path, apply=False, backup=True):
    """编排全流程，返回生成的 Markdown 字符串。

    默认 dry-run（一字节不写）；apply=True 才写 `<同名>.md`，写前若目标已存在先备份 `.bak`。
    """
    html, codec, _ = _load_html(path)
    filename = os.path.basename(path)
    check = consistency_check(filename, html)
    md, _shape, _data = _render_any(html, filename, check)
    if apply:
        out = os.path.splitext(path)[0] + '.md'
        if backup and os.path.exists(out):
            shutil.copyfile(out, out + '.bak')
        with open(out, 'w', encoding='utf-8', newline='\n') as f:
            f.write(md)
    return md


def _check_only(path):
    """只读预检：编码 / 一致性 / 关键词命中 / 形态 / 行数 / 残留标签，不写任何文件。"""
    try:
        html, codec, _ = _load_html(path)
    except ValueError as exc:
        print('编码：无法解码（%s）' % exc)
        return 1
    filename = os.path.basename(path)
    check = consistency_check(filename, html)
    md, shape, data = _render_any(html, filename, check)
    noise = residual_noise(md)
    print('编码：%s' % codec)
    print('形态：%s' % shape)
    print('专题关键词命中（可见正文）：%d' % check['keyword_hits'])
    print('一致性：%s' % (check['warn'] if check['mismatch'] else '文件名与内容无明显冲突'))
    if data['recent'] or data['replay']:
        print('近期直播 %d 行 / 往期回放 %d 行' % (len(data['recent']), len(data['replay'])))
    print('生成 MD %d 行' % len(md.splitlines()))
    print('生成 MD 残留标签：%s' % (', '.join(noise) if noise else '0（无）'))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='HTML → 标准 Markdown 去广告（单站点版式适配器，零第三方依赖）')
    parser.add_argument('path', help='源 .htm / .html 文件')
    parser.add_argument('--check', action='store_true',
                        help='只读预检：文件名↔内容一致性 + 残留标签 + 行数')
    parser.add_argument('--apply', action='store_true',
                        help='写出 <同名>.md（写前若已存在先备份为 .md.bak）')
    parser.add_argument('--no-backup', action='store_true',
                        help='与 --apply 同用时不备份（不推荐）')
    args = parser.parse_args(argv)
    if args.check and args.apply:
        parser.error('--check 与 --apply 互斥，请只选一个')
    if not os.path.isfile(args.path):
        parser.error('文件不存在：%s' % args.path)
    try:
        if args.check:
            return _check_only(args.path)
        md = process_file(args.path, apply=args.apply, backup=not args.no_backup)
        if args.apply:
            print('已写出：%s' % (os.path.splitext(args.path)[0] + '.md'))
        else:
            sys.stdout.write(md)
            print('[dry-run] 未写盘（仅预览，加 --apply 才写出）', file=sys.stderr)
        return 0
    except Exception as exc:                      # 单文件异常隔离，退出码 1
        print('处理失败：%s' % exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
