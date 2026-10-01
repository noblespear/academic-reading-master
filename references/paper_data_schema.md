# paper-data.js v3

文件仅 window.PAPER_DATA = <标准 JSON>; ，不得函数、模板字符串或脚本。顶层 schema_version:3、content_version、id、title_en、title_cn、authors、year、venue、level。

overview.sections 为概览，explanation.sections 为独立详解，顶层 sections 为全文翻译。section 含稳定 id、title_cn、可选 title_en、page、blocks。block ID 全篇唯一且更新时稳定；含 type、page、source_refs。

| type | 字段 |
|---|---|
| paragraph | text_zh；翻译必须同时有完整 text_en |
| figure | 相同 src 原图、caption_en/caption_zh；可配文字说明 |
| formula | 必填 latex 原公式及双语说明，不以图片代替公式 |
| table | 对应 rows_en/rows_zh；复杂原表可用 src 区域图与双语说明 |
| diagram | 本地 src 或可信静态 svg，无脚本事件外链 |

src 是论文目录内相对路径。source_refs 为数组，例如 {"page":3,"section_id":"src-section-3","block_id":"src-p003-b004","label":"Fig. 3"}；页码为从1开始的物理页，只填写确实存在的出处。

升级时，用户认可的旧概览与章节讲解直接成为 overview 与 explanation 主体，保留原 block ID、原句、顺序和类型，不称为忠实译文。新翻译使用独立 source ID。部署器核验 JSON、三视图、ID、资产和译文完整性。内容或字段布局调整提高 content_version，旧批注锚点需重新校验。

兼容已有内容：list.items 的 text_zh 原样保留；table.table_html 只通过严格白名单重建表格DOM，不执行HTML；algorithm 保留 algo_title/algo_lines；diagram.caption、figure.asset/fig_caption_zh 保留。block.annotations 是原有讲解，title/body 独立折叠，与用户问答区分；锚点字段为 annotations.N.body 或 body_latex。

概览原流水线可用 paragraph + title/step/pipeline_fields 表达；每项含 key、label、原 text 和可选 text_latex，选区字段为 pipeline_fields.N.text[_latex]。overview.pipeline_blocks 的 content_id/name/step 用于总图跳转。原自测用 paragraph + q/hint（及可选 q_latex/hint_latex），提示折叠，原始字段保持不变。text_zh_latex、items.N.text_zh_latex、body_latex、caption_latex 等仅规范数学显示，不改写原文。

## 数学语言

所有公式、集合、上下标、数学变量和关系均用严格 LaTeX。formula.latex 存不带外层分隔符的表达式；文字内统一用 \\( ... \\)，文字中的独立式用 \\[ ... \\]。JSON 反斜杠必须正确转义。正文普通数字及图表编号不必变成公式。

逐式核对原 PDF，补正老字体提取丢失的数学符号；不得把残缺数学文本或公式截图当正式表达式。部署检查结构，浏览器用本地数学渲染器实际验证并检查错误节点；语法有误时显式报错并修复，不把裸字符串显示当渲染成功。见 math_rules.md。
