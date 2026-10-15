# paper-data.js 数据结构规范 v2.2（Pass 2 唯一产出的契约文件）

阅读器三件套（reader.html / reader.js / styles.css）是**固定模板**，由 deploy_reader.py 复制到论文目录，永不重写。AI 每篇论文只生成一个 `paper-data.js` 数据文件，reader.js 读取并动态渲染全部内容。本文件规定它的结构、ID 规则与转义规则。

> v2.2 变更：顶层新增 `level`（挡位）与 `pass3`（第三遍产出：边界 + Gap，文末"深度剖析"区渲染）；section 新增可选 `page`（原文 PDF 页码，侧栏联动跳页）；`text_en` 降为可选、**不再全文转录原文**（原文由阅读器 PDF 侧栏呈现，生成成本接近减半）。
> v2.1 变更：新增 `diagram` 示意图板块（§5）；全部解释性文字要求**通俗优先**（先大白话后术语，见 pass2_workflow_guide.md 铁律 3）。
> v2 变更：pipeline 字段改为概述级（goal/input/tech/why/gain/benefit_next，废除 how/pitfalls）；顶层新增 `novelty` 与 `self_check`；明确 Pass 2 深度约束（技术黑盒原则）。

## 1. 文件格式

```js
window.PAPER_DATA = { ... };
```

- 整个文件只有这一条语句；
- `{ ... }` 内部必须是**严格合法 JSON**：双引号键值、无尾逗号、无注释、无单引号字符串——deploy_reader.py 会剥离前缀做 JSON 校验，校验不过部署会失败；
- 用 Write 创建骨架 + Edit 按 section 追加时，每次追加后都要保证文件整体仍是合法 JSON（追加发生在数组最后一个元素之后）。

## 2. 顶层结构

```json
{
  "id": "2026_anigen_unified_s3_fields",
  "title_en": "AniGen: Unified S³ Fields for Animatable 3D Asset Generation",
  "title_cn": "AniGen：基于统一 S³ 场的单图可驱动 3D 资产生成",
  "authors": "Yi-Hua Huang, Zi-Xin Zou, ...",
  "affiliations": "香港大学, VAST, ...",
  "venue": "arXiv:2604.08746",
  "year": 2026,
  "level": "novice",
  "pass3": {
    "boundary": { "assumptions": ["…"], "failure_cases": ["…"], "cost": ["…"] },
    "gaps": [{ "type": "渐进改进", "idea": "…", "linked_limitation": "…", "difficulty": "低", "validation": "…" }]
  },
  "novelty": "把形状、骨骼、蒙皮统一为连续 S³ 场端到端联合生成，代替'先出网格再绑骨'的分步流水线，从源头消除四肢粘连",
  "self_check": [
    { "q": "这篇论文解决什么问题？输入和输出分别是什么？", "hint": "看 pipeline 第 1 步的 input 与最后一步的产出" },
    { "q": "整条流水线分几步？每步用什么技术、达到什么效果？", "hint": "对着流水线卡片逐节点复述 tech 字段" },
    { "q": "核心创新点是什么？它替代了什么旧做法？", "hint": "看顶部'核心创新'标注" },
    { "q": "效果证据是哪张表/图？关键数字是什么？", "hint": "看实验章节表格的解读批注" }
  ],
  "pipeline": [],
  "sections": []
}
```

- `novelty`：一句话说清创新点，直白表达"比之前的方法多做了什么、换来什么好处"（强烈建议，deploy 缺失给 warning）；
- `self_check`：3~5 道复述自测题，每题带 `hint`（指向对应节点/字段，不直接给答案）——这是"大致理解"的验收工具，强烈建议；
- `level`：**可选**，生成时的挡位（`novice` 新手档 / `expert` 熟练档），阅读器顶栏显示徽标；非法值 deploy 报错；
- `pass3`：**可选**，第三遍产出，由 Pass 3 阶段用 Edit 追加（Pass 2 生成时**省略**）。`boundary`（隐式假设/失效场景/复杂度成本，字符串数组）+ `gaps`（type 渐进改进|跨域结合|痛点攻坚 / idea / linked_limitation / difficulty 低|中|高 / validation）。阅读器在文末渲染"深度剖析"区；notes.md 仍是导出权威。

