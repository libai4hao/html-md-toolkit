# 形态兜底转换器踩坑记录

本文件沉淀「富文本页面 → Markdown」转换过程中实际踩过 / 验证过的坑。每坑固定格式：**现象 → 根因 → 证据 → 正确写法 → 如何避免**。证据落到 `脚本/convert.py` 的具体函数或可复跑命令。

脚本：`脚本/convert.py`　测试：`tests/test_convert.py`　用法：根目录 `README.md`

坑 1–12 来自白名单首轮实现，坑 13–17 来自首版交付后的独立代码审查与修复，坑 18–22 来自 v2 多形态支持，坑 23–29 来自 v3 微信图文增强与知乎搜索结果页适配，坑 30 来自最终逐行核对落盘产物时发现的 v3 回归。

---

## 坑 1：文件名写「AQS 专题」，正文却是直播列表页 —— 别按标题脑补内容

- **现象**：用户给的文件名是 `某专题.htm`，直觉会以为里面是一篇 AQS/并发编程长文；全文 `<title>某在线教育站点-公开课直播</title>`，可见文本只有导航、直播课标题、分页、页脚版权。
- **根因**：这是一张 Nuxt SSR 的公开课直播列表页（`<div id="__nuxt">` + `window.__NUXT__` payload），文件名是下载时的命名，与页面内容无关。0 次命中 `AQS`/`AbstractQueuedSynchronizer`/`ReentrantLock`/`锁`/`队列`/`线程`/`同步器`/`Condition`/`公平`/`Semaphore`/`CountDownLatch`；全文仅 1414 个汉字，107 个可见文本节点全是界面文字。
- **证据**：`_visible_text()`（`convert.py` 对应函数）先剥 script/style/标签；`consistency_check()`（`convert.py` 对应函数）对真实源返回 `keyword_hits=0`。`py convert.py "<源>" --check` 输出「专题关键词命中（可见正文）：0」。
- **正确写法**：文件名暗示专题、可见正文命中 0 个关键词 → `mismatch=True`，**告警但不阻断**，按源文件实际内容转换，并在 MD 的 `>` 注记里如实写明「文件名与内容不符、不含 AQS 正文」（`_build_note()`，`convert.py`）。用户拍板「仍按当前文件就地转换」后才转，绝不编造 AQS 技术正文。
- **如何避免**：先做「文件名 ↔ 内容」一致性预检再动手；把「不脑补」写成代码里的分支与注记，而不是靠人记。测试 `test_filename_body_mismatch_warns_without_blocking` 锁死该行为。

---

## 坑 2：在全文里数 `Lock` / `Node` / `CAS` 会被 CSS/JS 假命中骗过

- **现象**：源里 `Lock` 命中 502 次、`Node` 68 次、`CAS` 91 次，看着像「有并发编程内容」；实际全在压缩 CSS、webpack chunk、NUXT 数据里。
- **根因**：这些是 `padding-left`、`clientNode`、Vue 的 `class` 样式名等字符串，不是可见正文。直接对 `html.count('Lock')` 会把脚本/样式当正文。
- **证据**：`_visible_text()`（`convert.py` 对应函数）用 `re.sub(r'<script\b[^>]*>.*?</script\s*>', ...)` 与同款 `<style>` 片段剔除、再去所有标签；`consistency_check()` 只在 `_visible_text` 结果里 `count`。测试 `test_css_js_keywords_are_not_body_hits` 用 `<style>.Lock{...}</style><script>var Node=1,CAS=2;</script><p>课程表</p>` 断言 `keyword_hits=0`。
- **正确写法**：关键词计数前先剥 `<script>`/`<style>` 内容再剥标签；宁可少算也不要把界面脚本算成正文。
- **如何避免**：把「可见正文」的定义固化成纯函数，并写一条「CSS/JS 假命中」回归测试。

---

## 坑 3：通用 `pandoc`/整页文本提取会把推广页噪声全带进 MD

- **现象**：直接整页转文本会得到导航（首页/实战课/体系课/直播课/题库/…）、「精品课」角标、`1096人已观看`、「直播回放」按钮、二维码/公众号/微信小程序、分页、页脚版权（京ICP17012835号-1）、商务合作电话（刘先生 15501115188）、`window.__NUXT__` JSON 与远程封面背景图。
- **根因**：页面是 SPA/SSR 壳，正文与界面/广告混在同一份 HTML；黑名单永远列不全（还有 `data-v-*`、`style="background-image:..."`）。
- **证据**：DOM 探查确认 `a[href]` 仅 8 个且全为页脚链接；`data:image` 4 块均小图标；`home-wrap-box__title` ×2、`recent-carouse-list__item` ×4、`wrap-box-list__item` ×15。
- **正确写法**：**白名单提取**。只认 `FIELD_MAP`（`convert.py` 对应函数）里 8 个 class 命中的字段文本，`DROP_TAGS`（`convert.py` 对应函数）与 `DROP_TEXT_HINTS`（`convert.py` 对应函数）丢弃脚本/样式与推广词；其余文本天然不收。`parse_sections()`（`convert.py` 对应函数）产出结构化的两张表。
- **如何避免**：面对「去广告」需求，先问「到底要保留什么」，用白名单而不是「见到广告词就删」；`residual_noise()`（`convert.py` 对应函数）再扫一遍生成 MD 里是否漏了 `<div`/`data-v-`/`<script` 残留。

