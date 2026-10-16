# Academic Reading Master · 通用版

面向**新晋研究生、刚开始科研的本科生，以及需要建立论文阅读方法的科研新手**的论文阅读 skill，适用于理工、人文社科及跨学科研究。

从找到值得读的论文开始，帮助读者理解研究问题、概念、研究路径与论证，并对照原文提问，再把阅读和实际研究进展整理为可以提交的周报。讲解重视必要背景、术语含义、推理过程和研究依据；原文涉及公式时解释其用途，让新手逐步学会判断与表达。

## 版本与分支

| 分支 | 定位 | 适合的阅读任务 |
|---|---|---|
| [main](https://github.com/noblespear/academic-reading-master/tree/main) | 工科特化版 | 方法、算法和系统论文；重点解释实验效果、性能与资源开销 |
| [general-purpose](https://github.com/noblespear/academic-reading-master/tree/general-purpose) | 通用版，基于 GitHub v3.1.1 | 理工、人文社科及跨学科论文；按研究类型解释论证与发现，实验、指标和资源分析按需展开 |

两个分支使用同一 skill 名称和数据格式，安装时选择一个版本。已有阅读材料沿用原内容与稳定 ID。

当前为 **general-purpose 通用版**，从 GitHub 的 v3.1.1 提交 `3c3cddc` 派生，版本标识为 `3.1.1-general-purpose`。保留原有阅读器、按需翻译与批注功能，调整内容指导、脚手架与相关界面措辞；原 v3.1.1 标签及 Release 保持原样。

## 可以做什么

| 功能 | 使用方式与产物 |
|---|---|
| 论文搜集 | 按主题检索，或导入 PDF、链接、DOI、标题；记录来源、筛选理由与全文获取状态，去重后保存书目 |
| 一体化阅读 | 同一网页切换**概览、详解、翻译**，分别记住阅读位置；全文翻译由读者点击生成，译文与英文按段落、公式和图表对齐，保留原 PDF 入口 |
| 图文讲解 | 随文插入并解释原论文图表，配简洁研究框架图和必要的局部示意；需要时通过交互观察对象关系与参数变化 |
| 批注答疑 | 三个视图均可划词提问，图表和公式可整块批注；问题提交后自动排队，答案显示在可折叠问答浮层，完成时提示新回答 |
| 连续追问 | 在原批注中继续提问、编辑、删除或重试；同一论文复用答疑会话，刷新后保留问答历史 |
| 调研类周报 | 根据选定论文、最终 HTML 与批注互动，整理研究比较、论证依据、阶段认识和下一步，输出 `.md` |
| 工作类周报 | 根据实际分析、调查、访谈、田野、实验或开发等记录，说明目标、实施过程、结果与问题，输出 `.md` |
| 个人讲解说明 | 默认与周报配套输出另一份 `.md`，解释被压缩的推理过程，并准备老师可能追问的问题 |

概览保留五个章节：**问题背景、研究框架、主要贡献、论证与发现、适用范围与局限**。论证可以来自实验、观察、访谈、文本、史料、理论证明或文献综合；实验、量化指标、公式、代码与开销分析只在相关时展开。详解依照论文原章节顺序，补足必要背景、概念、材料语境和中间推理。

研究框架图呈现论文实际的研究路径、概念关系或论证结构。理论、解释性研究与综述按其真实关系组织；工程论文仍可展示方法流程与资源取舍。

周报默认约 **1500–2500 字**，按实际内容调整，以连贯文字为主，并用简洁易懂的图表辅助关键判断。章节结构随文献研究、理论分析、调查、实验或其他实际工作调整，标题采用客观、范围明确的学术表述。正文体现认识如何推进、判断依据是什么；参考文献按需要保留。

个人讲解面向读者自己，展开研究目标、共性困难、主要研究方向、研究路径、论证依据与判断标准。关键问题先给可以口头表达的短答，再解释机制、证据和适用边界。

所有数学内容使用严格 LaTeX，阅读器采用随包提供的本地 KaTeX 渲染。导出包含公式的 Markdown 时会调用同版本 KaTeX 校验表达式。

## 安装与开始使用

这是提供给 AI 智能体宿主使用的 **skill 目录**。论文检索、内容生成和周报写作由宿主执行；本地脚本负责文献管理、页面、批注队列和导出。

1. 获取本分支源码：下载 [general-purpose 分支 ZIP](https://github.com/noblespear/academic-reading-master/archive/refs/heads/general-purpose.zip)，或运行：

   ```sh
   git clone --branch general-purpose --single-branch https://github.com/noblespear/academic-reading-master.git academic-reading-master
   ```

2. 将包含 `SKILL.md` 的完整目录命名为 `academic-reading-master`，放入宿主的 skills 目录，例如 `~/.agents/skills/`。保留 `assets/`、`scripts/`、`references/`、`schemas/`、`vendor/` 及许可文件；不复制 `.git/` 或本地测试产物。
3. 按宿主的技能加载方式重新加载，然后在会话中提出任务。已有文献库时指定其位置；首次使用可让宿主创建 `PaperVault`。

需要 `.skill` 或 `.zip` 安装包时，在本分支运行文末打包命令，得到 `academic-reading-master-v3.1.1-general-purpose.skill` 及同内容 `.zip`。它们均包含一个 `academic-reading-master/` 目录；宿主支持时直接导入，否则解压到 skills 目录。

[Releases](https://github.com/noblespear/academic-reading-master/releases) 中原有的 `v3.1.1` 文件对应工科特化版。通用版请使用本分支源码或本分支构建的安装包。

可以直接这样提问：

> 使用 academic-reading-master，围绕我的研究主题寻找适合入门的论文，说明每篇推荐理由和阅读难度。

> 阅读这个 PDF，生成概览和按原章节展开的详解，让我在网页里直接批注提问；全文翻译等我按按钮再生成。

> 根据这些论文的最终 HTML、批注和追问，写本周调研类周报，配上必要图表，并附一份给我自己看的详细讲解。

> 阅读这篇访谈研究或历史论文，解释材料如何支持作者的结论、有哪些其他解释，以及结论的适用范围。

> 根据这些研究记录写工作类周报，说明本周具体做了什么、依据支持什么判断，以及下一步安排。

写周报时会把 HTML 中的研究内容和批注互动作为素材，并核对实际互动与工作时间，区分本周进展和历史背景。无需另行整理材料包或逐项确认阅读。缺少关键事实时集中补问；AI 给出的解释会作为待核验理解材料，不能直接写成读者已经完成或验证的研究成果。

## 运行要求

| 场景 | 要求 |
|---|---|
| 服务、队列、普通 Markdown 导出 | Python 3.10 或更新版本；核心使用标准库 |
| PDF 文本、图像提取与核对 | Python 环境中有 PyMuPDF；可用 `python -m pip install -r requirements.txt` 补齐 |
| 数学表达式校验 | Node.js；优先复用宿主已有运行时，PATH 找不到时可设置 `ARM_NODE_PATH` 为可执行文件位置 |
| 自动批注答疑 | 当前提供 Codex app-server 适配器，需要本机可运行且已登录的 Codex CLI；认证、模型与推理设置继承宿主 |
| 前端开发测试 | 另外需要 Playwright 与 Chromium，日常使用无需安装 |

可用宿主的模型调用仍遵循其账号权限、额度和计费方式。skill 不附带模型、登录账号或 API 密钥。

阅读器已有内容时，可在 skill 目录运行以下命令；把占位路径与论文 ID 替换为自己的实际值。通常由宿主代为执行，读者无需手动操作。

```sh
python scripts/deploy_reader.py --vault <文献库路径> --paper <论文ID>
python scripts/open_reader.py --vault <文献库路径> <论文ID>
```

关闭网页后，已提交任务继续在后台处理。需要停下时使用页面的“停止后台服务”，或运行：

```sh
python scripts/open_reader.py --vault <文献库路径> --stop
```

桥接尚未连接时，问题保留并显示等待状态。`--bridge mock` 仅用于开发测试，回答带明确测试标记；`--bridge none` 可在暂不连接答疑宿主时保存问题。

## 数据与宿主适配

文献、生成的阅读 HTML、批注和周报保存在使用者自己的 `PaperVault`。新版批注与任务以 `.reader/state.sqlite3` 为权威存储，`annotations.json` 为可读导出快照。

本地服务只监听回环地址，浏览器与答疑桥接分别使用本机生成的凭据。提交问题后，选区、相关论文依据和必要问答上下文会发送给使用者选择的 AI 宿主处理；资料如何进一步处理取决于该宿主。分发包包含通用代码、模板和合成测试数据，不包含任何人的论文库、阅读记录或认证文件。

自动答疑核心与宿主分离，其他宿主可以遵循[桥接协议](references/bridge_protocol.md)接入。当前内置适配器为 Codex 和测试用 mock；其他宿主需要实现并登记适配器。批注、队列与阅读数据格式无需随模型更换。

已有阅读器迁移遵循[迁移说明](references/migration_guide.md)：保留用户认可的概览、详解及历史问答，仅迁移选定论文。无法唯一恢复的位置显示为需要重新定位。

## 内容脚手架

按论文的主要研究路径选择，混合研究可调整提示；详解始终沿原文章节展开。

| `--preset` | 适用内容 |
|---|---|
| `method` | 方法研究 |
| `theory` | 形式理论、定理与证明 |
| `survey` | 文献综述与研究综合 |
| `system` | 系统设计与实现 |
| `empirical` | 定量、质性或混合实证研究，包括观察、调查、访谈与田野 |
| `argument` | 概念论证、文本与史料解释 |

例如，由宿主在新论文目录创建待填脚手架：

```sh
python scripts/init_reading_content.py --paper-id my-paper --preset argument --output <论文目录>/paper-data.js
```

脚手架初始为未完成状态，需依据原文补写和审阅后部署。内部 `method_pipeline` 类型及原有 ID 保持兼容，名称不限定研究必须是算法。

## 开发与分发

目录中的 `references/` 按功能提供内容与数据规范；`scripts/` 提供命令行工具及测试；`assets/` 提供阅读器和本地数学渲染资源。

核心、搜集、周报与讲解测试使用合成材料，不调用真实模型：

```sh
python -m unittest discover -s scripts/tests -p test_reader_v3.py -v
python -m unittest discover -s scripts/tests -p test_collection_reports.py -v
python -m unittest discover -s scripts/tests -p test_report_explanation.py -v
```

前端测试可另行安装 `requirements-dev.txt` 和 Playwright Chromium，再运行 `scripts/tests/test_frontend_v3.py`、`scripts/tests/test_annotation_geometry.py`。批注面板滚动与后台更新回归使用 `scripts/tests/test_panel_scroll.py`，覆盖轮询、SSE 新回答及追问输入。真实宿主恢复测试需显式启用，详见桥接协议；其输出属于本地运行资料。

重新打包：

```sh
python tools/package_skill.py --output dist
```

命令生成 `.skill`、同内容 `.zip` 和 SHA-256 校验文件，只收录明确允许的源码与静态资源。论文库、运行状态、日志、缓存、截图、测试产物、Git 历史和凭据文件均不进入包。开源时仍应审查实际提交的文件，避免手动加入私人材料。

本项目代码与说明采用 [GNU AGPLv3（AGPL-3.0-only）许可证](LICENSE)，允许商业使用，须遵守相应的源码提供义务。随包提供的 KaTeX、字体和其他第三方资源保留其原许可证和归属，见[第三方资源说明](THIRD_PARTY_NOTICES.md)。论文及其图表属于各自权利人，分发 skill 不包含论文全文。