## 3. pipeline：方法主线节点

把论文方法提炼为 **3~8 个节点**的链（输入 → … → 输出）。**Pass 2 的深度标准 = 概述级**：只需读懂"第一步用名为 XX 的技术把 A 变成 B，第二步用名为 XX 的技术把 B 变成 C"；内部实现细节（超参、网络结构等）属于 Pass 3。

```json
{
  "step": 1,
  "name": "节点短名（如：图像特征编码）",
  "goal": "这个节点要解决什么问题",
  "input": "这一步的输入是什么",
  "tech": "用了什么技术：技术名 + 一句话它是什么 + 输入→效果。当黑盒讲，不展开内部机制",
  "why": "为什么用这个技术（可选：作者明确对比过替代方案才写，否则省略字段）",
  "gain": "效果如何 + 图表证据（如 'Table 1：骨架/蒙皮八项指标全面最优（GW 0.286），端到端 19 秒'）",
  "benefit_next": "给后续节点提供了什么"
}
```

- **技术黑盒原则**：`tech` 只写「名称是什么（一句话）+ 输入→效果（从 A 变成 B 这种级别）」。禁止展开网络结构、公式推导、超参数——用户标记不懂时，那是 Pass 3 定点打开黑盒的工作；
- **通俗原则**：goal/tech/gain 面向第一次接触该领域的人写——先大白话后术语，数据用比较级转译；
- `gain` 没有量化证据时写定性证据出处；论文没说的项写"论文未说明"；
- 节点划分依据论文自身的框架图/方法章节，**不得套用其他论文的模板**（通用性红线）。

## 4. sections：按原文结构逐段还原的双语内容

```json
{
  "id": "sec-3",
  "number": "3",
  "title_en": "Method",
  "title_cn": "方法",
  "page": 5,
  "blocks": []
}
```

- sections 按**论文原章节顺序**完整覆盖：摘要 → 引言 → … → 结论。完整性红线：禁止只挑"重要章节"、禁止把多段合并成一段概述；
- `number` 用论文原始编号（摘要可用 "0" 或 "A"），`id` 为 `sec-<number>`（去掉子级点号，如 `sec-3-2`）；
- `page`：**可选数字**，该节在原文 PDF 中的起始页码——阅读器"原文 PDF 侧栏"据此联动跳页（点击目录/章节标题即跳）。从 PDF 顺手标注，成本近乎为零，**强烈建议**。

## 5. blocks：内容块（8 种类型）

每个 block 有全局唯一 `id`（规则见 §6）。公共字段：`id`、`type`、`annotations`（0~N 条，见 §7）。

| type | 专有字段 | 说明 |
|---|---|---|
| `para` | `text_zh`,（可选 `text_en`） | 段落。原文一段 = 一个 block；长段可按句群拆成相邻 block，但顺序与原文一致。**v2.2：`text_en` 不再全文收录**（原文由 PDF 侧栏呈现），仅关键句可少量保留 |
| `formula` | `latex`, `text_zh`,（可选 `eq_ref`） | 独立公式。`latex` 是**不带 `$$` 包裹**的 LaTeX 源码；**Pass 2 的 `text_zh` 只写"这个式子在算什么、影响什么（效果层）"**，逐符号拆解是 Pass 3 答疑时才做的；`eq_ref` 如 "Eq. (3)" |
| `figure` | `asset`, `fig_caption_en`, `fig_caption_zh` | 插图。`asset` 为相对论文目录的路径（`assets/figure_p3_1.jpeg`）；未提取到图时省略 `asset`，仅用文字描述图内容与阅读顺序 |
| `table` | `table_html`, `text_zh` | 数据表。`table_html` 为完整 `<table>` HTML（最优值加 `<b>`，次优 `<u>`）；`text_zh` 只解读"这张表证明了什么主张、关键数字是哪几个" |
| `list` | `items`: `[{text_zh}, ...]`,（可选 `text_en`） | 原文的条目列表（贡献点、假设条件等）；`text_en` 同 para 规则可选 |
| `algorithm` | `algo_title`, `algo_lines`: `["...", ...]` | 伪代码（Pass 2 仅当论文的算法本身是流程主干时才收录，如装配/提取算法；每行一条，`▸` 表示缩进） |
| `diagram` | `svg`,（可选 `caption`） | **AI 手绘示意图**（v2.1）。`svg` 为内联 SVG 字符串（必须以 `<svg` 开头；会被消毒：去脚本/事件/外链）。用于方法结构、数据流向、方法对比等"关系与流向"类信息。风格：`viewBox="0 0 860 H"` 等比自适应、白底学术风（线条 `#4a4f57`、强调 `#2b5cb8`、方框 `rx="8"`、中文 13~15px、高度内容 ≤360px）、无脚本无交互；图内文字也用大白话；放在所属小节 blocks 最前，ID 用追加型新编号 |