---

## 坑 4：`html.parser` 遇到 `<br>`、`<img>` 等 void 标签会破坏栈平衡

- **现象**：源里时间是 `直播时间：<br>03月19日 20:00~22:00`，还有大量 `<img>`/`<meta>`/`<link>`。若把每个开始标签都压栈、每个结束标签都弹栈，void 标签没有对应结束标签，`self.stack` 会越积越深，字段归属错乱。
- **根因**：HTML 的 void elements 没有闭合标签；`HTMLParser` 对 `<br>` 只回调 `handle_starttag`，对 `<br/>` 回调 `handle_startendtag`。
- **证据**：`_VOID_TAGS`（`convert.py` 对应函数）列出 `br/img/input/meta/link/hr/...`；`handle_starttag()`（`convert.py` 附近）命中即 `return` 不入栈，`handle_endtag()`（`convert.py` 附近）同样忽略，`handle_startendtag()` 空实现。
- **正确写法**：显式维护 void 标签集合，开始/结束都跳过；结束标签从栈顶向下找最近的同名标签（容忍受 HTML 里不严格嵌套），弹出时对 `section_title` 收缓冲、对 item 调 `_finalize_item()`（`convert.py` 对应函数）。
- **如何避免**：用 stdlib `html.parser` 做栈式提取时，第一件事就是处理 void 标签；写一条含 `<br>` 时间的 fixture 测试锁住时间格式。

---

## 坑 5：近期直播 4 场里 2 场没有 `head` 块，讲师缺失

- **现象**：`recent-carouse-list__item` 共 4 个。前两个有 `item-head-content__title`(主题)、`item-head-content__info`(讲师)、`item-head-content__time`(带「直播时间：」前缀) 和一整套 `list-item-content__*`（课程分类 + 时间）；第 3、4 个**只有** `list-item-content__title`=「JAVA高级互联网开发|进阶架构师直播课」与 `list-item-content__time`，没有讲师。
- **根因**：页面对「有讲师的活动课」和「无讲师的普通课」用了两套模板结构，字段不齐。
- **证据**：`_finalize_item()`（`convert.py` 对应函数）分支：`if head_title:` 取 head 的主题/讲师/时间并把 `content_title` 归入 `categories`；`else:` 主题/时间取 content、讲师填 `—`。真实源 `--check` 输出「近期直播 4 行」且第 3、4 行讲师为 `—`。
- **正确写法**：按「有无 head_title」分两类归一到统一的 `{topic, speaker, time}`；缺失字段用 `—` 占位，不要丢行、也不要把课程分类 `list-item-content__title` 误当成主题塞进有 head 的场次。
- **如何避免**：探 DOM 时逐个 item 看字段差异，别拿第一个的结构套全部；`test_recent_item_fields` 与真实源 4 行比对双重覆盖。

---

## 坑 6：时间字段脏（前缀、`<br>`、日期与时刻间缺空格）

- **现象**：head 时间是「直播时间：<br>03月19日 20:00~22:00」，content 时间是「03月19日20:00~22:00」（日期与时刻之间无空格）；往期时间同样紧凑。
- **根因**：HTML 里用 `<br>` 换行、文案带「直播时间：」标签，文本拼接后空白不可控。
- **证据**：`_clean_time()`（`convert.py` 对应函数）去「直播时间：」前缀、`re.sub(r'(\d{1,2}月\d{1,2}日)\s*(\d{1,2}:\d{2})', r'\1 \2', s)` 补空格；`_text()`（`convert.py` 对应函数）折叠空白。
- **正确写法**：时间统一走 `_clean_time`，输出 `03月19日 20:00~22:00` 这种与黄金基准一致的格式。
- **如何避免**：别直接用原始文本进表格；把「清洗」做成纯函数并写断言。

---

## 坑 7：单元格里的 `|` 会撑破 Markdown 表格，`/` 排版不一致

- **现象**：主题「JAVA高级互联网开发|进阶架构师直播课」含 `|`，直接拼进 `| a | b | c |` 会被解析成多一列；简介里「云原生架构师/Golang全链路」的 `/` 无空格，与已交付 MD 的 `云原生架构师 / Golang全链路` 不一致。
- **根因**：Markdown 表格以 `|` 为分隔符；源数据里斜杠前后留白不统一。
- **证据**：`escape_cell()`（`convert.py` 对应函数）先 `_space_slashes()`（`convert.py` 对应函数）把 `\s*/\s*`→` / `，再 `replace('|', r'\|')`，最后折叠空白；`render_markdown()`（`convert.py` 对应函数）所有单元格都过它。真实源第 11/12 行输出 `JAVA高级互联网开发\|进阶架构师直播课`。
- **正确写法**：所有动态单元格一律经 `escape_cell`，不要手拼；测试 `test_pipe_is_escaped_in_cell`、`test_escape_cell_collapses_whitespace` 锁死。
- **如何避免**：把「转义 + 空白归一」集中在一个函数，任何新字段进表都过同一道关。

