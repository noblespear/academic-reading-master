# paper-data.js：schema 3，内容合同 3.2

文件仅包含window.PAPER_DATA = <标准JSON>;，不得函数、模板字符串或脚本。schema_version保持3。新稿content_contract_version为"3.2"；旧"3.1"与无标记数据按原规则校验，不通过删除版本字段绕过新稿要求。

## 三视图与稳定定位

顶层含id、content_version、title_en/title_cn、authors、year、venue、level。overview.sections为概览，explanation.sections为独立详解，顶层sections为全文双语翻译。section含稳定id、title_cn、page与blocks；block ID全篇唯一，更新时稳定。

通用版完整论文概览保留“问题背景、研究框架、主要贡献、论证与发现、适用范围与局限”五个独立顶层section，按此顺序填写现有脚手架。实验与资源分析按论文内容选用；原有section/block ID保持稳定，旧工科版标题仍可读取。最小教学/字段示例用于验证局部合同，不能照搬其章节数作为整篇论文架构；结构校验通过仍需审阅这五节是否齐备。

3.2完整论文登记overview_framework后，正式校验要求恰好五个概览section，且登记的研究框架位于第二节；隔离草稿允许未完成。旧3.1/无标记及未登记完整框架的局部字段示例不加此门槛。五节标题、内容职责和其余顺序仍按正文写作与独立审阅核对。

实际正文块及details/quote中的子块保留type和实际内容，ID在全篇统一唯一，每个正文块填写有效evidence_kind。来源粒度见[主规则](../SKILL.md#工作范围主规则)：section.source_refs登记章级范围，普通段落可省略page/source_refs；关键引文、图表、公式与研究依据可用块级定位补充。组件fallback和独立富文档使用各自局部ID范围。新正文填evidence_kind：paper、general_background、ai_explanation或teaching_example；翻译用translation。原文材料、作者论断、AI解释与教学设定区分。

| 类型 | 主要内容 |
|---|---|
| paragraph/para | text_zh；翻译同时有完整text_en |
| formula | 完整原latex，不加外层分隔符；正文或相邻段落解释对象、运算和条件 |
| figure/diagram | 本地src、图注、alt与来源；翻译保留原图和双语图注 |
| table | headers/rows或对应中英行列；数值、单位和脚注保留 |
| code/algorithm | 原步骤、代码或伪代码及解释，不执行 |
| details/quote | summary或引用说明与嵌套blocks；长支线可就地折叠 |
| learning_aid | 成熟组件aid或目录aid_id，恰好一个；新正文placement:"body" |

## 3.2 的正文与审阅

新3.2明确拒绝block.learning（包括实际正文的嵌套块），不要求plain_summary、concept_ids、隐藏formula_explanations或第二教学层。必要公式讲解、例子、比喻及试算直接在可见正文；机器不通过隐藏字段认证讲解充分性。

learning_catalog可省略；使用目录aid_id时保留aids及真实引用，使用inline aid可不建空目录。关键公式可用key_formula或learning_requirements.required_formula_ids定位，3.2检查原式与引用结构，实际解释由独立审阅核实，不要求重复讲解副本。

保留content_status:"incomplete"|"ready"、content_review:{reviewer,factual_gates,note?}。六门槛为source_accuracy、evidence_separation、claim_scope、prerequisites、formula_correctness、analogy_and_example。生成时incomplete；实际内容审阅后如实署名并单独更新ready。note记录实际范围、主要修正和待验事项，不硬编码到生成脚本。

content_status:"ready"表示已审阅的正文可部署。新稿translation:{status:"deferred"}要求顶层sections:[]，是正常成品；translation:{status:"ready"}要求非空且忠实的双语sections，空译文不能通过。未填translation标记的旧稿保留原完成要求。补译不改overview/explanation及其ID，翻译生成时机遵循[主规则](../SKILL.md#工作范围主规则)。

隔离preview可显示未完成草稿，尚未编写的视图使用空sections数组；已有内容仍检查结构、引用、资源和安全，只跳过内容完成与未发生的审阅门槛，不修改正式数据或用户历史。preview也不能把空译文标成translation.status:"ready"。

## 研究框架与共享组件

新稿learning_requirements.overview_framework登记kind:"interactive_pipeline"、section_id与block_id，指向概览研究框架顶层placement:"body"的method_pipeline。完整研究路径或论证、组织关系可见，细节留正文；沿用这些内部字段与类型以保持数据兼容。

method_pipeline现有version1合同保留：config含summary、nodes、edges和可选reading_order。节点id/label/summary/inputs/outputs/column/row，边id/from/to/kind/label/flow/cause。role/why_needed/handoff可选。骨架用prepare_method_pipeline自动补grid，模型不手写坐标；旧稿完整显式位置保留。

图上短标签，点击节点或边更新固定说明区域。静态回退可读，不能代替新概览登记的交互。类型详细字段见[组件合同](learning_support_guide.md)。节点可表示概念、论据或研究环节，inputs/outputs可表述前提与形成的认识，flow/cause说明实际关联及理由。理论、解释性研究与综述用真实论证或组织关系，不虚构算法、时间顺序或因果。

富文档、正文组件fallback及历史note_content/answer_content继续使用{version:1,blocks:[...]}。支持安全段落、标题、列表、图、公式、表格、代码、算法、quote/details和成熟learning_aid。新批注模型仅返回answer与sources，历史富文档仍读取；不再要求模型生成嵌套JSON字符串。

## 来源、资源与数学

source_refs是数组，可挂在section或关键证据块：本论文用物理page（从1开始）、真实section_id/block_id与label；章级多页范围用相应页的条目及label说明。外部材料用实际读取的label/url。网页无页码不填page，URL与本篇定位不混写。引用块需真实存在，不依赖尚未生成的译文ID，自拟例子不伪造原页。

src只用论文目录内登记相对路径，不接受越界、盘符、data或任意远程URL。安全静态SVG不含脚本、事件或外链。缺资源和未登记组件明确报错，不丢字段冒充修复。

formula.latex存裸LaTeX；段落内用反斜杠括号，独立式用反斜杠方括号，JSON反斜杠正确转义。原PDF数学逐式核对，浏览器严格渲染；不得用图片或Unicode伪式代替原式，见[数学规则](math_rules.md)。

## 历史兼容

旧3.1保持原learning、formula_explanations、catalog和六门槛规则，旧补充、开关与锚点继续呈现。无标记旧稿不强迫补新审阅字段。历史default_annotations继续不展示、不加入答疑，用户主动批注保留。

已有list.items、白名单table_html、algorithm字段、旧diagram/figure别名、pipeline_fields和自测字段按原约定读取；不因升级改旧block ID或数学原始措辞。界面升级保留认可内容，实质改写提高content_version并核对锚点。

API沿用content_contract.validate_learning_content与validate_rich_document；结构检查不替代科学/教学审阅，实际流程见[五步流程](reader_workflow.md)。
