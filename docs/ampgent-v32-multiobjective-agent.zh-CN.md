# AMPgent v32：证据治理式多目标短肽 Agent

状态：正式运行完成并通过数据库单源精确重放；结果已锁定，禁止重跑或回写。

## 目标

v32 将膜作用描述、AMP/MIC 软活性预测和毒性/溶血风险正式纳入 Agent，但不把它们压缩成一个“总分”。它输出一个互补候选组合，而不是强制选出单一冠军。所有结果仍是计算假设，不是实验活性、安全性或 AceA 结合证据。

## Agent 哲学：治理证据，而不是混合数字

1. **先固定身份，再看模型。** 原始生成顺序、seed、序列、候选 ID、逐行 SHA 和模型环境先冻结；指标不能触发补抽、换序或回填。
2. **证据按终点分家。** 膜作用、AMP/MIC 和风险是三个证据家族。不同终点、不同训练集的软模型不会被伪装成重复实验。
3. **冲突是结果。** 两个 MIC 模型或两个风险端点不一致时保留分歧，不用平均数抹平。
4. **只做局部支配。** 每个家族内部计算 Pareto 前沿；跨家族用“最差家族深度优先”的 max-min 公平原则构建均衡席位，不做加权总分。
5. **组合优于冠军。** 最终组合包含膜作用型、AMP/MIC 型、低风险型和均衡型四类席位。每类都施加序列多样性与 seed 配额，避免同一模式占满结果。
6. **软风险采用谨慎否决。** 只有 ToxinPred3 与 Macrel 溶血同时给出红旗时才排除；单模型警告保留并明确标注。此规则控制计算风险，不等于证明安全。
7. **Agent 本身也必须可审计。** 决策策略、全部候选评估、支配深度、排除原因、候选组合和证据边都进入数据库。CSV 是数据库的导出视图，不能成为唯一事实源。

## v32 冻结范围

- 生成器：AMP-Designer；使用新 seed，三组各产生 1000 条原始记录。
- 每 seed 只按原始顺序保留前 100 条合法、唯一、长度 10–25 aa 的序列进入指标评估。
- 膜作用家族：疏水矩、疏水比例目标区间、最长连续疏水段。
- AMP/MIC 家族：Macrel AMP 概率、LLAMP MIC、AMP-READ MIC。
- 风险家族：ToxinPred3 hybrid score、Macrel 溶血概率。
- 每个家族 6 个席位，另设 6 个均衡席位；全组合最多 24 条。
- AMPlify 与 PepMLM 均不进入 v32。

## 数据库与完整复原合同

Temporal workflow 是唯一正式执行入口。数据库必须保存：

- ExperimentRun 的完整原始协议和 SHA；
- 每次生成与指标调用的输入、参数、seed、工具版本、权重 SHA、环境 SHA、原始输出 SHA 和状态；
- 原始输出与环境清单的内容寻址对象；
- 候选、序列 SHA、生成调用、指标 Evaluation 的一一对应；
- ToolCallDependency 形成的生成→指标→组合决策图；
- AgentDecision 的原始 prompt、response、结构化决策及其 SHA；
- AgentDecisionToolCallEdge 的全部输入/输出边；
- 可从数据库与对象存储重建同一候选组合的 replay bundle。

缺失任一关键边、非有限值、序列映射错误或 replay 不一致时，正式运行失败关闭，不得用 CSV 手工补齐。

## v33 路线：显式正电性设计（本版本不实现）

v32 仍记录 pH 7.4 净电荷，但它的方向为 `observe_only`，不得用于阈值、排序、条件生成、突变或 tie-break。

v33 才考虑显式正电性，预期包含：按长度归一化的电荷密度、K/R/H 的位置与比例、D/E 抵消、两亲性相位、正电与疏水协同风险，以及生成器条件控制或受约束突变。正电不会被定义为越高越好；v33 必须先预注册适用区间和风险护栏，再生成任何新序列。
# 数据库证据图与可重放验收（硬门槛）

