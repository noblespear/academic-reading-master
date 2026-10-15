---
name: academic-reading-master
description: 搜集核验论文，生成概览、原章详解和按需全文双语翻译的阅读器。正文展开隐藏前提、概念、数学与证据，配简洁方法总图、相关原图表和必要局部图；支持快速批注答疑、续读、跨论文比较与研究周报。
---

# Academic Reading Master

帮助科研新手跟上论文的推理，并对照原文。固定阅读器负责呈现，模型负责准确、连贯的正文与图文解释。

## 任务入口

日常创作只读一份短指南；链接不是预读清单。同一上下文已读取且未变的规则和材料不重复加载。

| 当前任务 | 只加载对应入口 |
|---|---|
| 新建阅读器、继续精读或局部修订 | [日常创作短指南](references/reader_workflow.md) |
| 搜集、导入或核验论文 | [搜集](references/collection_guide.md) |
| 批注与追问 | [答疑](references/answer_worker_guide.md)；仅遇运行问题查[批注](references/annotation_guide.md)，通信异常再查[桥接](references/bridge_protocol.md) |
| 专门调整方法图或制作其他演示 | [图形专题](references/visual_decision_guide.md)，只查正在使用的组件字段 |
| 旧稿升级与锚点迁移 | [迁移](references/migration_guide.md) |
| 研究周报 | [周报](references/weekly_report_guide.md)，需要时查[素材](references/report_reading_inputs.md)和[讲解](references/report_explanation_guide.md) |
| 跨论文比较 | [主题矩阵](references/theme_matrix_guide.md) |

## 工作范围（主规则）

- 各阶段沿用宿主默认模型与推理设置，用户明确指定时覆盖对应项，不修改全局配置。
- 全文翻译仅在用户进入翻译页并明确点击生成后启动。批注用选区、必要历史及概览/详解，依据不足再补相关原文，不默认带翻译或原文全文；解释原图、公式，不自动创作新图或交互。
- 文献库使用用户指定位置或当前有效 PaperVault/library.json。保留原PDF、资源、稳定ID、批注、草稿和暂停状态；unknown投递不自动重发。`.reader/state.sqlite3`是问答权威状态，`annotations.json`是快照，凭据留宿主。

创作质量、图文放置、复查范围和交付步骤统一以短指南为准。其他文档只提供专题细节，不建立第二套日常流程。
