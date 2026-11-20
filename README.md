# html-md-toolkit

把富文本网页（微信公众号"另存网页"、图片型图文消息、mdnice 导出页）转成干净、可归档的 Markdown。
一套脚本 + 一份 SOP，服务"网页 → 笔记"这一类任务。

## 目录

| 路径 | 内容 |
|---|---|
| `脚本/html_to_md.py` | 分支 A：正文在 `#js_content` 里的普通富文本 HTML（BeautifulSoup + markdownify） |
| `脚本/wechat_picture_page.py` | 分支 B：图片型图文消息（正文是 N 张长图，靠 JS 注入） |
| `脚本/convert.py` | 分支 C：白名单 class + 多形态兜底（问答 / 表单 / 文章 / 知乎搜索、`.mht` 解包），零第三方依赖 |
| `脚本/html2md_batch.ps1` | 分支 D：pandoc 批量转换整棵目录 / 通配符，并行 + 增量 + 日志 |
| `脚本/md_verify.py` | 成品校验：BOM / frontmatter / 图片 / 空白 / 围栏 / 渲染 |
| `tests/test_pipeline.py` | 19 项回归（标准库 unittest，无需 pytest） |
| `tests/test_convert.py` | 82 项回归，覆盖 `convert.py` 的编码、白名单、四形态与 CLI 安全 |
| `SOP-富文本HTML转Markdown.md` | 分支判定、参数表、人工步骤、坑清单、验收标准 |
| `docs/形态兜底转换器.md` | 分支 C 的设计取舍、四形态判据、边界与自测 |
| `docs/批量转换-pandoc.md` | 分支 D 的参数、镜像规则、增量与日志 |
| `docs/PITFALLS-形态兜底.md` | 30 条踩坑记录（现象 / 根因 / 证据 / 写法） |
| `config/git-hooks/pre-commit` | 预提交门禁（密钥、二进制、单文件 5 MB、脚本非 ASCII） |

## 快速开始

```powershell
# 分支 A：普通富文本 HTML
python 脚本/html_to_md.py 文章.html -o 笔记.md

# 分支 B：图片型图文消息（先抓骨架 + 图片，再由人逐张转录）
python 脚本/wechat_picture_page.py "https://mp.weixin.qq.com/s/<id>" -o 笔记.md --drop "1,17"

# 分支 C：版式固定、噪声极多的单页（白名单优先 + 多形态兜底）
python 脚本/convert.py 页面.html --check      # 只读预检
python 脚本/convert.py 页面.html --apply --frontmatter   # 写盘 + YAML 头（归档用）

# 分支 D：整棵目录 / 通配符批量（需要 pandoc）
.\脚本\html2md_batch.ps1 -OutDir md_output -Incremental

# 成品校验（归档前必跑）
python 脚本/md_verify.py 笔记.md --strict

# 回归测试
python -m unittest discover -s tests -v
```

退出码：`wechat_picture_page.py` 图片失败 → 2；`convert.py` 运行期异常 → 1、用法错误 → 2；`html2md_batch.ps1` 有失败文件 → 1；`md_verify.py` 硬缺陷 → 1，参数/文件错误 → 2。

## 依赖

Python 3.10+（实测 3.13），分支 A 需要 `beautifulsoup4`、`markdownify`；渲染校验用 `markdown-it-py`（未装则跳过渲染项）。
分支 B 需要本机 Edge/Chrome 做无头渲染（`--dump-dom`）。分支 C 只用标准库。分支 D 需要 PowerShell 7 + pandoc。

## 约定

- 输出 md 一律 UTF-8 **无 BOM**、LF，frontmatter 才能被常见笔记软件正确解析。
- 图片只作为转录素材：**转录完成后连同 md 里的 `![]()` 行一起删除**，正文只留一句来源说明；确需保留插图时才落到 `assets/<md 名>/NN.jpg` 并用相对路径引用，任何情况都不写远端 CDN 图（需 Referer，且会失效）。
- 对比卡不用 Markdown 表格（CommonMark 不渲染表格），改成成对的分组列表。
- 归档前必过 `md_verify.py --strict`；它要求 YAML 头，所以分支 C 归档时加 `--frontmatter`，分支 D 的产物需另行补头（或先当草稿）。

## 维护

改口径时三处一起改：脚本 → `SOP` 对应章节 → `tests` 断言。只改一处，下次跑出来的结果就跟文档对不上。
