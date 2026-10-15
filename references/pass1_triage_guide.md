# 第一遍阅读（Pass 1：鸟瞰初筛与文献库管理）执行规范

## 1. 阶段目标
用 5~10 分钟快速判断单篇或多篇论文的价值与领域归属，提取核心 5C 属性与六要素超简版，并完成文献资产库（PaperVault）的登记入库，帮助用户决定是否深入。

---

## 2. 核心 5C 属性判定标准
- **Category (类别)**：这篇论文属于什么类别？（如：实证度量型、系统架构型、理论推导型、综述 Survey、算法改进型）。
- **Context (背景)**：它与哪些既有工作相关？基于什么理论假设？
- **Correctness (假设有效性)**：其核心假设是否成立、有无明显的物理/逻辑硬伤？
- **Contributions (核心贡献)**：本文最核心的 1~2 个学术贡献是什么？
- **Clarity (表达清晰度)**：论文写作质量与实验支撑是否扎实、是否清晰可读？

---

## 3. 六要素超简版提取清单
1. **所属细分领域 (Domain)**：如“三维高斯飞溅 3D-GS 动态场景重建”；
2. **解决的核心痛点 (Core Problem)**：指出传统方案的瓶颈；
3. **方法论关键点 (Method Prototype)**：一句话概括其提出的核心机制；
4. **证据置信度 (Evidence Level)**：
   - 🌟🌟🌟 **高置信度**：在权威公开基准（Benchmark）上显著超越 SOTA，且代码已开源，消融实验完备；
   - 🌟🌟 **中等置信度**：自建数据集或特定场景验证有效，但泛化性待考证；
   - 🌟 **低/待验证**：纯理论推演或实验样本极小。
5. **推荐阅读优先级 (Priority)**：`High (必读核心)` / `Medium (按需参考)` / `Low (可快速略读或淘汰)`.

---

## 4. 输出与入库格式

### 4.1 批量初筛流程与状态流转

1. 逐篇提取后立即写 `papers/<paper_id>/metadata.json` 并登记入库（状态先设 `unread`）；
2. 全部登记完再输出**一张批量总览表**；
3. **🔴 停**：请用户回复编号。据答复流转状态：选中的 → `triage_passed`，明确不读的 → `rejected`，未提及的保持 `unread`。流转完成前不得进入 Pass 2。

`paper_id` 规则：`<year>_<小写短横线slug>`（如 `2026_anigen_unified_s3_fields`）；`metadata.json` 至少含：`id / title / title_cn / authors / year / venue / doi / status / priority / p1_summary{domain, core_problem, method_key, evidence_level, read_priority} / created_at`。

### 4.2 对话框输出表格
```markdown
### 📚 论文鸟瞰初筛报告 (Pass 1)

| # | 标题 (中英对照) | 年份/会议 | 核心贡献与方法亮点 | 置信度 | 推荐优先级 |
|---|----------------|-----------|-------------------|--------|-----------|
| 1 | **3D Gaussian Splatting for Real-Time Radiance Field Rendering**<br>（用于实时辐射场渲染的三维高斯飞溅） | SIGGRAPH 2023 | 以 3D 高斯作为可微显式场景表征 + 快速平铺光栅化，1080p 实时 100+ FPS | 🌟🌟🌟 高 | **High** |
| 2 | ... | ... | ... | ... | ... |

> 💡 已全部登记至 PaperVault（状态 unread）。**回复编号选择深入阅读哪几篇；说明哪些不读（我会标记 rejected）。**
```

### 4.3 写入与状态流转
统一通过 `python "${CLAUDE_SKILL_DIR}/scripts/vault_manager.py" --vault <库路径>` 登记与 `--status <paper_id> <状态>` 流转。
