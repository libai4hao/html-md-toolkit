# 批量转换（pandoc · `脚本/html2md_batch.ps1`）

把**一堆** HTML 一次性批量转成 GFM Markdown：一条命令、并行转换、输出到独立目录、不碰原文件。

三个分支的分工：

- 单个富文本页面、要求"干净可归档"→ `html_to_md.py`；
- 单个富文本页面、版式固定且噪声极多 → `convert.py`；
- **整棵目录 / 通配符批量**、优先覆盖率与速度 → 本脚本（pandoc 兜底，表格、代码块、列表都能还原）。

## 用法

```powershell
# 转换当前目录下所有 html → md_output/（镜像源目录结构）
.\脚本\html2md_batch.ps1

# 转某个目录（或通配符）
.\脚本\html2md_batch.ps1 "<源目录>"
.\脚本\html2md_batch.ps1 "docs\*"

# 换输出目录、限制并行度
.\脚本\html2md_batch.ps1 -OutDir out -Throttle 4

# 输出到绝对路径
.\脚本\html2md_batch.ps1 -OutDir "<输出目录>"

# 只转有变化的文件、把本次摘要写进日志
.\脚本\html2md_batch.ps1 -Incremental -Log convert.log
```

| 参数 | 默认 | 作用 |
|---|---|---|
| `Source`（位置参数） | `.` | 目录 / 通配符 / 单文件 |
| `-OutDir` | `md_output` | 输出根目录（相对拼当前目录，绝对原样用） |
| `-Throttle` | `0`=自动 | 并行度（0 = 用满 CPU 逻辑核） |
| `-Incremental` | 关 | 跳过「已存在且源未变」的 md |
| `-Quiet` | 关 | 不逐条打印成功文件 |
| `-Log` | 无 | 追加一行运行摘要（时间戳、源、找到/待转/跳过/成功/失败、耗时、输出） |
| `-IncludeBuildArtifacts` | 关 | 连 `node_modules` / `build` / `dist` / `.venv` 等一起转 |

## 行为

- 输出树**镜像**源目录结构，多个 `index.html` 不会互相覆盖；
- 源在当前目录之内 → 相对当前目录；源在其外 → 相对其父目录，保留源目录名；
- 默认跳过构建与缓存目录（`node_modules`、`build`、`dist`、`.pubcache`、`_probe`、`.git`、`.venv`）；
- 不修改任何源文件；有失败文件时退出码 1，可直接串流水线。

## 环境

- Windows + PowerShell 7（`ForEach-Object -Parallel` 需要）；
- pandoc 加入 PATH（`winget install JohnMacFarlane.Pandoc`），md 方言固定 `-t gfm`。

## 已知限制

- pandoc 无法 100% 剥离源 HTML 残留的 `<div>` / `<span>` 包装，不影响渲染但会让 diff 变吵；要完全纯净的成品，转完再过一遍 `md_verify.py`；
- 前端 SPA 壳（`<div id="app">` + JS）转出的 md 几乎为空，属预期；
- 这是"看得懂就行"的批量兜底，不替代人工挑选与转录（图片型图文消息仍走 `wechat_picture_page.py`）。

## 验证经验

- 目录源（当前目录之外）32 个 `doc/*.html`：全部成功，输出树正确镜像；
- 增量复跑：`待转换 0`，秒级返回；
- 通配符源：只转匹配到的文件；
- 32 个文档在 16 核机器上并行约 14–15 秒，串行需 3 倍以上时间。