---

## 坑 8：编码检测要处理 BOM、GBK 兜底与二进制误判

- **现象**：源是 UTF-8 无 BOM；但同类下载页可能是 UTF-8 BOM、GBK，甚至夹 NUL 字节的二进制。按单一 `utf-8` 硬解会 `UnicodeDecodeError` 或读出乱码。
- **根因**：网页/下载文件编码不统一；`gb18030` 是 GBK 超集，严格解码可安全兜底。
- **证据**：`detect_decode()`（`convert.py` 对应函数）：`b'\x00' in raw` → `(None, 'not-text')`；BOM `b'\xef\xbb\xbf'` 剥离；依次 `utf-8` → `gb18030` 严格解码；全失败 `not-text`。沿用 `encoding-fix/convert.py:15` 的判定思想。
- **正确写法**：返回 `(text|None, codec)`，调用方 `process_file()`（`convert.py` 对应函数）在 `text is None` 时抛可读错误、退出码 1。
- **如何避免**：编码检测写成纯函数 + 4 条单测（UTF-8 / BOM / gb18030 / 坏字节），别在业务代码里到处 `try decode`。

---

## 坑 9：默认就写盘 / 覆盖已交付文件 = 数据损失

- **现象**：一次性脚本常直接 `open(out,'w')`；重跑会覆盖上一轮已交付的 `某专题.md`，且没有回滚。
- **根因**：转换与写盘耦合，缺少 dry-run 与备份。
- **证据**：`process_file(path, apply=False, backup=True)`（`convert.py` 对应函数）默认 `apply=False` 一字节不写；`apply=True` 且目标已存在先 `shutil.copyfile(out, out+'.bak')`（`convert.py` 对应函数）；`main()`（`convert.py` 对应函数）dry-run 打印 MD 到 stdout 并在 stderr 提示 `[dry-run] 未写盘`。测试 `test_dry_run_does_not_write`、`test_apply_writes_and_backs_up_existing` 覆盖。
- **正确写法**：默认 dry-run；`--apply` 才写；写前 `.md.bak` 备份；`--check` 与 `--apply` 互斥（`parser.error`）。在临时副本上做端到端 `--apply` 验证，绝不拿已交付 MD 试。
- **如何避免**：把「只读默认」写进 CLI 契约和文档；真实源验证只用 `--check` + dry-run 比对，写盘验证用 `_e2e/` 临时目录，验证后清理。

---

## 坑 10：`residual_noise` 会把注记里提到的广告词误报为残留

- **现象**：MD 的 `>` 注记里如实写了「已去除…二维码/公众号引导…」；若残留检测对全文扫 `二维码`/`公众号` 之类词，会自己报自己。
- **根因**：注记是给读者看的说明，不是 HTML 残留。
- **证据**：`residual_noise()`（`convert.py` 对应函数）只查 `RESIDUAL_MARKERS`（`convert.py`，全是 `<script`/`<div`/`data-v-`/`<img` 等 HTML 标记），并 `if line.lstrip().startswith('>'): continue` 跳过引用行。测试 `test_quoted_note_is_ignored` 锁死。
- **正确写法**：残留检测只查**结构化标记**（标签、属性），不查广告词；引用行整体跳过。
- **如何避免**：想清楚「检测对象」是生成物里的 HTML 泄漏，不是文案内容。

---

## 坑 11：把「等价」说成「字节级完全相等」

- **现象**：脚本生成的注记由真实文件名/标题自动拼出，与上一轮手写交付 MD 的注记措辞不同（脚本版写「文件名暗示『某专题』专题…（可见正文命中 0 个专题关键词）」，手写版写「文件名为『AQS 核心底层专题』…」）。
- **根因**：手写注记是人工润色，脚本为了「如实 + 可复现」用数据拼装，措辞必然不同；这是刻意设计，不是 bug。
- **证据**：逐行 diff 命令结论 `golden_lines=34 dryrun_lines=34 differing_lines=1`，`differing_line_numbers=3`，`table_rows_golden=23 table_rows_dryrun=23`。设计文档 §4.6 明确「以真实命令输出为准，不谎称完全等价」。
- **正确写法**：汇报写「34 行中仅第 3 行注记不同，其余 33 行逐字符一致、23 行表格全等 —— 等价而非字节级全等」，并给出可复跑的 diff 命令。
- **如何避免**：验收口径先写进规格；汇报时拿命令输出说话，不靠「应该一样」的口头判断。

---

## 坑 12：环境里没有 `lxml` / `pytest`，脚本别引入第三方依赖

