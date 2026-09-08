# Analysis 数据内核交接说明

本文档描述可独立合并的数据/查询层，不包含 App 路由、全局样式或具体卡片 UI。主线只需从 `src/analysis/dataKernel.ts` 导入公开 API。

## 真实发布快照

`public/data/launch-analysis.snapshot.json` 是从 PostgreSQL 只读导出的真实运行冻结快照：

- run：`57afecc7-22e9-4efb-9051-acb11234013d`
- run status：`cancelled`，UI 必须原样展示，不能包装为成功
- 原始 proposal occurrence：900（3 个生成器 × 3 个 seed cell × 100）
- promoted / unique candidate：773；invalid：127
- 评分证据：8,503 / 8,503，11 个指标；数值型指标 9 个、标签型指标 2 个
- admission：26 `mature_core`、124 `promising_uncertain`、623 `rejected`
- structure eligible：35（26 mature core + 9 exploration）
- 数据边界：序列生成、评分和 admission 已完成；structure 和 final portfolio 不完整，不允许推断

快照保留 occurrence、candidate、origin set、每项评分证据、OOD/limitations、拒绝原因、Pareto front、生成 cell、模型/工具版本、权重/环境/output 哈希。`snapshotSha256` 对实际传输文本做封印；浏览器适配器默认校验，内容被修改会拒绝加载。

重新导出（PowerShell）：

```powershell
$env:PEPAGENT_DATABASE_URL_SYNC = '<postgresql+psycopg connection string>'
& '<backend-python.exe>' '.\scripts\export_analysis_snapshot.py' `
  --run-id '57afecc7-22e9-4efb-9051-acb11234013d' `
  --output '.\public\data\launch-analysis.snapshot.json' `
  --generated-at '<UTC ISO timestamp>'
```

导出器只执行 `SET TRANSACTION READ ONLY`；凭据不进入快照。

## 加载策略

```ts
const snapshot = await loadAnalysisSnapshot({ runId })
```

适配器先尝试 `/v1/analytics/runs/{runId}`，响应缺失、非 2xx、契约不合法或 digest 不匹配时，再读取 `/data/launch-analysis.snapshot.json`。若使用冻结快照，返回值会追加 live API 失败原因。两个来源都失败时抛出 `AnalysisSnapshotLoadError`，不会暗中替换成 fixture。

## 卡片粒度查询

每张卡片持有独立的 `AnalysisCardQueryState`：

- `query`：受控 query key、grain、dimensions、measures、filters、metrics、attribution
- `slots`：`row`、`column`、`value`、`category`
- `chart` 与带理由/得分的 `recommendedCharts`
- 独立 `revision`，拖动字段只产生该卡片的新不可变状态

受控 query keys：

1. `run_quality`
2. `generator_funnel`
3. `metric_distribution_by_generator`
4. `metric_distribution_by_stage`
5. `origin_composition`
6. `admission_outcomes_by_generator`
7. `rejection_reasons_by_generator`
8. `coverage_by_metric`
9. `pareto_conflicts`
10. `candidate_table`

生成器共享来源支持三种归因：`full`、`fractional`、`exclusive`。贡献率默认使用 fractional，来源集合分析使用 exclusive，避免共享候选被无意重复计数。

## Overview 规则生成

```ts
const { cards, rejections } = generateCardsFromOverview(snapshot, {
  nodeIds: ['generation', 'scoring', 'safety', 'admission'],
  metrics: ['llamp_log10_mic_um', 'macrel_hemolysis_probability'],
  generators: ['amp_designer'],
})
```

规则行为：

- generation / deduplication / admission 生成各自 stage funnel
- scoring 为每个指标生成独立分布卡，并生成 coverage 卡
- safety 生成 gate/rejection 卡
- 选择至少两个有效指标时生成 Pareto conflict 卡；2 个指标推荐 scatter，3–7 个推荐 parallel
- structure / portfolio 在当前冻结快照中显式返回 `node_unavailable`
- 未知指标只拒绝该指标，仍可为其他合法选择生成卡片

## 字段拖动与显式拒绝

```ts
const nextCard = moveCardPivotField(snapshot, card, 'generator', 'row')
```

槽位规则：row/column/category 只接受维度，value 只接受度量或 `metric_value`；字段不能重复；row/column/value/category 容量分别为 2/2/4/1；估算透视单元超过 500 会拒绝。所有拒绝均为 `AnalysisCardRejectedError`，携带稳定 code、path 和可显示 message。

主要拒绝场景：未知 query/metric/filter、grain 不兼容、重复/过多维度、非法范围、scatter/parallel 指标数错误、funnel 无 stage、错误槽位、槽位超限、图表不兼容、高基数、卡片小于绝对可读尺寸。

## 图表推荐与尺寸响应

推荐器同时考虑字段语义和真实基数：

- ordered stage → funnel；generator + stage 可备选 Sankey
- 连续评分 + 低分类基数 → boxplot；样本 ≥100 且分组 ≤8 才推荐 violin
- 两个目标 → scatter；三个以上 → parallel
- 一维分类 → bar；二维低基数 → heatmap / stacked bar
- 无分类轴 → KPI；所有合法布局保留 table 作为无损回退

`planCardPresentation(card, { width, height })` 输出：

- `compact`：只保留 title、主值、关键图形、warning、actions；标签仅关键值
- `standard`：恢复 axes、legend、filters、summary
- `expanded`：展示 details、provenance 和完整操作区
- 小尺寸自动降级复杂视觉，例如 violin→boxplot、Sankey→funnel、heatmap→bar、parallel/table→KPI
- 小于 180×120 或非有限尺寸显式拒绝

实际角落 resize handle 和 DOM 渲染由主线 UI 实现；本内核提供一致的最小尺寸、可见元素和视觉降级决策。

## 验证

```powershell
npm run test:analysis
npm run build
npm audit
```

当前测试包含小型可控 fixture 和真实 773-candidate 集成快照，覆盖查询注册表、非法组合、透视执行、共享来源、缺失/OOD、metric range、空结果、多卡隔离、Overview 生成、字段拖动、图表推荐、尺寸降级、live→frozen 回退、契约和 digest 篡改检测。

