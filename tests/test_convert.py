"""HTML → Markdown 去广告工具回归测试。

每条用例对应一个已确认的坑（见 docs/PITFALLS-形态兜底.md）。测试用内联 HTML 夹具，不依赖任何真实源文件。
运行：py -m unittest -v
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '脚本'))
import convert  # noqa: E402  (被测模块；CONVERT.PY 不存在时此处即 RED)


# 内联夹具：复刻真实页面的两个区块与各字段 class，含 script/style/观看人数/回放按钮等噪声。
SAMPLE_HTML = (
    '<!doctype html><html><head><title>某在线教育站点-公开课直播</title>'
    '<style>.Lock{color:red}</style><script>var Node=1,CAS=2;</script></head><body>'
    '<div class="home-wrap-box__title flex"><span>近期直播 </span></div>'
    '<div class="recent-carouse-list__item" data-v-40ce4d62>'
    '<div class="item-head-content__title ellipses">今日课题：黑掉摄像头</div>'
    '<div class="item-head-content__info"><img src="x.png"><span>老谭</span></div>'
    '<div class="item-head-content__time">直播时间：<br>03月19日 20:00~22:00</div>'
    '<div class="list-item-content__title">Kali渗透/网络安全/黑客/Linux/解密/防护【网络安全】</div>'
    '<div class="list-item-content__time"><img src="y.png"><div>03月19日20:00~22:00</div></div>'
    '<span>1096人已观看</span><div class="item-right-remind__btn">直播回放</div>'
    '</div>'
    '<div class="home-wrap-box__title flex"><span>往期回放</span></div>'
    '<div class="wrap-box-list__item" data-v-734b8bd4>'
    '<div class="box-list-item__title ellipsis">谷歌面试官亲自分享：Google面试技巧</div>'
    '<div class="box-list-item__desc ellipsis">IT前沿技术分享</div>'
    '<div class="box-list-item__time">08月25日 20:30~23:00</div>'
    '</div></body></html>'
)

DATA = {
    'title': '某在线教育站点 · 公开课直播',
    'recent': [{'topic': 'JAVA高级互联网开发|进阶架构师直播课',
                'speaker': '—', 'time': '03月14日 20:00~23:00'}],
    'replay': [{'topic': '谷歌面试官亲自分享：Google面试技巧',
                'desc': 'IT前沿技术分享', 'time': '08月25日 20:30~23:00'}],
    'categories': [],
}


# MHTML 夹具：QP 编码的 multipart/related，正文标签被 =3D 打断（真实 .mht 就是这个样子）。
MHTML_SAMPLE = (
    b'From: <Saved by Blink>\r\n'
    b'MIME-Version: 1.0\r\n'
    b'Content-Type: multipart/related; boundary="----BOUND"\r\n'
    b'\r\n'
    b'------BOUND\r\n'
    b'Content-Type: text/html; charset=utf-8\r\n'
    b'Content-Transfer-Encoding: quoted-printable\r\n'
    b'\r\n'
    b'<html><body><h1 id=3D"questionTitle">=E9=97=AE=E9=A2=98</h1>'
    b'<p>package=3D"x"</p></body></html>\r\n'
    b'------BOUND\r\n'
    b'Content-Type: image/png\r\n'
    b'Content-Transfer-Encoding: base64\r\n'
    b'\r\n'
    b'iVBORw0KGgo=\r\n'
    b'------BOUND--\r\n'
)


class DetectDecodeTest(unittest.TestCase):
    """坑：编码检测顺序（UTF-8 优先 + gb18030 兜底）。"""

    def test_utf8_is_detected(self):
        text, codec = convert.detect_decode('中文'.encode('utf-8'))
        self.assertEqual(text, '中文')
        self.assertEqual(codec, 'utf-8')

    def test_utf8_bom_is_stripped(self):
        raw = b'\xef\xbb\xbf' + '中文'.encode('utf-8')
        text, codec = convert.detect_decode(raw)
        self.assertEqual(text, '中文')
        self.assertEqual(codec, 'utf-8')

    def test_gb18030_fallback(self):
        text, codec = convert.detect_decode('中文'.encode('gb18030'))
        self.assertEqual(text, '中文')
        self.assertEqual(codec, 'gb18030')

    def test_bad_byte_is_not_text(self):
        text, codec = convert.detect_decode(b'abc\x00def')
        self.assertIsNone(text)
        self.assertEqual(codec, 'not-text')


class ConsistencyTest(unittest.TestCase):
    """坑：文件名≠内容、CSS/JS 假命中、不脑补正文。"""

    def test_css_js_keywords_are_not_body_hits(self):
        # 必须植入**真**专题关键词：用 Lock/Node/CAS 这类不在 TOPIC_KEYWORDS 里的 token
        # 是恒真用例（即使不剥 script/style 也照样通过），挡不住回归。
        html = ('<style>.AQS{color:red}</style>'
                '<script>var 线程=1;var 锁=2;var ReentrantLock=3;</script>'
                '<p>课程表</p>')
        result = convert.consistency_check('AQS专题.htm', html)
        self.assertEqual(result['keyword_hits'], 0)
        self.assertTrue(result['mismatch'])

    def test_real_topic_in_body_is_counted(self):
        # 对照组：同样的词出现在可见正文里必须被数到，否则上一条用例没有区分力。
        result = convert.consistency_check('AQS专题.htm', '<p>AQS 与线程锁</p>')
        self.assertGreater(result['keyword_hits'], 0)
        self.assertFalse(result['mismatch'])

    def test_filename_body_mismatch_warns_without_blocking(self):
        result = convert.consistency_check('AQS核心底层专题.htm', '<p>直播列表</p>')
        self.assertTrue(result['mismatch'])
        self.assertIn('不符', result['warn'])

    def test_matching_topic_has_no_mismatch(self):
        result = convert.consistency_check('AQS专题.htm', '<p>AQS 队列与锁原理</p>')
        self.assertFalse(result['mismatch'])

    def test_plain_filename_has_no_mismatch(self):
        result = convert.consistency_check('live.htm', '<p>直播列表</p>')
        self.assertFalse(result['mismatch'])


class ParseSectionsTest(unittest.TestCase):
    """坑：Nuxt SSR 靠 class 定位、script/style 是假命中、字段有缺失。"""

    def test_title_is_normalized(self):
        self.assertEqual(convert.parse_sections(SAMPLE_HTML)['title'],
                         '某在线教育站点 · 公开课直播')

    def test_recent_item_fields(self):
        recent = convert.parse_sections(SAMPLE_HTML)['recent']
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0], {'topic': '今日课题：黑掉摄像头',
                                     'speaker': '老谭',
                                     'time': '03月19日 20:00~22:00'})

    def test_replay_item_fields(self):
        replay = convert.parse_sections(SAMPLE_HTML)['replay']
        self.assertEqual(len(replay), 1)
        self.assertEqual(replay[0]['topic'], '谷歌面试官亲自分享：Google面试技巧')
        self.assertEqual(replay[0]['desc'], 'IT前沿技术分享')
        self.assertEqual(replay[0]['time'], '08月25日 20:30~23:00')

    def test_categories_are_kept_for_note(self):
        self.assertEqual(convert.parse_sections(SAMPLE_HTML)['categories'],
                         ['Kali渗透/网络安全/黑客/Linux/解密/防护【网络安全】'])

    def test_script_style_text_is_dropped(self):
        data = convert.parse_sections(SAMPLE_HTML)
        blob = repr(data)
        self.assertNotIn('.Lock', blob)
        self.assertNotIn('var Node', blob)
        self.assertNotIn('1096人已观看', blob)
        self.assertNotIn('直播回放', blob)

    def test_adjacent_items_all_kept(self):
        # 相邻 item 不得互相覆盖（单例 _cur 的经典丢行 bug）。
        html = (
            '<div class="recent-carouse-list__item">'
            '<div class="item-head-content__title">A主题</div>'
            '<div class="item-head-content__info">甲</div>'
            '<div class="item-head-content__time">03月19日 20:00~22:00</div></div>'
            '<div class="recent-carouse-list__item">'
            '<div class="item-head-content__title">B主题</div>'
            '<div class="item-head-content__info">乙</div>'
            '<div class="item-head-content__time">03月20日 20:00~22:00</div></div>')
        recent = convert.parse_sections(html)['recent']
        self.assertEqual([r['topic'] for r in recent], ['A主题', 'B主题'])

    def test_nested_items_lose_no_rows(self):
        # 模板嵌套时不得因为单例 _cur 被覆盖而静默吞掉外层条目。
        html = (
            '<div class="recent-carouse-list__item">'
            '<div class="item-head-content__title">A主题</div>'
            '<div class="item-head-content__info">甲</div>'
            '<div class="item-head-content__time">03月19日 20:00~22:00</div>'
            '<div class="recent-carouse-list__item">'
            '<div class="item-head-content__title">B主题</div>'
            '<div class="item-head-content__info">乙</div>'
            '<div class="item-head-content__time">03月20日 20:00~22:00</div></div></div>')
        topics = [r['topic'] for r in convert.parse_sections(html)['recent']]
        self.assertEqual(len(topics), 2, '嵌套时丢了条目：%r' % topics)
        self.assertIn('B主题', topics)   # 内层先闭合，先产出
        self.assertIn('A主题', topics)

    def test_topic_containing_ad_hint_is_not_swallowed(self):
        # 主题里恰好含「精品课」等推广词，不得整条丢弃成空主题。
        html = (
            '<div class="recent-carouse-list__item">'
            '<div class="item-head-content__title">精品课：并发编程与AQS</div>'
            '<div class="item-head-content__info">老谭</div>'
            '<div class="item-head-content__time">03月19日 20:00~22:00</div></div>')
        recent = convert.parse_sections(html)['recent']
        self.assertEqual(recent[0]['topic'], '精品课：并发编程与AQS')
        self.assertEqual(recent[0]['speaker'], '老谭')


class RenderMarkdownTest(unittest.TestCase):
    """坑：标准 MD 结构、表格内 | 必须转义、注记用引用行。"""

    def test_headings_and_table_headers(self):
        md = convert.render_markdown(DATA, 'x.htm', '注记')
        self.assertIn('# 某在线教育站点 · 公开课直播', md)
        self.assertIn('## 近期直播', md)
        self.assertIn('## 往期回放', md)
        self.assertIn('| 主题 | 讲师 | 时间 |', md)
        self.assertIn('| 主题 | 简介 | 时间 |', md)

    def test_pipe_is_escaped_in_cell(self):
        md = convert.render_markdown(DATA, 'x.htm', '注记')
        self.assertIn(r'JAVA高级互联网开发\|进阶架构师直播课', md)

    def test_escape_cell_collapses_whitespace(self):
        self.assertEqual(convert.escape_cell('A|B\n C '), r'A\|B C')

    def test_url_in_cell_is_not_mangled(self):
        # 斜杠留白只服务课程分类的观感，绝不能把 URL 拆成 'http: / / x.com / a / b'。
        out = convert.escape_cell('见 http://x.com/a/b 说明')
        self.assertIn('http://x.com/a/b', out)
        self.assertEqual(out, '见 http://x.com/a/b 说明')

    def test_category_slash_spacing_still_applied(self):
        # 对照组：非 URL 的中文分类斜杠仍要留白。
        self.assertEqual(convert.escape_cell('Kali渗透/网络安全'),
                         'Kali渗透 / 网络安全')

    def test_note_is_a_quoted_line(self):
        md = convert.render_markdown(DATA, 'x.htm', '来源说明')
        self.assertIn('> 来源说明', md)

    def test_category_note_is_generated(self):
        data = dict(DATA, categories=['Kali渗透/网络安全', 'Kali渗透/网络安全'])
        md = convert.render_markdown(data, 'x.htm', '注记')
        self.assertIn('前两场课程分类均为：Kali渗透 / 网络安全。', md)


class ResidualNoiseTest(unittest.TestCase):
    """坑：注记里会提到「精品课」等词，残留检查必须跳过引用行。"""

    def test_html_residuals_are_detected(self):
        md = '# T\n\n| a |\n| <script>x</script> <div> data-v-40ce |\n'
        hits = convert.residual_noise(md)
        for marker in ('<script', '<div', 'data-v-'):
            self.assertIn(marker, hits)

    def test_quoted_note_is_ignored(self):
        md = ('# T\n\n> 已去除精品课、二维码、公众号、商务合作、ICP 备案。\n\n'
              '| a | b |\n| --- | --- |\n| 课程 | 时间 |\n')
        self.assertEqual(convert.residual_noise(md), [])


class ProcessFileTest(unittest.TestCase):
    """坑：默认 dry-run 不写盘；--apply 才写且写前备份；--check 与 --apply 互斥。"""

    def _write_sample(self, d):
        path = os.path.join(d, 'sample.htm')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(SAMPLE_HTML)
        return path

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_sample(d)
            md = convert.process_file(path, apply=False)
            self.assertIsInstance(md, str)
            self.assertIn('## 近期直播', md)
            self.assertFalse(os.path.exists(path[:-4] + '.md'))

    def test_apply_writes_and_backs_up_existing(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_sample(d)
            md_path = path[:-4] + '.md'
            first = convert.process_file(path, apply=True, backup=True)
            self.assertTrue(os.path.exists(md_path))
            # 目标当初不存在 → 不该凭空产生 .bak
            self.assertFalse(os.path.exists(md_path + '.bak'))
            convert.process_file(path, apply=True, backup=True)
            self.assertTrue(os.path.exists(md_path + '.bak'))
            # 备份必须是「被覆盖掉的上一版」，内容等于首次产物。
            with open(md_path + '.bak', encoding='utf-8') as f:
                self.assertEqual(f.read(), first)

    def test_no_backup_flag_skips_backup(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_sample(d)
            md_path = path[:-4] + '.md'
            convert.process_file(path, apply=True, backup=True)
            convert.process_file(path, apply=True, backup=False)
            self.assertFalse(os.path.exists(md_path + '.bak'))

    def test_runtime_error_exits_one(self):
        # 运行期异常（不可解码）走 except → 退出码 1；argparse 用法错误则是 2（Unix 惯例）。
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, 'bad.htm')
            with open(bad, 'wb') as f:
                f.write(b'abc\x00def')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(convert.main([bad]), 1)

    def test_check_and_apply_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_sample(d)
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    convert.main(['--check', '--apply', path])


class MhtmlTest(unittest.TestCase):
    """坑 18：MHTML 不能当纯文本读 —— QP 转义的 =3D 会破坏标签上下文。"""

    def test_plain_html_is_not_mhtml(self):
        self.assertFalse(convert.is_mhtml(b'<html><body>x</body></html>'))

    def test_mhtml_header_is_detected(self):
        self.assertTrue(convert.is_mhtml(MHTML_SAMPLE))

    def test_unpack_returns_inner_html(self):
        html = convert.unpack_mhtml(MHTML_SAMPLE)
        self.assertIn('<h1 id="questionTitle">问题</h1>', html)

    def test_unpack_missing_html_part_returns_empty(self):
        self.assertEqual(convert.unpack_mhtml(b'not a mime message'), '')


# 问答页夹具：复刻搜狗问问的真实嵌套（最佳答案 1 条 + 其他回答 2 条 + 两个广告 section）。
QA_SAMPLE = (
    '<html><head><title>问答</title><style>.a{color:red}</style><script>var a=1;</script></head><body>'
    '<nav><a href="#">导航</a></nav>'
    '<section class="ask-con"><h1 id="questionTitle">【未元物质】是怎样的一种能力？</h1>'
    '<div class="tags"><a class="tags-tab">化学</a></div></section>'
    '<section class="detail-wrap best-answer" id="bestAnswers">'
    '<h2 class="ask-list-tit"><div class="best-cap">最佳答案</div></h2>'
    '<ul class="ask-mod"><li id="ans_1" class="answer_item">'
    '<h3 class="ask-fresh-con answerContent">最佳答案正文</h3>'
    '<div class="show-all more_btn none"><div class="show-text">查看全文</div></div>'
    '<div class="opera"><div class="peo"></div><span class="time">2011-09-02</span></div>'
    '<div class="good-bad zan_cai_wrap"><p><span class="good ans_zan">赞</span></p>'
    '<p><span class="txt-num">13</span></p></div></li></ul></section>'
    '<section class="detail-wrap none out-sourcing-ad" id="underBestAnswerPlaceHolder"></section>'
    '<section class="detail-wrap out-sourcing-ad" style="display:none" id="wangMengAd">'
    '<div id="sogou_wap_805186">广告推广内容</div></section>'
    '<section class="detail-wrap other-answer" id="otherAnswers">'
    '<h2 class="ask-list-tit"><div class="cap">其他回答</div><span class="mes">2条回答</span></h2>'
    '<ul class="ask-mod">'
    '<li id="ans_2" class="answer_item defaultShow">'
    '<div class="detail-peo-mes"><div class="peo"></div><p class="name">风移影动</p></div>'
    '<h3 class="ask-fresh-con show"><p>其他回答正文一</p></h3>'
    '<div class="opera"><span class="time">2016-12-03</span></div>'
    '<div class="good-bad zan_cai_wrap"><p><span class="txt-num">13</span></p></div></li>'
    '<li id="ans_3" class="answer_item defaultShow">'
    '<div class="detail-peo-mes"><p class="name">匿名用户</p></div>'
    '<h3 class="ask-fresh-con">其他回答正文二</h3></li>'
    '</ul></section>'
    '<div id="relate_recommend"><ul><li class="rl-normal">'
    '<div class="recommend-con"><h6>相关推荐</h6></div></li></ul></div>'
    '<footer>页脚版权</footer></body></html>'
)

# 表单页夹具：复刻腾讯文档表单（2 个 question + 页头标题/副标题 + 会员/帐号噪声）。
FORM_SAMPLE = (
    '<html><body>'
    '<div class="form-header form-header-readonly">'
    '<div class="form-header-title-content">返现打款信息自助填写表单</div>'
    '<div class="form-header-subtitle-wrap"><div class="editable-input form-header-subtitle readonly">'
    '<div class="editable-input-content placeholder-type-follow">'
    '该表单提交后无法修改，请大家一定认真核对，填错无法打款，感谢！</div></div></div>'
    '<div class="form-header-setting-group"></div></div>'
    '<div class="form-fill-container"><div class="form-body form-fill-body">'
    '<div class="question" data-qid="q1" data-type="simple"><div class="question-main">'
    '<div class="question-main-index"><span class="icon-number"><i class="inumber">0</i>'
    '<i class="inumber">1</i></span><span class="required-span">*</span></div>'
    '<div class="question-main-content">'
    '<div class="question-title"><span class="auto-ellipse">阿里云账号昵称</span></div>'
    '<div class="question-sub"><div class="question-subtitle">'
    '<span class="auto-ellipse">登进阿里云，右上角个人信息里查看</span></div></div>'
    '<div class="question-content"><div class="form-simple">'
    '<textarea placeholder="请输入">libai4hao</textarea>'
    '<div class="form-simple-desc"></div></div></div>'
    '</div></div></div>'
    '<div class="question" data-qid="q2" data-type="simple"><div class="question-main">'
    '<div class="question-main-index"><span class="icon-number"><i class="inumber">0</i>'
    '<i class="inumber">2</i></span></div>'
    '<div class="question-main-content">'
    '<div class="question-title"><span class="auto-ellipse">邮箱</span></div>'
    '<div class="question-sub"><div class="question-subtitle">'
    '<span class="auto-ellipse">常用邮箱</span></div></div>'
    '<div class="question-content"><div class="form-simple">'
    '<textarea placeholder="请输入"></textarea></div></div>'
    '</div></div></div>'
    '</div></div>'
    '<div class="open-member"><span>开通会员畅享高效协作体验</span><button>立即开通</button></div>'
    '<div class="account-management"><span>帐号管理</span><span>切换帐号</span>'
    '<span>退出登录</span></div><button>提交</button></body></html>'
)

# 文章页夹具：三种形状都不像时的保守兜底。
ARTICLE_SAMPLE = (
    '<html><body><nav>导航链接</nav><h1>文章标题</h1>'
    '<p>第一段正文。</p><p>第二段正文。</p>'
    '<div><p>扫一扫，手机浏览</p><p>打赏</p></div>'
    '<div><p>内容涉黄</p><p>政治相关</p><p>涉嫌广告</p><p>侮辱谩骂</p><p>样式问题</p></div>'
    '<div><p>微信号</p><p>gh_123</p><p>功能介绍</p></div>'
    '<div><p>微信扫一扫赞赏作者</p><p>长按二维码向我转账</p></div>'
    '<footer>页脚版权</footer><script>var a=1;</script></body></html>'
)

# v3 夹具：知乎搜索结果页（2 张结果卡 + 1 张相关搜索卡；卡片内刻意保留嵌套 div
# 用于锁定 div 深度计数闭合规则；href 带 &amp; 用于锁定属性值反转义）。
ZHIHU_SAVED_FROM = ('<!-- saved from url=(0078)'
                    'https://www.zhihu.com/search?type=content'
                    '&q=2020%20%E5%86%85%E5%BE%AA%E7%8E%AF -->')

ZHIHU_SEARCH_SAMPLE = (
    ZHIHU_SAVED_FROM
    + '<html><head><title>(2 封私信 / 80 条消息) 2020 内循环 - 搜索结果 - 知乎</title></head>'
      '<body>'
      '<ul><li>综合</li><li>用户</li></ul>'
      '<div class="Card SearchResult-Card">'
      '  <div class="List-item">'
      '    <div class="ContentItem ArticleItem">'
      '      <h2 class="ContentItem-title"><a href="https://zhuanlan.zhihu.com/p/172279448?a=1&amp;b=2">'
      '        <span class="Highlight">“房住不炒”+“<em>内循环</em>”，大转折时代来临！</span></a></h2>'
      '      <div class="RichContent is-collapsed">'
      '        <div class="RichContent-inner">'
      '          <span class="RichText ztext CopyrightRichText-richText" itemprop="articleBody">'
      '            <b>羊迪</b>：作为生于斯长于斯并将终老于斯的中国人，别无选择。<em>内循环</em>提...'
      '          </span>'
      '        </div>'
      '      </div>'
      '      <button class="ContentItem-more">阅读全文</button>'
      '      <div class="ContentItem-actions"><button class="VoteButton">赞同 12</button></div>'
      '    </div>'
      '  </div>'
      '</div>'
      '<div class="Card SearchResult-Card">'
      '  <div class="List-item">'
      '    <div class="ContentItem AnswerItem">'
      '      <h2 class="ContentItem-title"><a href="https://www.zhihu.com/question/1/answer/2">'
      '        内循环对普通人有什么影响？</a></h2>'
      '      <div class="RichContent-inner">'
      '        <span class="RichText ztext">普通人更应关注<em>内循环</em>带来的机会...</span>'
      '      </div>'
      '    </div>'
      '  </div>'
      '</div>'
      '<div class="Card SearchResult-Card">'
      '  <div class="RelevantQuery">'
      '    <h2>相关搜索</h2>'
      '    <ul><li><a href="/search?q=1">经济内循环</a></li>'
      '        <li><a href="/search?q=2">经济循环</a></li></ul>'
      '  </div>'
      '</div>'
      '</body></html>'
)


class V3HelpersTest(unittest.TestCase):
    """v3：形状识别、URL/标题提取、article 噪声谓词。"""

    def test_zhihu_search_shape_detected(self):
        self.assertEqual(convert._shape_of(ZHIHU_SEARCH_SAMPLE), 'search')

    def test_non_search_samples_keep_shape(self):
        self.assertEqual(convert._shape_of(QA_SAMPLE), 'qa')
        self.assertEqual(convert._shape_of(FORM_SAMPLE), 'form')
        self.assertEqual(convert._shape_of(ARTICLE_SAMPLE), 'article')
        self.assertEqual(convert._shape_of(SAMPLE_HTML), 'article')
        self.assertFalse(convert._is_zhihu_search(SAMPLE_HTML))

    def test_is_zhihu_search_needs_both_signals(self):
        self.assertFalse(convert._is_zhihu_search(
            '<title>某页 - 搜索结果 - 知乎</title><div>无卡片</div>'))
        self.assertFalse(convert._is_zhihu_search(
            '<!-- saved from url=(0078)https://www.zhihu.com/search?q=x -->'
            '<h2 class="ContentItem-title">只有一个</h2>'))

    def test_source_url_and_title_text(self):
        self.assertEqual(
            convert._source_url(ZHIHU_SEARCH_SAMPLE),
            'https://www.zhihu.com/search?type=content'
            '&q=2020%20%E5%86%85%E5%BE%AA%E7%8E%AF')
        self.assertIn('搜索结果', convert._title_text(ZHIHU_SEARCH_SAMPLE))
        self.assertIn('知乎', convert._title_text(ZHIHU_SEARCH_SAMPLE))

    def test_is_article_noise(self):
        self.assertTrue(convert._is_article_noise('gh_deadbeef'))
        self.assertTrue(convert._is_article_noise('喜欢的朋友不要忘记点赞哦'))
        self.assertTrue(convert._is_article_noise('  打赏  '))
        self.assertTrue(convert._is_article_noise(''))
        self.assertFalse(convert._is_article_noise('第一段正文。'))


# v3 夹具：微信图文的最小复刻 —— 真标题 h2#activity-name、被双层 font/span 切碎的
# 单个 <p>、pre > ol.list-paddingleft-2 > li×3（行边界是 </li>）、翻译浮层、
# 公众号信息卡、尾部推广段；末尾额外放一个无 li 的普通 <pre>（Review Focus 5）。
WECHAT_LIKE_SAMPLE = (
    '<html><head><title>使用Qt实现超酷炫窗口特效</title></head><body>'
    '<h2 class="rich_media_title" id="activity-name">使用Qt实现超酷炫窗口特效</h2>'
    '<div class="rich_media_content" id="js_content">'
    '<p><font><font>按钮命名格式为“ btn </font></font>'
    '<span><font><font>行号</font></font></span>'
    '<font><font>列号”，并且将这些QPushButton提升为MyButton。</font></font></p>'
    '<pre><ol class="list-paddingleft-2" style="list-style-type: none;">'
    '<li><p><span><code>QPushButton {</code></span></p></li>'
    '<li><p><span><code>    border-radius: 4px;</code></span></p></li>'
    '<li><p><span><code>}</code></span></p></li>'
    '</ol></pre>'
    '<p>不要忘记点赞</p>'
    '<div class="profile_container"><p class="profile_meta">'
    '<span class="profile_meta_value">gh_deadbeef</span></p></div>'
    '</div>'
    '<div class="skiptranslate"><h1 class="title">原文</h1></div>'
    '<pre>int main() { return 0; }</pre>'
    '</body></html>'
)


class V3ArticleTest(unittest.TestCase):
    """v3：微信图文的 4 类缺陷修复 + 既有 article 夹具逐字不变。"""

    def setUp(self):
        self.data = convert.parse_generic(WECHAT_LIKE_SAMPLE)
        self.md = convert.render_generic(self.data)

    def test_article_shape(self):
        self.assertEqual(self.data['shape'], 'article')

    def test_code_block_restores_three_lines(self):
        fenced = self.md.split('```')
        code = fenced[1].strip('\n')
        self.assertEqual(code, 'QPushButton {\n    border-radius: 4px;\n}')
        self.assertEqual(code.count('\n'), 2)

    def test_paragraph_is_merged(self):
        self.assertIn(
            '按钮命名格式为“ btn 行号列号”，并且将这些QPushButton提升为MyButton。',
            self.md)
        self.assertNotIn('\n行号\n', self.md)
        self.assertNotIn('\n列号', self.md)
        self.assertEqual(
            [p for p in self.data['article']['paragraphs'] if not p], [])

    def test_heading_and_noise_dropped(self):
        self.assertTrue(self.md.startswith('# 使用Qt实现超酷炫窗口特效\n'))
        for noise in ('原文', 'gh_deadbeef', '不要忘记点赞'):
            self.assertNotIn(noise, self.md)
        self.assertEqual(convert.residual_noise(self.md), [])

    def test_plain_pre_without_li_is_kept(self):
        # Review Focus 5：无 li 的普通 <pre> 仍完整渲染，不丢内容
        self.assertIn('int main() { return 0; }', self.md)

    def test_existing_article_fixture_unchanged(self):
        self.assertEqual(
            convert.parse_generic(ARTICLE_SAMPLE)['article']['paragraphs'],
            ['第一段正文。', '第二段正文。'])
        self.assertEqual(
            convert.parse_generic(ARTICLE_SAMPLE)['article']['heading'],
            '文章标题')

    def test_reward_area_container_is_dropped(self):
        # 回归：段落合并把「微信扫一扫赞赏作者」+「赞赏」两个本可各自精确
        # 丢弃的碎片拼成整串，精确匹配失效；根因是赞赏浮层容器 reward_area
        # 未整棵丢弃。用容器 class 从源头丢弃，正文段落必须保留。
        html = (
            '<html><head><title>T</title></head><body>'
            '<h2 class="rich_media_title" id="activity-name">T</h2>'
            '<div class="rich_media_content" id="js_content">'
            '<p>正文段落。</p></div>'
            '<div class="reward_area tc reward_area_primary">'
            '<p class="reward_button_wrp"><strong>微信扫一扫赞赏作者</strong> 赞赏</p>'
            '</div></body></html>')
        md = convert.render_generic(convert.parse_generic(html))
        self.assertIn('正文段落。', md)
        self.assertNotIn('微信扫一扫赞赏作者', md)
        self.assertNotIn('赞赏', md)


class V3ZhihuSearchParseTest(unittest.TestCase):
    """v3：知乎搜索结果页白名单 zone 抽取（含嵌套 div 深度计数）。"""

    def setUp(self):
        self.data = convert.parse_generic(ZHIHU_SEARCH_SAMPLE)

    def test_shape_is_search(self):
        self.assertEqual(self.data['shape'], 'search')

    def test_two_cards_extracted(self):
        items = self.data['search']['items']
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['title'],
                         '“房住不炒”+“内循环”，大转折时代来临！')
        self.assertEqual(items[0]['url'],
                         'https://zhuanlan.zhihu.com/p/172279448?a=1&b=2')
        self.assertIn('羊迪：作为生于斯长于斯', items[0]['summary'])
        self.assertIn('内循环', items[0]['summary'])
        joined = ''.join(i['title'] + i['summary'] for i in items)
        for noise in ('阅读全文', '赞同 12', '综合', '相关搜索'):
            self.assertNotIn(noise, joined)

    def test_related_terms(self):
        self.assertEqual(self.data['search']['related'],
                         ['经济内循环', '经济循环'])

    def test_query_and_source_url(self):
        self.assertEqual(self.data['search']['query'], '2020 内循环')
        self.assertEqual(
            self.data['search']['source_url'],
            'https://www.zhihu.com/search?type=content'
            '&q=2020%20%E5%86%85%E5%BE%AA%E7%8E%AF')

    def test_missing_q_falls_back_to_title(self):
        # Review Focus 4：/search? 无 q= 时 query 由 <title> 退化，不抛异常、不为空
        html = ZHIHU_SEARCH_SAMPLE.replace(
            '&q=2020%20%E5%86%85%E5%BE%AA%E7%8E%AF', '')
        data = convert.parse_generic(html)
        self.assertEqual(data['shape'], 'search')
        self.assertEqual(data['search']['query'], '2020 内循环')

    def test_missing_saved_from_comment_still_search(self):
        # Review Focus 3：无 saved-from 注释时仍靠 <title> 识别，source_url 退化为 ''
        html = ZHIHU_SEARCH_SAMPLE.replace(ZHIHU_SAVED_FROM, '')
        data = convert.parse_generic(html)
        self.assertEqual(data['shape'], 'search')
        self.assertEqual(data['search']['source_url'], '')
        self.assertEqual(data['search']['query'], '2020 内循环')


class V3SearchRenderTest(unittest.TestCase):
    """v3：搜索页渲染 + 诚实注记 + note 仅对 search 生效 + 白名单不变量。"""

    def test_render_search(self):
        md = convert._render_search(convert.parse_generic(ZHIHU_SEARCH_SAMPLE))
        self.assertTrue(md.startswith('# 知乎搜索结果：2020 内循环'))
        self.assertIn(
            '1. [“房住不炒”+“内循环”，大转折时代来临！]'
            '(https://zhuanlan.zhihu.com/p/172279448?a=1&b=2)', md)
        self.assertIn('   羊迪：作为生于斯长于斯', md)
        self.assertIn('## 相关搜索', md)
        self.assertIn('- 经济内循环', md)
        self.assertNotIn('阅读全文', md)
        self.assertEqual(convert.residual_noise(md), [])

    def test_render_any_adds_honest_note(self):
        md, label, _ = convert._render_any(
            ZHIHU_SEARCH_SAMPLE, '公租房时代已经来临！？ - 知乎.html', {})
        self.assertEqual(label, '知乎搜索结果')
        for phrase in ('搜索结果列表页', '并非文章正文', '保存文件名仅供参考',
                       '原页面', '公租房时代已经来临！？ - 知乎.html'):
            self.assertIn(phrase, md)
        self.assertEqual(convert.residual_noise(md), [])

    def test_note_ignored_by_other_shapes(self):
        # Review Focus 1：note 仅对 search 生效，qa/form/article 传非空 note 逐字不变
        for sample in (QA_SAMPLE, FORM_SAMPLE, ARTICLE_SAMPLE):
            data = convert.parse_generic(sample)
            self.assertEqual(convert.render_generic(data, '忽略我'),
                             convert.render_generic(data))

    def test_render_search_without_note(self):
        # Review Focus 2：直调 _render_search 不传 note 不崩、不出现 None 字面
        md = convert._render_search(convert.parse_generic(ZHIHU_SEARCH_SAMPLE))
        self.assertNotIn('None', md)
        self.assertIn('1. [', md)

    def test_missing_saved_from_note_still_honest(self):
        # Review Focus 3：无 saved-from 注释时注记仍完整、原页面给退化文案
        html = ZHIHU_SEARCH_SAMPLE.replace(ZHIHU_SAVED_FROM, '')
        md, _, _ = convert._render_any(html, 'x.html', {})
        self.assertIn('（源文件未记录）', md)
        self.assertIn('并非文章正文', md)

    def test_render_generic_direct_call_unchanged(self):
        self.assertTrue(
            convert.render_generic(
                convert.parse_generic(ARTICLE_SAMPLE)).startswith('# 文章标题'))


class QaShapeTest(unittest.TestCase):
    """坑 19：广告块 out-sourcing-ad 里没有 answer_item，但旁边有 —— 必须按区段归类。"""

    def setUp(self):
        self.data = convert.parse_generic(QA_SAMPLE)

    def test_qa_shape_is_detected(self):
        self.assertEqual(self.data['shape'], 'qa')

    def test_question_title_is_captured(self):
        self.assertIn('未元物质', self.data['qa']['question'])

    def test_best_answer_count(self):
        self.assertEqual(len(self.data['qa']['best']), 1)

    def test_other_answers_count(self):
        self.assertEqual(len(self.data['qa']['others']), 2)

    def test_author_is_captured(self):
        self.assertEqual(self.data['qa']['others'][0]['author'], '风移影动')

    def test_ad_block_answers_are_excluded(self):
        texts = [a['text'] for a in self.data['qa']['best'] + self.data['qa']['others']]
        self.assertNotIn('广告推广内容', ''.join(texts))

    def test_recommend_block_is_excluded(self):
        texts = [a['text'] for a in self.data['qa']['best'] + self.data['qa']['others']]
        self.assertNotIn('相关推荐', ''.join(texts))


class FormShapeTest(unittest.TestCase):
    """坑 20：表单页没有语义标签，只能靠 div.question 这类框架 class。"""

    def setUp(self):
        self.data = convert.parse_generic(FORM_SAMPLE)

    def test_form_shape_is_detected(self):
        self.assertEqual(self.data['shape'], 'form')

    def test_form_title_and_subtitle(self):
        form = self.data['form']
        self.assertEqual(form['title'], '返现打款信息自助填写表单')
        self.assertIn('该表单提交后无法修改', form['subtitle'])

    def test_field_count(self):
        self.assertEqual(len(self.data['form']['fields']), 2)

    def test_first_field_triple(self):
        f = self.data['form']['fields'][0]
        self.assertEqual(f['no'], '1')
        self.assertEqual(f['label'], '阿里云账号昵称')
        self.assertEqual(f['note'], '登进阿里云，右上角个人信息里查看')
        self.assertEqual(f['value'], 'libai4hao')
        self.assertTrue(f['required'])

    def test_second_field_is_not_required(self):
        f = self.data['form']['fields'][1]
        self.assertFalse(f['required'])
        self.assertEqual(f['value'], '')


class ArticleShapeTest(unittest.TestCase):
    """坑 22：article 只是兜底，不承诺排版还原。"""

    def test_article_shape_is_detected(self):
        data = convert.parse_generic(ARTICLE_SAMPLE)
        self.assertEqual(data['shape'], 'article')
        self.assertEqual(data['article']['heading'], '文章标题')

    def test_article_paragraphs(self):
        pars = convert.parse_generic(ARTICLE_SAMPLE)['article']['paragraphs']
        self.assertEqual(pars, ['第一段正文。', '第二段正文。'])


class GenericRenderTest(unittest.TestCase):
    """坑 21：渲染必须无 HTML 残留，形态分派各自成篇。"""

    def test_qa_render_has_headings(self):
        md = convert.render_generic(convert.parse_generic(QA_SAMPLE))
        self.assertIn('# 【未元物质】是怎样的一种能力？', md)
        self.assertIn('## 最佳答案', md)
        self.assertIn('## 其他回答', md)
        self.assertIn('### 风移影动', md)

    def test_form_render_has_table(self):
        md = convert.render_generic(convert.parse_generic(FORM_SAMPLE))
        self.assertIn('| 序号 | 字段 | 说明 | 已填值 |', md)
        self.assertIn('| 1* | 阿里云账号昵称 |', md)
        self.assertIn('| 2 | 邮箱 |', md)

    def test_form_render_drops_noise(self):
        md = convert.render_generic(convert.parse_generic(FORM_SAMPLE))
        for noise in ('开通会员', '立即开通', '退出登录', '帐号管理', '切换帐号'):
            self.assertNotIn(noise, md)

    def test_article_render_drops_noise(self):
        md = convert.render_generic(convert.parse_generic(ARTICLE_SAMPLE))
        self.assertIn('# 文章标题', md)
        self.assertIn('第一段正文。', md)
        for noise in ('导航链接', '页脚版权', '扫一扫，手机浏览', '打赏',
                      '内容涉黄', '政治相关', '涉嫌广告', '侮辱谩骂', '样式问题',
                      '海报', '分享海报说明', '评论', '微信扫一扫',
                      '发送到看一看', '关注该公众号',
                      '微信号', 'gh_123', '功能介绍',
                      '微信扫一扫赞赏作者', '长按二维码向我转账'):
            self.assertNotIn(noise, md)

    def test_generic_output_has_no_html_residual(self):
        for sample in (QA_SAMPLE, FORM_SAMPLE, ARTICLE_SAMPLE):
            md = convert.render_generic(convert.parse_generic(sample))
            self.assertEqual(convert.residual_noise(md), [], md)


class DispatchTest(unittest.TestCase):
    """坑 21 的核心：白名单路径必须先行，generic 只在两表皆空时兜底。"""

    def test_live_path_never_calls_generic(self):
        calls = []
        orig = convert.parse_generic
        convert.parse_generic = lambda h: calls.append(1) or orig(h)
        try:
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, 'sample.htm')
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(SAMPLE_HTML)
                md = convert.process_file(path)
        finally:
            convert.parse_generic = orig
        self.assertEqual(calls, [])
        self.assertIn('## 近期直播', md)

    def test_non_live_file_uses_generic(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'qa.html')
            with open(path, 'w', encoding='utf-8') as f:
                f.write(QA_SAMPLE)
            md = convert.process_file(path)
        self.assertIn('## 最佳答案', md)

    def test_search_path_uses_generic(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'zh.html')
            with open(path, 'w', encoding='utf-8') as f:
                f.write(ZHIHU_SEARCH_SAMPLE)
            md = convert.process_file(path)
        self.assertIn('# 知乎搜索结果', md)

    def test_mhtml_file_is_unpacked_before_parse(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'x.mht')
            with open(path, 'wb') as f:
                f.write(MHTML_SAMPLE)
            md = convert.process_file(path)
        self.assertIn('问题', md)


if __name__ == '__main__':
    unittest.main()