- **现象**：探 DOM 阶段用 lxml 很顺手（`_aqs_probe.py`），但 `py -c "import lxml"` → `ModuleNotFoundError: No module named 'lxml'`；仓库模板 `encoding-fix/` 用 stdlib `unittest`，没有 pytest。
- **根因**：探查脚本跑在带 lxml 的 bundled python，交付脚本跑在 `py` 3.11.4 标准环境；两者不是同一套解释器。
- **证据**：`convert.py` 顶部 `import` 仅 `argparse / html.parser / os / re / shutil / sys`；`requirements.txt` 为空说明；测试用 `import unittest` + `py -m unittest -v`。
- **正确写法**：交付脚本只用标准库；探查用的第三方库脚本用完即删，不进交付物。
- **如何避免**：写实现前先确认目标解释器能 import 什么；「探查工具」和「交付工具」的依赖要分开看。

---

## 坑 13：拿不在关键词表里的 token 写「假命中」测试 = 恒真假绿

- **现象**：首版 `test_css_js_keywords_are_not_body_hits` 用 `<style>.Lock{...}</style><script>var Node=1,CAS=2;</script>` 断言 `keyword_hits=0`，看起来在防「CSS/JS 假命中」，实际防不住。
- **根因**：`Lock`/`Node`/`CAS` **本身就不在** `TOPIC_KEYWORDS`（`convert.py` 附近）里。就算 `_visible_text` 完全不剥 script/style，计数依然是 0、`mismatch` 依然 True，用例永远通过。实测把 `_visible_text` 猴补成 `lambda h: h` 后该测试照样绿。
- **证据**：审查时临时猴补验证 —— `convert._visible_text = lambda h: h` 后 `keyword_hits = 0 | mismatch = True`，测试未失败。
- **正确写法**：夹具必须植入**真关键词**：`<style>.AQS{...}</style><script>var 线程=1;var 锁=2;var ReentrantLock=3;</script>`，并补一条对照组「同样的词出现在可见正文里必须被数到」（`test_real_topic_in_body_is_counted`），否则用例没有区分力。
- **如何避免**：写「不该被算进去」的测试时，先确认那些 token **在正确实现下确实会被算进去**；否则测的是空集。凡是对照类测试，都要配一条「正面必须命中」的反向断言。

---

## 坑 14：给所有 `/` 补空格会把 URL 拆成 `http: / / x.com`

- **现象**：`escape_cell('见 http://x.com/a/b 说明')` 输出 `见 http: / / x.com / a / b 说明`。
- **根因**：`_space_slashes`（`convert.py` 附近）用 `re.sub(r'\s*/\s*', ' / ', s)` 无条件处理每个斜杠；`escape_cell` 又把它套到**每一个**表格单元格，URL、路径里的 `/` 全被留白。
- **证据**：临时脚本实测 `escape_cell('见 http://x.com/a/b 说明')` → `'见 http: / / x.com / a / b 说明'`。真实源没暴露只因页面里没有 URL。
- **正确写法**：按需留白，不是全局替换。先用 `_URL_RE = re.compile(r'(?:[a-zA-Z][a-zA-Z0-9+.-]*://|www\.)[^\s|]+')` 把 URL 摘出来换成占位符，处理完再换回；补「URL 不被破坏」+「中文分类斜杠仍留白」两条用例。
- **如何避免**：任何「全局格式化」都要问「有没有不该动的子串」；用占位符保护的是最省心的做法。

---

## 坑 15：单例 `self._cur` 遇上嵌套 item 会静默丢行

- **现象**：两个 `recent-carouse-list__item` 嵌套时，只产出内层 `B主题` 一行，外层 `A主题` 静默蒸发（`len(topics) == 1`）。
- **根因**：收集器是单例 `self._cur`，内层开始标签直接覆盖外层；`handle_endtag` 反向扫栈弹出一批标签时，`_finalize_item` 又把 `_cur` 整体置 `None`，外层再闭合时已无数据。
- **证据**：临时脚本实测嵌套夹具 → `nested recent = [{'topic': 'B主题', ...}]`（仅 1 条）。相邻（非嵌套）item 正常，所以容易漏测。
- **正确写法**：收集器**随栈帧保存**（`self.stack.append((tag, field, item))`），闭合时只 finalize 自己那一层（`_finalize_item(kind, cur)`）；`handle_data` 落到最近一层 item（`for _, _, item in reversed(self.stack)`）。补「相邻与嵌套都零丢行」用例。
- **如何避免**：解析器的状态凡是「当前正在收集的条目」，都要绑定到栈帧而不是实例字段；测相邻还不够，模板嵌套才是常态。

---

## 坑 16：推广词过滤误伤白名单字段内的正文

- **现象**：`item-head-content__title` 的内容是「精品课：并发编程与AQS」时，主题被吞成空串，产出一行 `|  | — |  |`。
- **根因**：`handle_data` 里 `if any(h in data for h in DROP_TEXT_HINTS): return` 对**所有** data 生效，包括已经落在白名单字段内的正文。「精品课」既是角标文案，也可能是课程标题的一部分。
- **证据**：临时脚本实测 → `hinted recent = [{'topic': '', 'speaker': '—', 'time': ''}]`。另探得该过滤对真实源的产物**零影响**（`lines_changed = 0`）—— 它既没在真实页面上起作用，又埋了误伤风险。
- **正确写法**：推广词只用于拦截「**不属于任何白名单字段**的游离噪声节点」——先算出 `field`，再 `if field is None and any(...)`。补「主题含推广词不被吞」用例。
- **如何避免**：内容过滤的位置要在「已经确定这是不是要保留的地方」之后；先归类、再过滤，别先过滤、再归类。

