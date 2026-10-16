# Academic Reading Master 3.1.0

面向科研新手的论文阅读skill：搜集核验论文，生成概览、原章详解，并通过固定阅读器批注提问与持续追问。全文双语翻译按需生成。

## 阅读方式

正文是唯一教学主线，展开隐藏前提、概念、公式和证据，随文使用例子、比喻与试算。概览五节接成问题缘由、方法流转、创新突破、实验证据及使用代价与边界的连续讲述。先按图已在位完成连续正文，再插入图片；额外读图提示以引用式字体放在图下。专业英文术语首次出现即对应中文译名。概览与详解省去逐段来源入口，保留顶部原文PDF。较长支线就地折叠，新稿不再生成独立AI说人话或全局开关。旧阅读器的补充和历史问答仍兼容显示。

默认成品为“正文讲清楚、简洁方法总图、相关原图表、必要局部图”。总图按功能模块组织，内部计算默认隐藏；点击节点或连线后在图下显示职责、输入输出、正文入口或传递与依赖，说明自然展开。图按页面宽度排布，窄屏纵向展示。其他交互只响应明确需求，仅文字翻页的steps默认不生成。公式采用无底色、无框的紧凑论文排版。单篇任务只填内容，不开发共享组件。

规划、编写、翻译、图形、独立审阅和批注均沿用宿主默认模型与推理设置，不按职责分配模型或档位；用户明确指定时覆盖。批注先解决当前疑问，输出文字、LaTeX、小表格或代码，并保留来源与连续追问。原图仍能理解，批注不自动生成新的图或学习组件。

日常创作只加载[短指南](references/reader_workflow.md)，正文质量、原图解释、装配与复验统一在一处；组件字段、复杂证据和异常处理遇到时查。首次验收后只复查改动及其依赖。问答默认使用选区、必要历史及概览与详解，不附全文翻译或原文全文；相关原文与图像按需补充。同一论文的新批注和追问复用持续答疑会话；正文初始化发送一次，后续只补问题、必要选区和变化块，正常提问不重复恢复会话或加载整套规则。

新稿保留“翻译”视图，打开页面仅查看状态；用户明确点击“生成完整双语翻译”才启动模型。生成期间可继续阅读正文，完成后载入对照内容，原有块ID与批注保留。失败或中断不会在重启后自动重放；执行结果未知时先核对已有会话。已有全文翻译继续直接阅读。

## 功能

| 功能 | 产物与用法 |
|---|---|
| 搜集核验 | 主题检索或PDF、DOI、标题导入，记录全文状态、来源和筛选依据 |
| 三视图阅读 | 概览、详解、按需全文翻译，保留原PDF与各自阅读位置 |
| 正文图文 | 可点击方法框架、局部机制、数学说明与结果比较 |
| 批注答疑 | 划词或整块提问，自动排队、通知、追问、历史与连接恢复 |
| 研究周报 | 根据实际论文阅读、互动或实验开发记录生成Markdown周报与配套讲解 |
| 跨论文比较 | 基于核验资料增量整理主题矩阵 |

周报默认1500–2500字，按实际材料调整；论点与真实证据相连，AI解释不能冒充读者已验证的成果。数学使用本地KaTeX严格渲染，导出Markdown保留可编辑LaTeX。

## 安装

下载academic-reading-master-v3.1.0.skill或同内容.zip。它们是包含academic-reading-master目录的ZIP容器。宿主支持时按其方式导入；否则解压完整目录到skills位置，例如~/.agents/skills/。源码安装也须保留assets、scripts、schemas、vendor和许可，不复制测试输出或论文库。

升级前备份现有安装目录；替换后重新加载skill。已有论文按迁移指南保留认可正文、块ID和历史，不自动批量改写。历史版本通过Git标签与Release保留，见下表。

## 使用

> 阅读这个PDF，生成概览和按原章节详解，让我在网页里直接提问；全文翻译等我按按钮再生成。

> 根据本周阅读和实验记录写研究周报，并提供详细讲解。

