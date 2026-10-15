---
name: academic-reading-master
description: 论文三遍精读工作流（初筛→概述级大致理解+双语伴读阅读器→定点打开黑盒答疑与Gap挖掘）+ PaperVault 文献资产库与主题图谱管理。当用户要读论文/精读论文/批量初筛论文/生成双语对照阅读页/在论文上写批注并让AI答疑/找研究空白 Research Gap/整理文献矩阵或主题框架/迁移文献库，或提到 PaperVault、三遍读法、论文文献卡片时使用。支持 PDF / arXiv / DOI 输入。
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, WebSearch, WebFetch
---

# Academic Reading Master — 论文三遍精读工作流

按凯沙夫三遍读法组织论文阅读：**Pass 1** 鸟瞰初筛（可批量，会话内直接出结论）→ **Pass 2** 概述级大致理解（总览示意图 + 流水线卡片 + 复述自测 + 双语伴读阅读器，技术当黑盒、语言通俗优先，按整库挡位分新手档/熟练档）→ **Pass 3** 深度精读（用户批注哪里就定点打开哪个黑盒 + 边界剖析 + Gap 挖掘，产出同步进阅读器"深度剖析"区）；多篇同类后可选**阶段四**主题框架沉淀（跨论文方法图谱）。每个 Pass 的产出全部落盘到 PaperVault 文献库，支持跨会话断点续读；用户与 AI 的交互载体是**批注**（annotations.json 双向写回：阅读器经本地服务自动写 note，AI 写 answer）。

## 四大红线（任何情况下不得违反）

1. 🔴 **节奏红线**：每个 Pass 结束都是强制停下点，必须等用户明确指令才进入下一遍。Pass 1 出完筛选表即停（等用户回复编号）；Pass 2 部署并打开阅读器即停（等用户读完、批注完并主动说"进第三遍"）；Pass 3 的答疑/边界/Gap 三段之间各停一次确认。**严禁一口气跑完三遍**——渐进的意义就在于让用户掌握节奏、按需消耗上下文。
2. 🔴 **上下文红线**：只 Read 当前 Pass 需要的 reference 文件（见各 Pass 路由），不预读其他 Pass 的规范；每个 Pass 的产出立即写盘，不把待持久化的内容留在对话里。
3. 🔴 **通用性红线**：方法拆解框架必须适配任意论文。严禁把某一篇具体论文的模块名、损失函数、训练策略、数据集当成"通用标准"写进任何产出——那是单篇论文的内容，不是规范。
4. 🔴 **深度边界红线**：Pass 2 只做到"大致理解"——技术一律黑盒（名称+一句话+输入→效果），严禁提前展开超参、损失逐项、训练细节；这些实现细节属于 Pass 3（用户批注问到才打开，要复现才补全）。同时 sections 双语正文仍按原文完整还原（translation_rules.md）。

## PaperVault 库定位（每次执行先做）

按顺序确定文献库位置：

1. 用户本轮消息中显式给出的路径；
2. 当前工作目录下的 `PaperVault/`（内含 `library.json` 即有效）；
3. 都没有 → 用 AskUserQuestion 询问：新建库（在哪个目录）/ 指定已有库路径 / 从外部资产包导入（zip 或文件夹均可）。

**挡位确认**（定位库之后）：Read `library.json` 的 `level` 字段，缺失时用 AskUserQuestion 问一次——**新手档**（非常通俗，把用户当成刚上大学的学生：术语首现必解释、每步有比喻）/ **熟练档**（默认密度）——`vault_manager.py --set-level novice|expert` 写入，整库沿用；用户说"切换挡位"即改（新论文生效，旧论文更新需重跑 Pass 2）。

库结构（数据资产，与 skill 程序完全分离，整体拷走即完成迁移）：

```
PaperVault/
├── library.json                     # 唯一权威状态源
├── themes/<topic>.md                # 阶段四产出（主题图谱）
└── papers/<paper_id>/
    ├── source.pdf / assets/         # 原文与提取图表
    ├── metadata.json                # Pass 1 产出
    ├── paper-data.js                # Pass 2 唯一产出（阅读器数据，非 HTML）
    ├── reader.html / reader.js / styles.css   # deploy_reader.py 复制的固定模板，永不重写
    ├── annotations.json             # 批注（双向：阅读器经本地服务自动写 note，AI 写 answer）
    ├── 打开阅读器.bat                # 双击自启本地服务 + 阅读器（open_reader.py 生成）
    └── notes.md                     # Pass 3 产出（最终成果：笔记+卡片+引文）
```

## 状态机与断点续读

```
unread ──[Pass 1]──▶ triage_passed ──[Pass 2]──▶ reading ──[Pass 3]──▶ completed
   └──────[Pass 1]──▶ rejected（明确不读）
```