---

## 坑 17：argparse 用法错误退出码是 2，不是 1

- **现象**：规格写「异常退出码 1」，但 `--check --apply` 互斥、文件不存在、缺参数实测退出码都是 `2`。
- **根因**：这些都是 argparse `parser.error()` 的产物，而 argparse 按 Unix 惯例对用法错误固定返回 `2`；只有 `try/except` 捕获的**运行期异常**才返回 `1`。
- **证据**：`py convert.py <file> --check --apply` → `mutex exit = 2`；不可解码文本 → `main()` 返回 `1`。
- **正确写法**：**别为迁就文档去改 `parser.error` 的返回码**（会破坏与其他 CLI 的一致性）。把文档写准确：运行期异常 = `1`，用法错误 = `2`；并补一条断言运行期异常返回 `1` 的用例。
- **如何避免**：规格里写「退出码」这类契约时，先实跑确认 argparse/工具链的实际行为，再落笔；文档与实现冲突时，先判断哪边是行业惯例。

---

## 坑 18：`.mht` 当纯文本读会「什么都抽不到」—— QP 转义把标签打断了

- **现象**：对 173448 字节的 `【未元物质】是怎样的一种能力？.mht` 跑 `--check`，输出「近期直播 0 行 / 往期回放 0 行」；直接 `detect_decode` 整份文件后再数标签，得到 `script_count=0 / style_count=0 / img_count=0` —— 而解包后真实页面里明明有 5 个 `answer_item`、105 段 CSS。
- **根因**：`.mht` 是 MHTML（MIME `multipart/related`），正文部段的 `Content-Transfer-Encoding` 是 `quoted-printable`。`=` 软换行与 `=3D`（即 `=`）、`=E9` 这类转义让 `<h1 id=3D"questionTitle">` 这种形态出现，标签边界全被打断，`html.parser` 自然认不出任何标签。
- **证据**：`is_mhtml(raw)` → `True`；`unpack_mhtml(raw)` 返回 14461 字符，其中 `<h1 id="questionTitle">`、`class="answer_item"` 均正常；解包前 `raw[:200]` 可见 `MIME-Version: 1.0` 与 `Content-Type: multipart/related; boundary="----MultipartBoundary--…"`。实现见 `convert.py`（`is_mhtml`）与 `convert.py`（`unpack_mhtml`）。
- **正确写法**：进解析前先判容器形状 —— `is_mhtml(raw)` 为真就用标准库 `email.message_from_bytes(raw, policy=email.policy.default)` + `msg.walk()` 取首个 `text/html` 部件，按 `part.get_content_charset()` 解码。`email` 会自动解 QP / base64，不需要自己写解码器。
- **如何避免**：拿到扩展名是 `.mht`/`.mhtml`，或者文件头出现 `MIME-Version` + `multipart` 时，**先问「这是容器吗」再去 decode 纯文本**。另一个信号：`detect_decode` 成功返回了文本，但标签计数全为 0 —— 基本就是容器/编码层没剥。

---

## 坑 19：广告块里 `answer_item` 是 0，但**紧邻**的其它 section 有 —— 必须按区段归类

- **现象**：搜狗问问页里有两个 `section.detail-wrap.out-sourcing-ad`（`#underBestAnswerPlaceHolder` 与 `#wangMengAd`）。若只按 `li.answer_item` 全局收集，会得到 5 条答案；若只按「有 answer_item 的 section」判断，又会漏掉整块广告的存在 —— 更危险的是把广告 section 里的文本混进答案列表。
- **根因**：答案归属由**所在的 section** 决定（`best-answer` / `other-answer`），不是由 `li` 自身决定。广告 section 里没有 `li.answer_item`，但它的兄弟 section 里有，因此「有没有答案」不能用来判断「是不是广告」。
- **证据**：`parse_generic(QA_SAMPLE)` → `best` 1 条、`others` 2 条；`test_ad_block_answers_are_excluded` 断言产物中不含「广告推广内容」。实现：`_GenericParser._role`（`convert.py` 起）在 `tag == 'section'` 时先判 `out-sourcing-ad` → 返回 `drop`，整棵子树不进文本流。
- **正确写法**：**role 判定按「整棵子树的容器语义」优先**，遇到广告容器直接返回 `drop`，`handle_data` 检查栈里任一层是 `drop` 就丢弃。同时把「区段归属」记在 section 帧上（`sec_best` / `sec_others`），答案闭合时按当前区段入 `best` 或 `others`。
- **如何避免**：写抽取器时先画一遍「容器 → 条目」的归属关系图，再决定 role。别用「有没有条目」反推「是不是广告」。

---

## 坑 20：腾讯文档表单页没有 `h1`/`h2`/`form`/`table`/`p`，只能靠框架 class

