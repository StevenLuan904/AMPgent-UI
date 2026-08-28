# 发布边界、已知限制与后续填充清单

## 当前发布版的可信边界

- 真实数据来自 run `57afecc7-22e9-4efb-9051-acb11234013d` 的只读冻结快照。
- run 最终状态为 `cancelled`。序列 generation、metric evaluation 和 admission 已完成；structure 与 final portfolio 未完成。
- 900 条指 proposal occurrence；773 条指合法且去重后的 unique candidate。两种粒度不能混用。
- 35 条指 structure eligible，其中 26 mature core、9 exploration；不是最终实验验证候选。
- 8,503/8,503 表示 773 candidates × 11 registered metrics 均有 evaluation record。两个 label metric 的 numeric value 为空是数据类型语义，不是 coverage 缺失。
- 本次运行没有跨生成器重复序列，因此真实 origin set 都是单生成器集合；查询内核支持共享来源，但不能用 fixture 的共享比例冒充该 run 的真实结论。
- MIC、溶血、毒性均为模型或规则输出，不是湿实验测量。
- 冻结快照适合发布演示与可复现审计，不会自动反映之后写入 PostgreSQL 的新运行。

## 当前刻意不实现

- 不实现 Analysis Agent，也不提供自由 SQL 或自然语言直连数据库。
- 不允许连续分布转换为饼图等语义不合理图表。
- 不从未完成的 structure/portfolio 阶段推断最终科学结论。
- 不把源 PostgreSQL 作为可写 analytics cache。

## 发布后填充任务

### 统计与多目标分析

- 增加 P10/P90、效应量和 bootstrap confidence interval。
- 增加 Spearman、分层/条件相关与共同覆盖样本说明。
- 计算 dominated count、front membership、binding-constraint prevalence。
- 增加阈值放宽后的候选增量敏感性分析。
- 将“统计冲突、选择冲突、前沿冲突”实现为三套独立证据，不根据单一负相关生成 claim。

### 可视化与透视

- 增加 ECDF、ridge plot、UpSet、waterfall、hexbin 和 correlation matrix 渲染器。
- 为高基数 pivot 增加分页、Top-N 与稀疏矩阵模式，而不是简单放宽 500-cell 安全阈值。
- 为 loss segment 增加点击钻取、边界候选和 retained-vs-rejected 比较。
- 为真实存在共享来源的后续 run 增加 UpSet 与 fractional/full attribution 对照。

### 数据服务

- 实现正式 `/v1/analytics/query`、result cache 和 definitions API。
- 建立可重建的 candidate origin、metric long、funnel、Pareto 与 structure coverage 派生数据集。
- 对重型 bootstrap、structure aggregation 和大 cohort comparison 使用异步 job。
- 为 snapshot 增加签名清单、过期策略和多 run catalog。

### 产品体验

- 增加卡片模板保存、dashboard 分享和 query permalink。
- 增加键盘可访问的字段移动与 resize 操作。
- 为每种拒绝 code 补充就地修复建议和一键恢复默认布局。
- 增加发布环境 telemetry、前端 error boundary 和 Sentry 等正式告警接入。