- 状态流转用 `python "${CLAUDE_SKILL_DIR}/scripts/vault_manager.py" --vault <库路径> --status <paper_id> <新状态>`；
- 用户说"继续读 X / 这篇读到哪了"：先 Read `library.json` 查该篇 status，从对应 Pass 断点继续（已落盘的 metadata/paper-data 不重做，只补缺的部分）；
- 常见意图映射：「初筛 / 帮我看看这几篇」→ Pass 1；「第 N 篇继续 / 精读它」→ Pass 2；「请回答 / 回答批注 / 看看我的批注」→ **答疑动作（Pass 2/3 期间随时触发，不必等第三遍）**：Read annotations.json → 只答 `answer` 为空的 → 逐条写回（页面 20 秒内自动更新，无需刷新）；「进第三遍」→ Pass 3；「整理 XX 图谱 / 主题框架 / 文献矩阵」→ 阶段四；「我的库 / 文献列表」→ Read library.json 输出状态总览表。

`paper_id` 规则：`<year>_<小写短横线slug>`（如 `2026_anigen_unified_s3_fields`）。

## Pass 1 — 鸟瞰初筛（可批量，轻量：结论直接在会话中输出）

进入本遍时 Read：`references/pass1_triage_guide.md`。

> 定位：Pass 1 不生成任何 HTML/阅读器——筛选结论**直接输出在会话中**（批量总览表），同步落盘 `metadata.json` 与 library.json 登记即可。

1. **输入**：本地 PDF / arXiv 链接 / DOI / 论文标题，支持一次多篇。PDF 用 Read 工具分页读（超过 10 页必须用 `pages` 参数，每次不超过 20 页）；抽取出的文本混乱（扫描版）时，渲染相关页面并让当前可用的多模态模型直接识别；若当前模型不支持图像输入，改用其他可用视觉模型/工具；arXiv/DOI 可先 WebFetch 获取摘要与元数据；
2. **原文 PDF 由 AI 自动入库**（用户不手动拷文件）：输入为链接/DOI/标题时，自动下载真 PDF 存为 `papers/<paper_id>/source.pdf`——多镜像依次尝试（`https://export.arxiv.org/pdf/<id>`、中科院镜像 `https://xxx.itp.ac.cn/pdf/<id>`、项目页/出版方 PDF 链接），校验 `%PDF` 文件头与页数后才落盘；直连慢就 `run_in_background`，全部失败才请用户提供文件。**红线：拿到真 PDF 之前，任何产出（metadata/正文/批注/流水线）严禁出现具体数字或指标**——可写定性描述，数字必须能在原文中找到出处；
3. 按指南提取 5C + 六要素简版 + 证据置信度 + 推荐优先级，逐篇写入 `papers/<paper_id>/metadata.json`，并用 vault_manager.py 登记入库；
4. 输出**批量初筛总览表**（编号 / 中英标题 / 年份会议 / 一句话方法 / 置信度 / 推荐度）；
5. **🔴 停**：明确问用户"回复编号选择深入阅读哪几篇，或说明哪些不读"。按答复把选中篇流转为 `triage_passed`、明确不读的流转为 `rejected`。用户答复前，不得生成任何阅读器内容。

## Pass 2 — 概述级大致理解 + 双语交互阅读器

进入本遍时 Read 这四个：`references/pass2_workflow_guide.md`、`references/paper_data_schema.md`、`references/translation_rules.md`、`references/annotation_guide.md`。

1. 判定论文类型，按 pass2 指南拆解：pipeline 节点（goal/input/tech 黑盒/gain/benefit_next）+ `novelty`（一句话创新点）+ `self_check`（复述自测题）+ **diagram 示意图**（方法结构/数据流向/对比，先见图后见细节）——解释文字一律**通俗优先**，且**按 library.json 挡位生成分级内容**（新手档：术语首现必通俗解释、每步有比喻；记录顶层 `level`）；每个 section 标注 `page`（PDF 起始页，供原文侧栏联动跳页）；
2. **提图**：`python "${CLAUDE_SKILL_DIR}/scripts/extract_pdf_assets.py" <pdf路径> <论文目录>/assets`（依赖 PyMuPDF，缺失则提示安装，或跳过插图仅用文字描述图表）；
3. **生成 `papers/<paper_id>/paper-data.js`**——本遍唯一产出文件，结构严格遵循 paper_data_schema.md v2.2。**text_zh 必填、text_en 不再全文收录**（原文由阅读器 PDF 侧栏呈现，仅关键句保留对照）。超长论文分段写入：先 Write 只含骨架与第 1 个 section 的文件，再按 section 用 Edit 逐段追加，防止单次写入过长被截断；
4. **部署**：`python "${CLAUDE_SKILL_DIR}/scripts/deploy_reader.py" --vault <库路径> --paper <paper_id>`（复制 reader.html/reader.js/styles.css 到论文目录，并校验 paper-data.js 语法；校验失败会指出 JSON 错误位置，修复后重跑）；
5. **打开（服务模式）**：`python "${CLAUDE_SKILL_DIR}/scripts/open_reader.py" --vault <库路径> <paper_id>` 以 run_in_background 运行——起本地服务并自动开浏览器，批注**零弹窗**直写论文文件夹，同时在论文目录生成"打开阅读器.bat"供用户日后双击自启；状态流转为 `reading`；
6. **🔴 停**，向用户交代阅读分层路径与批注用法：
   > "建议先只读**顶部总览图 + 流水线卡片 + 自测题**（5 分钟概述级）：能复述'解决什么问题/分几步/每步什么技术什么效果/创新点是什么'即完成大致理解，可进第三遍或先读下一篇同类；答不上的题再下钻对应章节正文，想对照原文点顶栏'原文'开 PDF 侧栏（点章节标题自动翻页）。任何看不懂的词句/公式/图表，**划词、或悬停板块点铅笔图标写批注**（可反复编辑）——批注全自动保存，无需任何操作；**攒了批注随时回来说'请回答'，我立即逐条答复并写回页面（约 20 秒自动更新，无需刷新）**；想深挖边界与创新点就说'进第三遍'。"
   
   此前不得读取 annotations.json 或开展任何 Pass 3 内容。

