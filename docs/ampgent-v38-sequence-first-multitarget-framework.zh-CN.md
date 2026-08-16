# AMPgent v38：历史继承、序列优先与多靶点并行框架

## 1. 当前边界

本文件描述框架升级，不是新的短肽实验。用户已要求停止 v37.0.15，并明确要求在再次生成短肽前先升级
Agent 框架。v37.0.15 的 Temporal workflow 已终止，PostgreSQL run 已闭合为 `cancelled`；900 个
Candidate、9000 个 proposal occurrence、11468 个 Evaluation、282 个 succeeded ToolCall、2 个
AgentDecision 以及已有结构阶段证据全部原样保留。没有删除、回填、替代运行或新正式提交。

v38.0.0 的执行状态是 `framework_implemented_not_authorized`。它只实现可验证的合同、数据结构与确定性
调度规划；`candidate_generation_authorized=false`、`structure_execution_authorized=false`、
`formal_run_authorized=false`。

## 2. 为什么改成序列优先

v37 的实际流程能够计算 MIC、AMP 活性、毒性、溶血和理化指标，但它先从每个生成 seed 的有效输出中保留
固定数量候选，再一次性评价并立刻挑 48 条上结构。主要缺口不是“没有打分器”，而是没有把这些打分器组成
一个可审计的序列成熟度闭环：

- 未在保留前比较全部有效、去重的原始 proposal，存在顺序依赖；
- 没有父子候选迭代优化、未改变父本对照和知识依据；
- MIC 双模型分歧、域外预测和排序稳定性没有成为结构准入条件；
- 毒性、溶血、活性和可开发性虽有数据，却未形成清晰的“成熟 / 冲突探索 / 拒绝”状态；
- 结构预算可能被序列证据尚未成熟的候选消耗。

v38 将流程改为：知识检索 → 全量有效 proposal 轻量评价 → 多轮可追溯改写 → 冻结的序列成熟度判定 →
质量准入后的多样性选择 → 多靶点结构确认。结构模型不再承担挽救弱序列的职责。

## 3. 历史版本如何继承

历史继承不是把旧候选或旧模型输出搬到新 run。`HistoricalEvidenceSnapshot` 在冻结的时间截止点读取全部
终态 run：

- `succeeded` 作为决策 replay 证据；
- `failed` 作为完整失败分母；
- `cancelled` 作为完整取消分母；
- `created/running` 明确排除并记录 ID；
- 保存 run/target/spec/parent identity、终态事件和各阶段持久化计数；
- 不包含候选序列或模型值，不允许复制、回填或跨 run 复用输出。

这样新 harness 能分析“哪类工程故障反复出现、哪种策略在固定预算内完成得更好、证据在哪个阶段丢失”，
同时不把历史结果泄漏成新实验的答案。快照按规范 JSON 哈希，挂接既有 v36 `HarnessRelease` 和
`HarnessLineageEdge`，形成可回放版本谱系。

## 4. 序列成熟度合同

`SequenceMaturityPolicy` 把序列证据分为四组：

1. 活性：LLAMP 与 AMP-READ 的 log10 MIC 预测都必须成功，达到外部冻结阈值且相互一致；
2. 安全：ToxinPred3 score/label 与溶血风险都必须完整，失败、缺失或域外结果不能进入核心结构 lane；
3. 可开发性：电荷、疏水性、疏水矩和不稳定指数必须完整并通过外部证据阈值；
4. 稳定性：候选在冻结扰动/重采样下的排序稳定性必须过线。

阈值只能来自外部参考、provider 合同或明确的工程 guard，禁止用本批候选的分位数冒充生物学阈值。
判定结果只有三类：

- `mature_core`：全部门通过，可进入结构；
- `exploratory_conflict`：例如两个 MIC 模型明显冲突，保留研究价值但默认不消耗结构预算；
- `rejected`：缺失、失败、域外、安全红旗或硬门失败。

多样性只在 `mature_core` 内实施，且不得为凑满结构数量而强制补位。

## 5. 知识卡如何进入 Agent

知识不再只作事后注释。每次 proposal 或 refinement 前记录 `KnowledgeUseTrace`，包括 provider task
`019fad3e-76b8-7e32-8455-d2e9b31d33e5`、卡片 ID、查询 SHA、引用 passage SHA、采用/拒绝决定和理由。
任何改写候选至少需要一张明确采用的卡片，并保存父候选关系和未改变父本对照。知识支持仍不是分数，不能
替代 MIC、毒性、溶血或结构证据。

## 6. 多靶点并行，而非 AceA 单靶点

v38 复用 v35 的靶点资质化和 typed panel witness。目标面板必须在看到新肽结果前冻结，包含 2–6 个唯一
靶点，AceA 可以作 anchor，但不能是唯一靶点。每个靶点必须有 A/B 级证据、坐标哈希、native pocket、
wrong-pocket control 和 qualification witness。

共享序列阶段只执行一次。其输出是同一批 `mature_core` candidate IDs，然后并行分发到独立靶点分支：

- 所有靶点接收完全相同的序列集合；
- 每个分支有独立 evidence namespace 和失败分母；
- 每个分支使用相同预注册 Boltz seed 与 Rosetta decoy 预算；
- 一个靶点的结果不能改写另一个靶点的输入或选择；
- target-agnostic AMP lane 始终保留；
- 最终既可查看每靶点 Pareto，也可比较跨靶点稳健性，但禁止压成一个不透明加权总分。

因此“并行”是数据和证据意义上的隔离并行，而不只是同时启动几个进程。

## 7. 再次运行前仍缺什么

框架代码与单元合同完成不等于可以开新 run。下一次科学预注册至少还需冻结：

- 通过 v35 资质化流程得到的真实多靶点 panel witness；
- 各靶点独立坐标、口袋与 wrong-pocket 控制；
- MIC、毒性、溶血和可开发性外部阈值证据；
- 序列 refinement 模型/seed/轮数/预算；
- 每轮晋级数量与无强制补位停止条件；
- 多靶点结构资源 placement 和 per-target replay 合同。

在这些内容明确前，v38 保持 framework-only，不提交短肽生成流程。