- **现象**：596124 字节的表单页，去 `script`/`style` 后仍有 149029 字符，但标签计数是 `h1=0 h2=0 h3=0 form=0 table=0 tr=0 td=0 p=0`；可见正文只有 848 字。标题、副标题、8 个字段全部由 `div` + `span` 承载。
- **根因**：这是一个 Vue/webpack 的富表单渲染器，语义标签被完全放弃，结构信息全在 class 上：`div.question`（字段容器）、`div.question-title > span.auto-ellipse`（字段名）、`div.question-subtitle`（说明）、`div.question-content > div.form-simple > textarea`（已填值）、`span.required-span`（必填 `*`）、`span.icon-number > i.inumber`（题号，两位数字串）。
- **证据**：`parse_generic` 对真实文件产出 8 个字段，`fields[0] = {'no': '1', 'label': '阿里云账号昵称', 'note': '登进阿里云，右上角个人信息里查看', 'value': 'libai4hao', 'required': True}`；形态判定 `div.question` = 8、`div.question-title` = 8 → `form`。实现：`_shape_of`（`convert.py` 对应函数）与 `_GenericParser._role` 的 `field`/`label`/`note`/`value`/`required`/`no` 分支。
- **正确写法**：形态判据用**计数阈值**（`div.question >= 2` 且 `div.question-title >= 2`）而不是「存在即成立」，避免单个偶发 class 误判整页。题号要拼 `i.inumber` 的两段数字，并去掉前导零（`'0' + '1'` → `'1'`）。
- **如何避免**：面对「现代前端渲染的页面」，先数一遍语义标签；全是 0 就直接去读 class 结构，别在 `h1`/`p` 上浪费时间。同时注意标题本身也带 `form-header` 前缀 —— **具体 role 必须判在噪声规则之前**，否则表单标题会被当噪声丢掉（这正是 `_role` 里把 `form-header-title-content` 放在 `NOISE_CLASS_HINTS` 检查之前的理由）。

---

## 坑 21：兜底逻辑一旦抢在白名单前面，黄金等价立刻破

- **现象**：加了 `parse_generic` 之后，最危险的不是它抽得不好，而是它把白名单直播页也接管了 —— 那个页面同样没有 `h1#questionTitle`、没有 `div.question`，会落到 `article` 形状，把整张直播表拍成几行段落，34 行的黄金产物直接报废。
- **根因**：形状判定（`_shape_of`）只回答「像哪种形态」，不回答「该不该由兜底处理」。两条路径的优先级必须由**调用方**显式决定，不能靠形状判据自然分叉。
- **证据**：`DispatchTest.test_live_path_never_calls_generic` 用 monkeypatch 把 `convert.parse_generic` 换成计数桩，跑完 `process_file(LIVE_SAMPLE)` 断言 `calls == []`；`test_non_live_file_uses_generic` 用对照组证明桩确实能被调到（否则又是恒真测试）。实现：`_render_any`（`convert.py` 对应函数）先 `parse_sections`，两表任一非空直接 `return`，只有皆空才 `parse_generic`。
- **正确写法**：把「白名单优先」写成**结构性不变量**（早返回），而不是注释里的君子协定；再用一条「桩未被调用」的测试锁死。
- **如何避免**：任何「新增兜底」都要先问：这条新路径会不会与已有路径重叠？重叠时谁优先？优先级要用代码结构和测试表达，不能只写在文档里。

---

## 坑 22：`article` 兜底只抽标题与段落，别对外承诺排版还原

- **现象**：`article` 形状对任意陌生页面都能产出「有点东西」的 MD，容易让人误以为它是通用转换器；实际上表格、代码块、嵌套列表、图片全都会丢掉。
- **根因**：兜底只按 `h1`/`h2` 取标题、`p`/`li` 取段落，是**保守提取**而非版式重建。写文档时若说「支持任意 HTML」，就是把能力边界说大了。
- **证据**：`ArticleShapeTest` 断言 `heading == '文章标题'`、`paragraphs == ['第一段正文。', '第二段正文。']`；README「运行风险与边界」明写「不还原表格、代码块、嵌套列表」。
- **正确写法**：能力边界写进 README 与规格，`article` 分支保持最小实现（YAGNI）。真要复杂排版，让用户用 pandoc，别在本工具里堆 feature。
- **如何避免**：兜底路径一律配一条「边界声明」，并让 `--check` 打印形态名 —— 用户一眼能看出「走的是兜底」。

---

## 坑 23：微信代码块没有字面换行，行边界是 `</li>`

- **现象**：8 个 `<pre>` 代码块全被压成单行，63 行 `ControlTable.cpp` 变成 `#include "controltable.h"ControlTable::ControlTable...`。
- **根因**：结构是 `pre > ol.list-paddingleft-2 > 每行一个 li > p > span > code > 语法高亮 span`，**行序由 `</li>` 表达**，HTML 内无 `\n`；`handle_endtag` 原用 `''.join(payload['text'])` 无分隔拼接。
- **证据**：探针统计 8 个 `pre` 的 `li` 数为 `1/20/31/63/68/228/16/24`；全文件 `<br` 94 但代码行边界不是 `<br>`。
- **正确写法**：在 `handle_endtag` 弹栈**之前**，若 `tag == 'li'` 且 `_code_frame()` 非 None，向该 code 帧 `payload['text'].append('\n')`；`_render_article` 的 `strip('\n')` 去尾换行。
- **如何避免**：`V3ArticleTest.test_code_block_restores_three_lines` 锁 3 行 / 2 个 `\n`；真实文件 dry-run 数 8 个块行数。

