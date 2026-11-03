# paper-data.js：schema 3，内容合同 3.2

文件仅包含window.PAPER_DATA = <标准JSON>;，不得函数、模板字符串或脚本。schema_version保持3。新稿content_contract_version为"3.2"；旧"3.1"与无标记数据按原规则校验，不通过删除版本字段绕过新稿要求。

## 三视图与稳定定位

顶层含id、content_version、title_en/title_cn、authors、year、venue、level。overview.sections为概览，explanation.sections为独立详解，顶层sections为全文双语翻译。section含稳定id、title_cn、page与blocks；block ID全篇唯一，更新时稳定。

完整论文概览保留“问题背景、方法框架、创新点、实验与效果、开销与论文边界”五个独立顶层section，按此顺序填写现有脚手架。最小教学/字段示例用于验证局部合同，不能照搬其章节数作为整篇论文架构；按概览写作要求填入这五节。

3.2完整论文登记overview_framework后，正式校验要求恰好五个概览section，且登记的方法框架位于第二节；隔离草稿允许未完成。旧3.1/无标记及未登记完整框架的局部字段示例不加此门槛。新建完整阅读器使用默认脚手架并保留方法总图登记；历史纯文字稿和局部字段示例继续兼容。

实际正文块及details/quote中的子块保留type和实际内容，ID在全篇统一唯一，每个正文块填写有效evidence_kind。来源粒度见[主规则](../SKILL.md#工作范围主规则)：section.source_refs登记章级范围，普通段落可省略page/source_refs；图、式、关键实验可用块级定位补充。组件fallback和独立富文档使用各自局部ID范围。新正文填evidence_kind：paper、general_background、ai_explanation或teaching_example；翻译用translation。实际原式、实验数据与教学设定区分。AI 可自主新增整理表、解释性公式、推导和图示；evidence_kind 按依据而非制作者填写：原文事实及基于原文的整理用 paper 并保留来源，补充解释/推导用 ai_explanation，通用背景用 general_background，自拟数值或模拟用 teaching_example。不要因为由 AI 排版就将真实论文数据改标为教学设定，也不把新增设定当作原文实测。

| 类型 | 主要内容 |
|---|---|
| paragraph/para | text_zh；翻译同时有完整text_en |
| formula | 完整原latex，不加外层分隔符；正文或相邻段落解释对象、运算和条件 |
| figure/diagram | 本地src、图注、alt与来源；翻译保留原图和双语图注 |
| table | headers/rows或对应中英行列；数值、单位和脚注保留 |
| code/algorithm | 原步骤、代码或伪代码及解释，不执行 |
| details/quote | summary或引用说明与嵌套blocks；长支线可就地折叠 |
| learning_aid | 成熟组件aid或目录aid_id，恰好一个；新正文placement:"body" |

## 3.2 的正文与完成状态

新3.2明确拒绝block.learning（包括实际正文的嵌套块），不要求plain_summary、concept_ids、隐藏formula_explanations或第二教学层。必要公式讲解、例子、比喻及试算直接在可见正文；机器不通过隐藏字段认证讲解充分性。

learning_catalog可省略；使用目录aid_id时保留aids及真实引用，使用inline aid可不建空目录。关键公式可用key_formula或learning_requirements.required_formula_ids定位，3.2检查原式与引用结构，实际解释直接写入正文，不要求重复讲解副本。

保留content_status:"incomplete"|"ready"。生成时incomplete，概览、详解、方法总图及适用图表公式填完后设ready即可部署；ready表示内容完成，不表示独立审阅。content_review可省略；已有记录保留，用户要求专项审阅时可如实记录，但不要求六项门槛全通过或审核署名，不自动填写通过。

新稿translation:{status:"deferred"}要求顶层sections:[]，是正常成品；translation:{status:"ready"}要求非空且忠实的双语sections。未填translation标记的旧稿保留原完成要求。补译不改overview/explanation及其ID。

隔离preview仅在需要查看未完成稿时使用，非日常必经步骤；仍检查结构、引用、资源和安全，不修改正式数据或用户历史。


## 方法框架与共享组件

新建完整阅读器默认包含中文静态方法总图；脚手架自动在learning_requirements.overview_framework登记kind:"static_figure"、section_id与block_id，指向概览第二节顶层figure，默认src:"assets/method-framework.svg"。figure仍需中文alt/caption、准确证据性质及来源。图文件存在、全部功能链可见，不另填交互节点。普通放大图片不算交互方法组件。

历史kind:"interactive_pipeline"与method_pipeline version1合同保留；仅维护旧稿或用户明确要求交互时查[组件合同](learning_support_guide.md)。默认脚手架不再生成交互配置；显式--with-method-pipeline为旧用法兼容。

图上用中文短标签、对象图元与清楚的分区和连线表达完整关系；理论与综述用真实论证/组织关系，不虚构算法。新稿不得省略总图登记冒充完成。

富文档、正文组件fallback及历史note_content/answer_content继续使用{version:1,blocks:[...]}。支持安全段落、标题、列表、图、公式、表格、代码、算法、quote/details和成熟learning_aid。新批注模型仅返回answer与sources，历史富文档仍读取；不再要求模型生成嵌套JSON字符串。

## 来源、资源与数学

source_refs是数组，可挂在section或关键证据块：本论文用物理page（从1开始）、真实section_id/block_id与label；章级多页范围用相应页的条目及label说明。外部材料用实际读取的label/url。网页无页码不填page，URL与本篇定位不混写。引用块需真实存在，不依赖尚未生成的译文ID，自拟例子不伪造原页。

src只用论文目录内登记相对路径，不接受越界、盘符、data或任意远程URL。安全静态SVG不含脚本、事件或外链。缺资源和未登记组件明确报错，不丢字段冒充修复。

formula.latex存裸LaTeX；段落内用反斜杠括号，独立式用反斜杠方括号，JSON反斜杠正确转义。原PDF数学逐式核对，浏览器严格渲染；不得用图片或Unicode伪式代替原式，见[数学规则](math_rules.md)。

## 历史兼容

旧3.1保持原learning、formula_explanations、catalog和六门槛规则，旧补充、开关与锚点继续呈现。无标记旧稿不强迫补新审阅字段。历史default_annotations继续不展示、不加入答疑，用户主动批注保留。

已有list.items、白名单table_html、algorithm字段、旧diagram/figure别名、pipeline_fields和自测字段按原约定读取；不因升级改旧block ID或数学原始措辞。界面升级保留认可内容，实质改写提高content_version并核对锚点。

API沿用content_contract.validate_learning_content与validate_rich_document；完整图文内容直接生成并部署，见[短指南](reader_workflow.md)。