v32 中每一步都必须先落 PostgreSQL：原始生成调用、冻结候选、每个指标调用、候选—指标一一对应、调用依赖、风险排除、Pareto 层级、portfolio lane 决策、AgentDecision 输入/输出边、对象存储 artifact 身份及生命周期事件。CSV/JSON 只是从数据库导出的只读视图，不得反过来作为选择事实源。

正式运行只有在“数据库单源重放”能够不读取工作目录中间文件、按冻结 config 重建完全相同的候选顺序、排除集合、lane 与 lane_rank，并得到相同输出 SHA 后才算完成。任何缺失节点、缺失依赖边、重复/错位候选、非有限指标或回放 SHA 不一致均 fail-closed，禁止手工补表或从报告回填。

## 正式运行结果（2026-08-11 锁定）

- run：`d695853e-cb94-4608-ad71-e4d7c4df1e85`
- workflow：`pepagent-multiobjective-v32-d695853e-cb94-4608-ad71-e4d7c4df1e85`
- 状态：PostgreSQL `succeeded`；Temporal `completed`
- 候选：300 条，三个 seed 各 100 条；6000 条 Evaluation
- 证据图：10 个 ToolCall、24 条 ToolCallDependency、1 个 AgentDecision
- 风险治理：109 条“双红旗”候选按预注册规则排除；191 条仍可进入 Pareto 组合
- 组合：24 条，膜作用、AMP/MIC、风险控制、均衡四个 lane 各 6 条
- portfolio artifact SHA-256：`d50b0b77e8e04f86f6b8d48fa3bc24f9d96a43aa9016a315f4004cca0db6d0e3`
- database-only replay bundle SHA-256：`4c3eef0a74f6db34503d605154c5d2ea7aa5035cc706c1d33d7001b363315634`
- 精确重放：通过；没有使用加权总分；没有优化正电性

这 24 条是互补的计算候选组合，不是实验活性、安全性、AceA 结合或亲和力证据。尤其是膜作用和 AMP/MIC lane 中多条候选仍有单模型溶血高风险警告；风险控制 lane 则以较低软风险换取较弱的预测活性。冲突没有被平均或隐藏。显式正电性仍按计划留给先预注册的 v33。

## v32 数据库原生验收层

正式 v32 run 保持只读。后续候选解释与导出使用独立的派生 child run，精确协议为 `config/benchmarks/amp_multiobjective_acceptance_v32.yaml`。它不得生成新序列、重新评分、改变阈值或向父 run 回写；只允许从 PostgreSQL 与内容寻址对象存储复原父证据。

验收层必须生成并落库五类 artifact：300 条全候选 CSV、24 条组合 CSV、4 条路线汇总 CSV、验收 manifest JSON、派生精确重放 JSON。导出 ToolCall 与重放 ToolCall、二者依赖边、AgentDecision、artifact links 和生命周期事件全部保存在 child run。只有父证据计数、原始 artifact 字节 SHA、父组合重放和派生导出重放均精确通过，才可以给出 `ready_for_v33_preregistration`；该结论只授权编写 v33 预注册，不授权生成或运行。

验收 child run `f87c4db4-83e5-4c6f-8f4e-3d52f5c40ce3` 已成功完成，父 run 候选数仍为 300。child run 保存 2 个 ToolCall、1 条依赖、1 个 AgentDecision、2 条 decision edge、6 个 run lifecycle event 和 5 个内容寻址 artifact。所有 11 个 v33 预注册门槛通过，结论为 `ready_for_v33_preregistration`。

路线级结果进一步显示真实 trade-off：膜作用 lane 的预测 MIC 中位数约为 LLAMP 8.15 µM、AMP-READ 19.35 µM，但 6/6 均有 Macrel 溶血高风险警告；AMP/MIC lane 的相应中位数约为 5.68/4.04 µM，但 6/6 同样为溶血高风险；风险控制 lane 的溶血概率中位数降至 0.426、6/6 为低标签，但预测 MIC 变弱至约 49.28/134.85 µM，且 2/6 有 ToxinPred3 单模型警告；均衡 lane 仍是 6/6 溶血高风险。这里没有“全能冠军”，而是被数据库完整保存的机制—活性—风险冲突。