## 坑 24：Google 翻译双层 `<font>` 把一个逻辑段落切成多个 data 节点

- **现象**：「按钮命名格式为“ btn」「行号」「列号”，并且将这些QPushButton…」被拆成 3 个独立段落块。
- **根因**：`handle_data` 对每个 data 立即 `paragraphs.append(data)` + `blocks.append({'type':'para',...})`；翻译浮层用双层 `<font style="vertical-align: inherit;">` 包裹，503 个 `<p>` 里 406 个含 ≥2 个 font/span。
- **证据**：`convert.py` 旧实现；`@734987` 处单个 `<p>` 内含多个 data 节点。
- **正确写法**：`handle_starttag` 给 `art_para` 帧挂 `payload = {'text': []}`，`handle_data` 把 data 追加进缓冲，`</p>` 闭合时 `_squash(''.join(...))` 后一次性成块；复用 `:598` 的 `reversed(self.stack)` 遍历，**不需要新增 `_para_frame()`**。
- **如何避免**：`V3ArticleTest.test_paragraph_is_merged`；注意 `pre > li > p` 内的 `<p>` 缓冲为空，闭合时不产生空块。

## 坑 25：噪声 hint 按 class 子串匹配、不查 id；短前缀会误伤真标题

- **现象**：把 `id="goog-gt-tt"` / `id="js_profile_qrcode"` / `id="meta_content"` 写进 `NOISE_CLASS_HINTS` 完全不生效；`gh_41e8a1e75988`、「原文」残留。
- **根因**：`_classes(attrs)`（:394-399）只取 `class` 属性，`_role`（:453-454）是 `any(h in joined for h in NOISE_CLASS_HINTS)` 子串匹配。用 `'rich_media'` / `'profile'` 短前缀会连真标题 `h2#activity-name.rich_media_title` 一起丢。
- **证据**：实测对应 class 是 `skiptranslate` / `profile_container` / `rich_media_meta_list`；`id=` 值各只出现 1 次。
- **正确写法**：追加 3 个精确 class 名 `'skiptranslate'`、`'profile_container'`、`'rich_media_meta'`；`profile_container` 整棵丢弃即连带 `p.profile_meta` 的 `gh_`/`微信号`/`功能介绍`；`img_loading` 不用加（`img` 是 `_VOID_TAGS`）。
- **如何避免**：`V3ArticleTest.test_heading_and_noise_dropped` 同时锁「真标题保留」与「原文/gh_ 消失」。

## 坑 26：知乎搜索结果页不是文章，摘要在 `<span class="RichText">` 而非 `<p>`

- **现象**：20 个结果标题被拼成 1 个超长 H1，摘要全部丢失，导航 Tabs 被抽成 `- 综合/- 用户/...` 列表。
- **根因**：`_shape_of` 只有 qa/form/article 三形态 → 落到 article；`_role` 把 21 个 `h2` 全当 `art_heading`；`span.RichText` 没有角色，文本被漏掉。
- **证据**：`<title>(2 封私信 / 80 条消息) 2020 内循环 - 搜索结果 - 知乎</title>`；`div.Card.SearchResult-Card` ×21、`ContentItem-title` ×20；`<p>` 计数 0。
- **正确写法**：`_is_zhihu_search` 双信号（`<title>` 含「搜索结果」+「知乎」或 `/search?`，**且** `ContentItem-title` ≥2）新增 `search` 形态；独立 `_ZhihuSearchParser` 按 title/summary/related 白名单 zone 抽取，卡片闭合用 **div 深度计数**（卡内有多层嵌套 div）。
- **如何避免**：`V3ZhihuSearchParseTest.test_two_cards_extracted`（夹具刻意保留嵌套 div）；问答详情页有 `h1#questionTitle` 时第 1 优先级已返回 qa，不会误判。

## 坑 27：微信正文图地址在 `data-src`，`img` 是 void tag

- **现象**：11 张 `mmbiz.qpic.cn` 正文图不进产物。
- **根因**：真实地址在 `data-src` 而非 `src`；`img` 属 `_VOID_TAGS`（:55-56），`handle_starttag` 直接 return，不产文本节点。
- **证据**：DOM 探查 `data-src` ×11；`RESIDUAL_MARKERS` 要求产物无 `<img`。
- **正确写法**：本阶段**明确不做**图片还原（spec §4）；若要做得另起范围处理 `data-src` 抓取与落地，不能靠噪声 class hint。
- **如何避免**：验收只核对代码/文字完整；README「已知限制」写明图片不进产物。

## 坑 28：文件名与搜索页主题不符，`consistency_check` 只认 AQS 专题词

