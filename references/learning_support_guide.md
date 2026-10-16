# 正文组件与历史补充合同

仅在填写某个现有组件或兼容旧富内容时查阅，按type搜索相关行；日常创作以[短指南](reader_workflow.md)为准，不通读本目录。

新3.2内容把概念、原式解释、比喻和试算直接写进正文，长支线用普通details。不生成block.learning或隐藏公式讲解副本，不要求未使用的概念目录。稳定ID、来源、数学与证据性质仍核对，见[数据合同](paper_data_schema.md)。

## 在正文使用成熟组件

研究框架使用method_pipeline，placement:"body"，登记overview_framework。按概念、论证或功能模块切分，通常先尝试4–7主要模块，适用时另列输入输出；细节留正文局部图。可表达理论论证、材料解释、实证设计和综述框架，不要求算法流程。骨架可由prepare_method_pipeline自动补column/row，完整显式grid保持兼容。

除研究框架图外，其他组件只在用户明确需要演示时采用，使用给定受控类型与字段。默认以原图表和必要静态局部图表达关系。真实method_pipeline和comparison_chart可用paper并附来源，其余教学演示用teaching_example。原结果单位与比较条件保持原样；不把公式或假设演示当性能预测。未支持的共享能力另作代码任务，单篇不扩类型/运行时。

inline aid与aid_id恰好一个；inline无需维护空catalog。组件fallback使用已有富文档或可读文字，静态SVG资源必须安全本地。默认状态与关键变化可复算，输入边界明确。组件类型完整说明只在配置实际类型时查阅，不进入轻量批注会话。

## 旧3.1与历史富内容

旧learning及narrative blocks、概念目录和完整formula_explanations仍按原合同读取，原锚点和用户问题保留。旧数据中关键公式的隐藏解释门槛继续兼容；新3.2由实际可见正文审阅，不要求另一份purpose/symbols/operations/example等副本。

旧note_content、answer_content、附件及study_state继续保存显示；新模型答疑仅answer/sources，不生成组件配置或嵌套JSON字符串。富文档{version:1,blocks:[...]}仍由共享安全渲染器处理。

## 固定组件 v1

需要新绘静态图时才查[制图适配](visual_authoring_guide.md)；填写现有组件无需先加载其他图形手册。下表按type查当前字段，完整上游原版只供专门设计或维护时查阅。

先确定要解释的关系，再按[覆盖规则](visual_decision_guide.md)选择原图表或必要局部图。概览的 `method_pipeline` 点选节点显示概要、所依据内容和形成的结果，点选边显示关联内容及理由，同时突出相应关系。用户要求的其他交互才检查选择、变化或对照是否帮助理解。实际结果图标来源、单位与比较口径；假设性演示标教学示例，不把边数或矩阵秩当作 token 或准确率。桌面和窄屏核对图中文字与真实关系，实际操作本次使用的组件。

所有组件是登记类型与 JSON 配置，不能携带 JS、HTML 或 eval。信封为 {id,type,version:1,title?,evidence_kind:"teaching_example",config,fallback:{version:1,blocks:[...]}}；兼容 fallback_text，但新内容优先保留完整图文回退。教学组件输出不能自动进入论文发现或研究结论。

真实数据的 comparison_chart，以及基于原文整理研究框架、概念或论证关系的 method_pipeline，可使用 evidence_kind:"paper" 并附 source_refs；不挂教学示例标签。假设性组件使用 teaching_example。可参考[正文与补充的图文数据样板](../assets/content-templates/narrative-example.json)的字段和放置方式；讲述依当前写作指南，不照抄样板的场景或句式。

验证器允许 comparison_chart 与 method_pipeline 承载 `paper` 证据，其余现有类型按教学演示校验。不同指标不必强塞百分制比较组件；可以用标准绘图工具准确画出原单位的数据图。单篇只配置已支持的类型；现有组件不适合时用准确的静态或分步表达，并把值得新增的共享能力另列代码任务。不得改挂teaching_example伪造论文依据。