完整创作和局部修订统一按[日常创作短指南](references/reader_workflow.md)执行，不预读全套手册。新内容合同为3.2，数据schema仍为3；3.1和无标记旧稿继续兼容。

    python scripts/init_reading_content.py --paper-id <id> --preset method --output <论文目录>/paper-data.js
    python scripts/prepare_method_pipeline.py --input <骨架.json> --output <组件.json>
    python scripts/deploy_reader.py --vault <库> --paper <id> --preview --preview-dir <隔离目录>
    python scripts/deploy_reader.py --vault <库> --paper <id>
    python scripts/open_reader.py --vault <库> <id>

草稿预览只按返回HTML入口或静态服务器查看，不启动正式答疑，不写用户状态。正常部署仍要求真实内容审阅。关闭网页后已提交任务可在后台继续；停止：

    python scripts/open_reader.py --vault <库> --stop

默认不传入模型或档位覆盖；用户明确要求时可用启动参数设置，分别覆盖对应项。显式参数用于新后台服务；已有服务运行时需先停止再按参数启动，默认启动器不保存一次性覆盖。服务已暂停时普通恢复不解除暂停，unknown投递不自动重发。mock仅供开发测试，none可保存问题但不代表答疑已验。

## 运行要求

| 场景 | 依赖 |
|---|---|
| 服务、队列、Markdown导出 | Python 3.10+，核心标准库 |
| PDF读取核验 | PyMuPDF |
| 数学校验 | Node.js和随包本地KaTeX；可设置ARM_NODE_PATH |
| 自动批注 | 本机已登录的Codex CLI/app-server |
| 前端开发验证 | Playwright与Chromium，日常阅读无需额外安装 |

宿主提供模型、认证、权限与额度，skill不附带账号或密钥。文献、HTML、批注和周报保存于用户PaperVault；.reader/state.sqlite3是问答权威状态，annotations.json是快照。请求会将选区、相关论文证据和必要历史送到用户选择的宿主。

固定服务仅监听回环地址，认证留本机。分发包包含代码、模板、原始内置资源与合成测试数据，不包含私人论文库、凭据或聊天记录。宿主接口见[桥接协议](references/bridge_protocol.md)，旧数据升级见[迁移](references/migration_guide.md)。

## 版本与许可

| 版本 | 定位 | 获取 |
|---|---|---|
| 3.1.0 | 当前完整阅读器：正文优先、翻译按需、持续批注、必要可视化 | [v3.1.0](https://github.com/noblespear/academic-reading-master/releases/tag/v3.1.0) |
| 3.0.1 AGPL 重发行 | 保留3.0.1功能，仅调整发布许可与说明 | [v3.0.1-agpl](https://github.com/noblespear/academic-reading-master/releases/tag/v3.0.1-agpl) |
| 3.0.0 AGPL 重发行 | 首个统一三视图阅读器的历史版本 | [v3.0.0-agpl](https://github.com/noblespear/academic-reading-master/releases/tag/v3.0.0-agpl) |
| 最早三遍阅读工作流 | 从2026-10-01备份恢复的未编号旧快照，尚未形成完整统一阅读器 | [legacy-three-pass-20261001](https://github.com/noblespear/academic-reading-master/releases/tag/legacy-three-pass-20261001) |

项目代码 Copyright (c) 2026 Academic Reading Master contributors，采用 **AGPL-3.0-only**，完整条款见[LICENSE](LICENSE)。允许商用；分发受覆盖的修改或衍生版本，以及通过网络向用户提供修改后的服务时，须按许可提供相应源码和许可。原v3.0.0、v3.0.1标签和已授出的MIT许可作为历史记录保留，不作追溯撤回；所有新整理的发行版采用AGPL。

第三方资源仍保留各自版权与许可，见[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。Anti-Defensive Writing的MIT原件、许可及固定来源随包保留；可视化规范由本项目的独立适配指南与固定组件提供。没有公开再分发授权的Visualize完整上游快照不包含在公开仓库或安装包中。日常创作仍由短指南直接执行语言与可视化原则，完整专题按需加载。

