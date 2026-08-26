# AMPgent Analysis Dashboard 顶层框架

## 1. 决策摘要

本产品采用“自研产品层 + 确定性分析服务”的架构，暂不加入 Analysis Agent。

Metabase 开源版可以作为内部临时查询、数据巡检或运营 BI 辅助工具，但不作为 AMPgent 面向用户的主 Dashboard。原因不是它不能画图，而是它无法在开源版中同时满足以下组合要求：产品级品牌视觉、可嵌入的原生交互、科学专用图表与卡片、稳定的数据粒度语义、Pareto/来源交集/筛选损失等领域行为。

当前代码只完成顶层产品骨架。真实聚合、统计检验、数据库查询、缓存与异步任务留给后续模块填充。

## 2. 产品信息架构

一级工作区：

1. **Overview**：一次科学运行发生了什么；展示流程、状态、节点摘要与证据入口。
2. **Analysis**：为什么得到这个结果；展示分布、来源、损失、风险与多目标冲突。
3. **Evidence**：可复现证据、模型/运行版本、数据快照和方法说明。

Analysis 的交互顺序固定为：

```text
选择 run → 明确数据粒度 → 选择阶段/集合 → 选择生成器 → 选择评分器 → 查看兼容卡片
```

不允许用户在没有明确粒度时直接提问“300 条里有多少条”。系统必须区分：

- `proposal_occurrence`：原始生成事件，同一序列可出现多次；
- `unique_sequence`：按规范化序列身份去重；
- `candidate_metric`：候选与某一评分器的观测；
- `candidate_target_structure`：候选、靶点与结构证据的组合。

## 3. 专业问题覆盖矩阵

| 问题族 | 必答问题 | 顶层卡片 | 后续实现重点 |
|---|---|---|---|
| Run quality | 原始量、去重率、覆盖率、缺失、OOD、入选率 | Run quality | snapshot 级统计 |
| 来源与贡献 | 每个生成器的 occurrence、独占 unique、共享 unique、最终贡献 | Origin composition | origin set / UpSet |
| 分布 | 原始 300 条、去重后、candidate pool、admitted 的评分差异 | Score distribution | ECDF、箱线、效应量、CI |
| 筛选损失 | 每一步损失多少、为什么损失、边界附近是谁 | Lineage & yield | funnel / waterfall / reason matrix |
| 安全性 | 溶血、毒性、OOD、缺失和不确定性 | Safety profile | prevalence + uncertainty |
| 多目标冲突 | 冲突在哪里、哪个约束最常 binding、阈值放宽会增加谁 | Multi-objective frontier | Pareto rank、Spearman、门槛敏感性 |
| 候选审计 | 序列、来源集合、分数、Pareto、风险标记和证据 | Candidate laboratory | 服务端筛选、分页、导出、lineage |

“优化冲突”必须区分三个层次：

1. **统计冲突**：目标之间存在经不确定性评估后的负相关；
2. **选择冲突**：提升一个目标会系统性淘汰另一个目标较好的候选；
3. **前沿冲突**：Pareto 前沿上存在不能同时改善的区间。

负相关散点图本身不能完成结论。

## 4. 前端扩展结构

```text
src/analysis/
  AnalysisDashboard.tsx       # 页面编排、联动筛选、拖拽/缩放布局
  analysis-dashboard.css      # 当前配色与卡片视觉系统
  cardRegistry.ts             # 卡片元数据、默认布局、兼容粒度、扩展点
  contracts.ts                # UI 与分析服务之间的稳定数据契约
  frameworkFixture.ts         # 仅用于框架验收的显式示例数据
```

新增一张卡片的正确顺序：

1. 在 `contracts.ts` 增加结果类型，不把数据库行直接泄露给 UI；
2. 在 `cardRegistry.ts` 注册问题类型、兼容粒度、默认布局和扩展点；
3. 在单独组件中实现展示；
4. 在 Analytics API 增加确定性计算；
5. 补充 coverage、warnings、method、snapshot 和可复现 query spec；
6. 才把 fixture 替换为真实 adapter。

## 5. Analytics API 顶层契约

建议保留以下边界，不要求本分支实现：

