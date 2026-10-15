# 标准化 Markdown 文献卡片模板 (notes.md)

以下为每篇论文沉淀至 `PaperVault/papers/{paper_id}/notes.md` 的标准模板结构：

```markdown
---
id: "{{PAPER_ID}}"
title: "{{TITLE_EN}}"
title_cn: "{{TITLE_CN}}"
authors: "{{AUTHORS}}"
year: {{YEAR}}
venue: "{{VENUE}}"
doi: "{{DOI}}"
status: "{{STATUS}}" # triage_passed | reading | completed
priority: "{{PRIORITY}}" # High | Medium | Low
tags:
  - 文献卡片
  - {{DOMAIN_TAG}}
created_at: "{{CREATED_AT}}"
updated_at: "{{UPDATED_AT}}"
---

# 📄 文献精读卡片：{{TITLE_CN}}

## ① 基础文献信息与引用

| 字段 | 内容 |
| :--- | :--- |
| **英文原题** | {{TITLE_EN}} |
| **中文译名** | {{TITLE_CN}} |
| **作者列表** | {{AUTHORS}} |
| **发表期刊/会议** | {{VENUE}} ({{YEAR}}) |
| **DOI / 链接** | {{DOI_OR_URL}} |
| **文献类型** | [期刊论文(J) / 会议论文(C) / 预印本(P)] |

### 📚 GB/T 7714-2015 格式引文
```text
{{GBT7714_CITATION}}
```

### 🏷️ BibTeX 条目
```bibtex
{{BIBTEX_CODE}}
```

---

## ② 核心研究问题与动机 (5C & 6要素)

- **一句话核心贡献**：{{ONE_SENTENCE_CONTRIBUTION}}
- **研究背景与痛点**：{{BACKGROUND_AND_PAIN_POINTS}}
- **本文提出的核心机制**：{{CORE_METHOD_PROTOTYPE}}
- **证据与置信度**：{{EVIDENCE_AND_CONFIDENCE}}

---

## ③ 方法流水线（Pass 2 大致理解层，技术当黑盒）

```mermaid
graph LR
    A[输入] --> B[{{NODE_1_NAME}}]
    B --> C[{{NODE_2_NAME}}]
    C --> D[{{NODE_3_NAME}}]
    D --> E[输出]
```

**核心创新**：{{NOVELTY}}

### 1. {{NODE_1_NAME}}
- **输入**：...
- **用了什么技术（黑盒）**：技术名（一句话是什么）：输入→效果
- **为什么用它**（可选）：...
- **效果与证据**：...
- **给下一步什么**：...

### 2. {{NODE_2_NAME}}
- **输入**：...
- **用了什么技术（黑盒）**：...
- **效果与证据**：...

---

## ④ 批注答疑归档 (Pass 3 定点打开黑盒)

{{RESOLVED_ANNOTATIONS_SECTION}}
<!-- 每条格式：**[章节名] "quote 片段"** 用户的批注 → 三层解答要点（白话类比/机理/工程） -->

---

## ⑤ 边界探索与失效场景 (Boundary & Corner Cases)

- **未明说的隐式假设**：
  1. ...
  2. ...
- **Corner Cases（失效场景）**：
  1. ...
  2. ...
- **计算/存储瓶颈**：...

---

## ⑥ 创新启发与课题拓展 (Research Ideas)

- **💡 思路 1（渐进改进型）**：
  - *改进点*：...
  - *预期难度*：低~中 | *验证路径*：...
- **💡 思路 2（跨域结合型）**：
  - *改进点*：...
  - *预期难度*：中 | *验证路径*：...
- **💡 思路 3（痛点攻坚型）**：
  - *改进点*：...
  - *预期难度*：高 | *验证路径*：...

---
> 🤖 **生成说明**：本卡片由 `academic-reading-master` 自动构建与归档，已同步保存至个人文献资产库 `PaperVault/`。
```