## Pass 3 — 定点打开黑盒：批注答疑 + 边界 + Gap

进入本遍时 Read：`references/pass3_deep_mining_guide.md`、`references/gbt7714_rules.md`、`references/notes_template.md`。

1. **答疑段**（此动作在 Pass 2 期间也可被"请回答"随时触发，见意图映射；进第三遍时先扫一遍补漏）：Read `papers/<paper_id>/annotations.json`（严禁要求用户复制批注）。**只处理 `answer` 为空的批注**，逐条按「白话类比 → 机理 → 工程」三层打开该黑盒（深度只到用户问的为止），**每答完一条立即 Edit 写回该条的 `answer` 与 `answered_at`**（阅读器每 20 秒自动检测写回并在批注原位更新，无需刷新）；多条同根源可合并但逐条写回；全部答完告知用户回答已写回、页面会自动更新，再问是否进入下一段。用户后续补批注再喊你 → 只答新批注（多轮循环）；
2. **边界段**：先问"要继续隐式假设与失效边界剖析吗？"确认后输出：隐式假设清单 / 失效场景 / 复杂度与硬件成本（每条注明论文出处或标注推断），**并把结果 Edit 写入 paper-data.js 顶层 `pass3.boundary`**（不动 sections/blocks），重跑 deploy_reader.py 让阅读器文末"深度剖析"区同步显示；
3. **Gap 段**：先问"要继续挖掘可拓展创新点吗？"确认后输出 3 阶 Gap（渐进改进 / 跨域结合 / 痛点攻坚，各含改进点、呼应的局限、难度、最小验证路径），**同样写入 `pass3.gaps` 并重新部署**；末尾主动询问用户的研究方向以定制；
4. **可选·复现级细节补全**：用户明说"我要复现"时，按 pass3 指南的类型深挖检查单逐项补全（超参、损失逐项、训练流程、装配伪代码），论文未披露项列出并标注推断；
5. **沉淀**：三段完成后按 notes_template.md 生成 `papers/<paper_id>/notes.md`（批注答疑归档进 ④，边界进 ⑤，Gap 进 ⑥），状态流转 `completed`；
6. **个人精读版 PDF（标准收尾，主动引导一次）**：阅读器内点**打印按钮 → 目标选"另存为 PDF"**——得到自包含精读版：全文译文 + AI 精读批注 + 你的划词与提问 + 每条 AI 回答 + 深度剖析（边界/Gap）；未回答的批注以"待回答"占位卡保留问题。备选：`notes.md` 原生 Markdown（Obsidian 兼容）/ docx（`python "${CLAUDE_SKILL_DIR}/scripts/md2docx.py" <notes.md> -o <notes.docx>`）/ 无头矢量 PDF（`html_to_pdf.py`，需 Playwright）。

## 阶段四（可选）— 主题框架沉淀（跨论文方法图谱）

进入时 Read：`references/theme_matrix_guide.md`。

- **触发**：某主题/tag 下 ≥3 篇 reading/completed，且用户说"整理 XX 图谱 / 主题框架 / 文献矩阵"；
- 产出 `PaperVault/themes/<topic>.md`：论文×方法矩阵（保持黑盒措辞）+ 方法速查表（效果/出处/深入入口检索词）+ 开放问题汇总（衔接 Gap）；已有图谱**增量更新**不整篇重写。

## 迁移与库管理

- **打包迁移**：整个 PaperVault 文件夹自包含（阅读器全部走相对路径），zip 拷走即可在任意机器使用；`vault_manager.py --export [zip路径]` 可直接出包；
- **导入合并**：`vault_manager.py --vault <目标库> --import-vault <外部库路径或zip>`，按 paper_id / DOI 去重合并；
- **库总览**：`vault_manager.py --list`，或在对话中输出更友好的状态表（在读 / 待读 / 已读 / 不读 + 优先级 + 一句话方向）。

## 依赖

Python 3；PyMuPDF（提图）、python-docx（docx 导出）、Playwright（可选，矢量 PDF）。缺依赖时按脚本内提示安装，或走降级方案（浏览器打印导出 PDF、跳过插图）。