- **现象**：`公租房时代已经来临！？ - 知乎.html` 的 `--check` 仍显示「文件名与内容无明显冲突」。
- **根因**：`公租房` 16 次命中全在 `_files/` 资源路径、可见正文 0 次；`FILENAME_HINTS = TOPIC_KEYWORDS + ('并发','专题','底层')`（:49）不含「公租房」。
- **证据**：`consistency_check`（:127-139）的 `mismatch = bool(name_has_topic and keyword_hits == 0)`。
- **正确写法**：本阶段**不改** `consistency_check`（v1 遗留、影响面大）；由 `_search_note` 的 4 行引用注记并置展示「保存文件名 / 搜索词 / 结果数 / 原页面 URL」，并写「若与上列页面实际主题不一致，一律以页面实际内容为准」，诚实覆盖不符事实，**不臆断、不做自动不符判定**。
- **如何避免**：`V3SearchRenderTest.test_render_any_adds_honest_note` 锁 4 个关键短语；注记行以 `> ` 开头，`residual_noise` 会跳过。

## 坑 29：`render_generic` 的 label 字典是直接索引，新增形态必须同步加键

- **现象**：新增 `search` 形态后若只改 `render_generic`，`--check` 会 `KeyError: 'search'`。
- **根因**：`_render_any`（:923）用 `{'qa':'问答','form':'表单','article':'通用文章'}[shape]`，不是 `.get()`；`render_generic` 的 `else` 默认走 `_render_article`，`search` 数据取 `data['article']` 也会 `KeyError`。
- **证据**：`convert.py` 的搜索页解析与渲染函数。
- **正确写法**：`render_generic` 加 `elif shape == 'search': body = _render_search(data, note)`；`label` 字典加 `'search': '知乎搜索结果'`；generic 分支算 `note = _search_note(...) if shape == 'search' else None` 并传入。**直播页分支一行不动**。
- **如何避免**：`V3SearchRenderTest.test_render_any_adds_honest_note` 与 `DispatchTest.test_search_path_uses_generic` 覆盖「经 `_render_any`」路径；`test_note_ignored_by_other_shapes` 覆盖「直调 `render_generic`」路径。

---

## 坑 30：段落合并会让「按整块文本精确匹配」的噪声过滤静默失效

- **现象**：微信图文产物末行残留 `微信扫一扫赞赏作者 赞赏`；`residual_noise` 仍报 0（该短语不在 `RESIDUAL_MARKERS`），`--check` 也看不出来。
- **根因**：v3 的段落合并把原本两个**各自**能被 `ARTICLE_BLOCK_NOISE_TEXTS` 精确匹配丢弃的碎片（`微信扫一扫赞赏作者`、`赞赏`）拼成一整串，精确相等的 `t in frozenset` 判定随之失效。真正的源头是赞赏浮层根容器 `div#js_reward_area` 的 class 是 `reward_area tc reward_area_primary ...`，而 `NOISE_CLASS_HINTS` 只有 `reward-box`，整个浮层没被整棵丢弃。
- **证据**：TEMP 探针 `parse_generic` 后最后一个 `para` 块即 `'微信扫一扫赞赏作者 赞赏'`，`_is_article_noise` 对它返回 `False`，但对两个碎片分别返回 `True`；内存 monkeypatch 追加 `reward_area` 后该块消失、8 个代码块行数不变、无新增行。
- **正确写法**：**从源头整棵丢弃容器**——给 `NOISE_CLASS_HINTS` 追加 `reward_area`（class 子串，同时覆盖 `reward_area_primary` / `reward_area_carry_whisper` / `reward_qrcode_area`）。不要改成「噪声词子串匹配」去补：探针证明正文 `您好…今天给大家分享个比较有趣的小示例` 含「分享」但**是正文**，子串法会误杀。也不要把合并后的整串塞进精确集合：那是追着碎片组合打补丁。
- **如何避免**：`V3ArticleTest.test_reward_area_container_is_dropped` 用内联最小复现锁住「容器丢弃 + 正文段落保留」；**验收不能只 grep 已知的几项噪声短语**，最终必须把 `--apply` 产出的文件从头到尾读一遍（这次残留正是读落盘产物时发现的，dry-run 的关键词清单没覆盖它）。

---

## 一句话总结

**先证「文件名≠内容」再动手、用 class 白名单只取要保留的、关键词只数可见正文、单元格统一转义、默认只读且写前备份、汇报拿命令输出说话** —— 这五条把一次性的「去广告转 MD」变成了可复跑、可核对、不编造内容的工具。

再加三条来自代码审查的教训：**测试里「不该被算进去」的样本，必须先确认在正确实现下它确实会被算进去**（否则测的是空集）；**任何全局格式化都要保护不该动的子串**（URL）；**解析器的「当前条目」必须绑栈帧**（嵌套是常态，单例必丢行）。

v2 再补四条：**先判容器再解文本**（`.mht` 是 MIME 容器，QP 转义会打断标签）；**广告归属看容器不看条目数**；**无语义标签的现代前端页面只能读 class，且具体 role 要判在噪声规则之前**；**兜底路径必须让白名单先行，优先级用早返回 + 「桩未被调用」的测试锁死，而不是写进注释**。