| type | config | 选择依据 |
|---|---|---|
| method_pipeline | {summary,nodes,edges,reading_order?}；node必需id/label/summary/inputs/outputs/column/row，edge必需id/from/to/kind/label/flow/cause | 可点击研究框架；全图保留，点节点显示概要、所依据内容及形成的结果，点边显示关系内容与成立理由 |
| concept_cards | {concept_ids:[概念ID]} | 词语与背景卡 |
| steps | {steps:[{title,text?,blocks?}],initial_step?:0} | 保留按需与旧稿兼容；仅文字翻页默认不生成 |
| formula_explorer | {parameters,expression,result_label,unit?,latex?,symbols?} | 交互观察关键公式；[选用门槛见短指南](reader_workflow.md#原图与必要可视化) |
| parameter_explorer | 同上 | 与formula_explorer同一实现，遵循同样选用门槛 |
| prediction | {question,choices:[{id,text,feedback}],correct_choice_id,explanation?} | 先预测再看解释，每个选项都有原因 |
| lru_cache | {sequence:[项名],capacity,min_capacity,max_capacity} | 本版完整缓存教学演示 |
| graph_walkthrough | {nodes,edges,stages,initial_stage?}；nodes有id/label/round?，edges有id/from/to/kind?；stage有id/title/text/active_node_ids/active_edge_ids及可选高亮与注释 | 同一图上看对象与连接实际如何变化；不能只换文字 |
| comparison_chart | {metric_label,unit:"%",models,tasks,series,observations,default_model_id?,default_task_id?,baseline_series_id?} | 同模型/同任务比较实际百分制结果；tasks必须含description和denominator，observation含model_id/task_id/series_id/value |
| weighted_degree | {nodes:[{id,label}],edges:[{id,from,to,weight}],adjustable_edge_id,tie_break:"node_order",selected_node_id?,initial_mode?:"all"或"prune_one"} | 在一幅小图里看边权如何参与节点分数、改变排序与退出者；选择节点看入/出项展开，改变一条权重后图与选择同步更新 |

两种 explorer 共用受控表达式求值器：第一个实际参与表达式的参数作为横轴，在配置的 min/max 内绘制结果曲线，其余参数固定在当前值；横轴参数移动当前点，其他参数改变曲线。将最值得观察的自变量放在前面，并为参数、result_label 和 unit 写清量与单位。曲线是公式计算而非测量数据；正文解释观察什么、变化为何发生。常数表达式不伪造曲线，未定义区域断开并提供静态回退。新稿在正文使用；旧补充和历史富答案继续兼容呈现。

method_pipeline 自3.7支持。配置的 `summary` 描述总图主线；每个节点的 `summary`、`inputs`、`outputs` 为必需非空字符串，分别写出准确概要、所依据内容和形成的结果。工程流程填写具体输入输出；理论或解释性节点可填写前提、概念、材料及其论断、解释或组织作用，没有独立产物时如实说明该节点承担的作用，不虚构数据。`role`、`why_needed`、`handoff` 可选补充职责、设计依据和衔接，不能代替必需三项。字段名称与类型不变。图上只显示短标签，选中后展开解释。`column`/`row` 是布局网格位置，不是方法执行顺序。

每条边的 `flow` 与 `cause` 为必需非空字符串，前者解释连接的内容或关系，后者解释关系成立的依据。`kind` 为 `flow`、`branch`、`feedback` 或 `dependency`，按实际含义选用；概念、组织或比较关系可用 `dependency`，并在 `label`、`flow`、`cause` 写明具体关系，不冒称执行依赖。字段名称与枚举不变，不为填字段虚构传递过程。依赖、反馈和分支按各自性质解释，不把数据流或论证依赖写成未经证实的因果效应。点边高亮该边与两端，并显示关系内容与理由；节点和边互斥选择，提供图上点选和键盘/触屏入口，刷新保存当前选择，复位回到全貌。静态回退应保留全部节点概要与所依据内容、形成的结果，以及全部边的关系与理由。

不得把分支/反馈强排成一条直线。可选 `reading_order` 只定义节点的阅读顺序，不改变图中的执行/依赖关系。新稿 overview_framework 的 kind 为 interactive_pipeline，指向顶层 placement:body 的该组件。新稿在正文使用；旧补充与历史批注按原容器呈现，不设置body越过所属容器。完整例见[方法流水线数据样板](../assets/content-templates/method-pipeline-example.json)，其中是合成演示，不是论文证据。

weighted_degree 自阅读器3.3支持，仅用于教学。固定4节点，label最多12字符；1–6条边，无自环和重复节点对（含反向），权重0–1且一位小数；只允许上表字段。按删前全图的入边、出边权重之和排名，移除最低的一个节点及其关联边，不在删后重新计分以偷换筛选依据。并列按nodes顺序选最前者，必须在界面说明这是教学约定，不能称为论文规定。用户主动提问仍可带该组件当前状态。选用该演示时，前后文字要说明计算值如何进入方法；不能把度分数等同真实贡献、准确率或token。

parameter 为 {id,label,min,max,step,value,unit?}，1–8 个，范围有限、step>0、默认值在范围中。expression 只允许有限数、{parameter:ID} 或 {op,args:[表达式...]}。op 为 add、subtract、multiply、divide、power、sum、mean；subtract/divide/power 必须两个操作数，其他至少一个。最多深度12、节点128、单层32项。结果有限且实数，零分母等无定义输入由组件报告；默认输入无定义则拒绝部署。指数绝对值最多1024，防止无界计算。

兼容比率别名 {model:"ratio",numerator:参数ID或数字,denominator:参数ID或数字,result_label,parameters}。新写作使用 AST。兼容旧实验候选类型 concept_card、step_walkthrough、cache_walkthrough，主类型依上表；cache_walkthrough.requests 为 sequence 的别名。未知组件使用回退呈现，部署新合同仍拒绝未登记类型，避免承诺不存在的交互。

steps 最多200步，预测2–20项，缓存序列1–200项且容量1–64。每次组件实例的状态隔离并可重置；旧稿关闭补充只隐藏，不删除输入。主动问某一步时将 study_state 快照传给答疑，而不是仅传“为什么这里”。

完整范例见 [缓存样板](../assets/content-templates/complete-cache-example.json)。它包含七次读取、常见词、完整比例公式和五种首批组件。每个教学数字可复算：容量1、2、3、4分别命中0、1、2、3次；容量3在第5步移出B。现实提速不由这些计数推出。
