# AMPgent v36 证据治理式 Harness Evolution 框架

状态：`governance_framework_frozen_not_authorized`

精确合同：`config/benchmarks/amp_harness_evolution_v36.yaml`。

本阶段不运行 challenger、不生成新短肽，也不修改任何冻结 run。它先回答一个更基础的问题：AMPgent
怎样从完整历史中学习，同时避免把事后调参、软模型自洽和成功案例筛选包装成“Agent 进化”。

## 1. Harness 不是一段 Prompt

v36 把 harness 定义为“版本化、可执行的认识论策略”：它规定 Agent 何时读取哪些证据、调用哪些
工具、如何分配预算、怎样更新 Pareto archive、何时停止，以及怎样把冲突转成决策。Prompt 只是其中
一个有 SHA 的组件；模型、工具、配置、失败分类、预算和证据边界同样属于 release 身份。

因此优化单位不是某条短肽的分数，而是“固定预算下，从证据到决策的全过程”。一个 harness 可以在
AceA 结构审阅上更好，却在 target-agnostic AMP lane 上更差；这种结果应保留为作用域专用策略，而不是
强迫产生全局冠军。

这与传统 AutoML 的差别是：v36 不在一个固定数据集上寻找最高单指标，而是管理证据可见性、因果归因、
失败分母和策略适用范围。候选生成或筛选所用指标不能同时充当唯一验收端点。

## 2. 每次只改变一个可归因主件

历史证据可提出变化的来源包括：重复失败、工具顺序与重试、证据来得过晚、计算浪费、Pareto lane
塌缩、上下文特异成功，以及 provider 被拒绝后怎样恢复。每个 challenger 必须预先记录：

- 完整分母中的失败模式和历史截止时间；
- 一个主要变化组件及其因果假设；
- 预期改善的端点家族、可能受损的端点家族；
- 可证伪条件和回滚触发条件。

禁止根据 prospective holdout 的最终结果搜索阈值，禁止挑候选身份，禁止在同一 challenger 中同时改
Prompt、预算、工具顺序和模型后再把收益归给其中一个。若 PepShot 或知识库有问题，变化请求退回
provider 自身任务；AMPgent 不用兼容补丁把工具“调到能过”。

## 3. 时间隔离比随机切分更重要

一次演化使用四个互不穿越的历史区域：

1. `proposal_history`：只用于归纳失败和提出假设，不可见最终 holdout 标签。
2. `counterfactual_replay`：只用历史决策当时已经存在的证据重新执行策略，不能补跑模型或重新评分。
3. `shadow`：challenger 对新 episode 给出决策并记录成本，但 champion 仍控制正式动作。
4. `prospective_holdout`：同输入、seed、预算和停止规则的盲化 champion/challenger 对照；判定锁定后才揭盲。

随机划分无法阻止后来的 policy 读取早期 run 的最终答案，因此每个 release 必须冻结 history cutoff、
允许的 evidence slice 和禁止的 holdout manifest。任何跨区 episode 或标签泄漏都 fail-closed。

## 4. 五关演化循环

```text
完整历史证据图归纳失败模式
→ 冻结最小变化假设
→ 历史时点一致的 counterfactual replay
→ 不影响正式动作的 shadow challenger
→ 同预算、盲化的 prospective champion/challenger
→ 按作用域晋级、保留为专用策略、拒绝或回滚
```

离线 replay 只能说明“若当时使用新 policy，决策会怎样变化”；它无法证明新工具产生的新证据有效。
Shadow 能验证安全性、成本和运行语义，但也不能晋级。只有新的前瞻、同预算对照通过后，才允许
`promote_for_declared_scope`。

## 5. 不设单一总冠军

评价保持五个端点家族独立：发现质量、错误控制、跨 seed/噪声稳定性、计算效率、证据完整性。晋级
至少要求一个预注册实用端点达到改善幅度，同时所有受保护端点家族不发生不可接受退化，并在独立
seed 或上下文复现。成本、失败和无效果都必须报告。

禁止用加权总分或单一 hypervolume 晋级。允许的最终决策只有：在声明作用域晋级、保留为上下文专用
策略、保留 champion、拒绝 challenger、回滚到已注册祖先。作用域晋级不等于全项目更优。

## 6. 数据库必须保存“策略本身”

现有 PostgreSQL 图已经能保存 ExperimentRun、ToolCall、Evaluation、AgentDecision、Artifact 和生命周期，
但没有一等的 harness lineage。把 `harness_id` 和 promotion 塞进自由 JSON 会让查询、约束和重放依赖
约定，不能满足长期自动演化。

因此 v36 执行前必须新增并迁移六类 typed entity：`HarnessRelease`、`HarnessLineageEdge`、
`HarnessTrial`、`HarnessAssignment`、`HarnessOutcome` 和 `HarnessPromotionDecision`。它们必须与现有
run、ToolCall、decision、artifact 和 lifecycle 图相连。当前明确标记
`typed_harness_entities_implemented=false`，所以本框架不授权任何 replay、shadow 或正式 trial。

database+object-store-only replay 最终必须重建：完整 release 谱系与 SHA、四个历史分区、每个 episode
可见的证据、所有分配与盲化、counterfactual/shadow 决策、前瞻配对效应和成本、以及晋级或回滚的
作用域。CSV、JSON 和 Markdown 都只是导出。

## 7. 回滚也是追加证据

回滚目标必须是已注册祖先；回滚创建新事件，不能删除失败 release 或改写旧 run。进行中的 run 按其
冻结 harness 完成，不能热切 policy。紧急禁用也必须有理由 artifact 和 lifecycle event。回滚后的 replay
仍要保留导致回滚的全部失败证据，防止后续版本重复犯错。

## 8. 下一步

下一独立阶段只实现 typed lineage schema、迁移、repository primitive 和 database/object-store-only
offline replay verifier，并用合成 fixture 验证泄漏、重复分配、谱系环、非法回滚和缺边均会 fail-closed。
它不读取候选结果来提 policy，不运行 challenger，也不生成短肽。实现、部署和任何真实 replay 仍需
单独冻结与授权。