**Pass 2 深度约束总则**：`formula`/`table`/`algorithm`/`figure` 的解读文字一律停在效果层（在算什么/证明什么/怎么走），训练细节、损失逐项、超参不属于 Pass 2 产出。所有解释性文字通俗优先（先大白话后术语）。

## 6. block ID 稳定规则（批注不丢的关键，必须遵守）

- 格式：`b-<节号>-<块序号>`，如 `b-3-2` = 第 3 节第 2 块；
- **ID 与位置绑定**：同一内容块在原文中的位置不变，重新生成 paper-data.js 时必须沿用原 ID；只允许在追加新块或删除块时引入变化；
- 用户在阅读器里的所有批注都锚定 section_id + block id 写入 annotations.json——ID 漂移会让批注指向错误内容；
- 修订某块内容时改字段值即可，**不要顺手重排整个数组**。

## 7. annotations：AI 伴读批注（8 类）

挂在 block 上，类型与触发时机遵循 `annotation_guide.md`：

```json
{ "type": "concept", "title": "FlexiCubes", "body": "一种从连续场提取三角网格的技术（黑盒：场→可用的网格），类似'把雕塑翻成石膏模'。" }
```

- `type` ∈ `concept | key-conclusion | method | formula | figure | experiment | reading-reminder | critical-thinking`；
- **concept 类一句话化**（v2）：专有名词只需"是什么（一句话）+ 效果"，可附检索词（如"深入：搜 FlexiCubes SIGGRAPH 2023"）——用户需要时自己去查或进 Pass 3；
- 密度：每 1~2 个 section 1~3 条有信息量的批注，禁止空话；
- `body` 内可用行内 `$...$` 数学与少量 HTML（`<b>` `<i>` `<br>`）。

## 8. 转义与数学书写（最常见出错点）

- **JSON 内的 LaTeX 反斜杠必须双写**：`\frac` 写成 `\\frac`，`\text` 写成 `\\text`。示例：原文 $\text{Softmax}$ → JSON 里 `"\\text{Softmax}"`；
- `text_en / text_zh / body` 等文本字段允许行内 `$...$` 数学与少量 HTML 标签（`<b>` `<i>` `<br>` `<a href>`），**禁止**其他标签（会被阅读器过滤或破坏排版）；
- 希腊字母、上下标尽量用 LaTeX 而非 Unicode 近似字符（用 `$\\alpha$` 不用 `α`，保证打印 PDF 一致）。

## 9. 生成流程建议（超长论文防截断）

1. 通读全文后先定 pipeline（方法主线）与 self_check，再定 section 划分；
2. Write 首次落盘：完整顶层骨架 + `sections` 数组只含第 1 个 section（结尾保持 `"sections": [ {...} ]` 合法）；
3. 之后每个 section 一次 Edit，把新 section 对象插入数组末元素之后；
4. 全部写完 → 跑 deploy_reader.py 校验；报错信息会给出 JSON 解析失败的行号，定位修复后重跑。