```text
POST /v1/analytics/query
GET  /v1/analytics/jobs/{job_id}
GET  /v1/analytics/results/{result_id}
GET  /v1/analytics/definitions
GET  /v1/analytics/runs/{run_id}/summary
POST /v1/analytics/compare
POST /v1/analytics/pareto
```

查询输入使用 `AnalysisQuerySpec`。结果必须至少包含：

```json
{
  "data": {},
  "method": {},
  "coverage": {},
  "warnings": [],
  "source_snapshot": {},
  "query_spec": {},
  "result_id": "..."
}
```

所有统计值由确定性服务计算；前端只做视图变换，不重新定义科学口径。

## 6. 后台语义层

后续实现优先保证以下事实/派生数据集：

```text
dim_run
dim_candidate
dim_generator
dim_metric
dim_target

fact_candidate_occurrence
fact_evaluation
fact_stage_checkpoint
fact_candidate_target_assignment
fact_structure_evidence

candidate_origin_sets
generator_unique_yield
candidate_metric_long
candidate_metric_coverage
admission_reason_matrix
stage_transition_funnel
pareto_front_membership
objective_conflict_matrix
```

来源集合不能被压成单个“主生成器”。同一序列由多个生成器提出时，必须保留完整 origin set；否则“最终 35 条来自哪个生成器”会产生错误归因。

## 7. 拖拽 Dashboard 约束

- 12 列网格；卡片可拖动和缩放，默认按科学问题分区；
- 布局编辑显式进入/退出，避免浏览时误拖；
- 当前布局持久化在 `localStorage`，后续改为服务端用户偏好；
- 卡片库负责显示/隐藏，卡片注册表是唯一元数据源；
- 卡片尺寸变化不应改变统计口径，只改变视觉密度；
- 每张科学卡片最终都必须能打开 query、method、coverage、warning 与 snapshot。

## 8. Metabase 开源版评估

可以满足的部分：

- Dashboard 编辑模式支持网格内移动、缩放卡片和自动排布；
- 支持 tabs、filters、基础图表和 SQL/可视化查询；
- 可用于团队内部快速探索已经整理好的 analytics schema。

不能满足或会迫使购买 Pro/Enterprise 的关键部分：

- 自定义品牌外观、应用 UI 色板与 logo 属于商业版能力；
- 自定义 visualization 插件属于 Pro/Enterprise；
- 高级嵌入主题、认证嵌入和完整应用嵌入属于 Pro/Enterprise；
- OSS 嵌入还需要遵守 AGPL/Embedding License 约束；
- 科学专用的 ECDF、ridge、UpSet、专业 Pareto 冲突诊断、跨卡片候选选择与证据审计并非开箱即用。

因此建议：

- **主产品**：React + react-grid-layout + ECharts + 自有 Analytics API；
- **内部辅助**：可选部署 Metabase OSS，直连独立 analytics schema，用于临时查询和质量检查；
- 不把 Metabase iframe 作为 AMPgent 主交互层。

官方依据：

- Dashboard 卡片移动与缩放：https://www.metabase.com/docs/latest/dashboards/introduction
- Appearance 商业版边界：https://www.metabase.com/docs/latest/configuring-metabase/appearance
- Custom visualization 商业版边界：https://www.metabase.com/docs/latest/developers-guide/custom-visualizations
- 嵌入主题边界：https://www.metabase.com/docs/latest/embedding/appearance
- OSS/Embedding 许可：https://www.metabase.com/license/

## 9. 后续填充顺序

适合交给后续 Codex 的任务按依赖顺序拆分：

1. 实现 `GET /definitions` 与 metric/objective 注册表；
2. 实现 occurrence、unique、origin set 的只读聚合；
3. 接入 Run quality 与 Lineage & yield；
4. 接入 Score distribution 的 ECDF/箱线/coverage；
5. 接入 Safety profile 与 admission reason matrix；
6. 实现 Pareto rank、binding constraint 与阈值敏感性；
7. 接入 Candidate laboratory 的服务端筛选和审计入口；
8. 将 layout、可见卡片与 query preset 持久化到用户配置。

每一步都应替换一个明确 fixture 切片，并保留未接入部分的显式标记；禁止混合真实值和未标注的示例值。
