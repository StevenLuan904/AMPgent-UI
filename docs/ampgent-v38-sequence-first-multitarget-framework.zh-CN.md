# AMPgent v38：历史继承、序列优先、多靶点并行与运行控制

## 1. 目标和不可越过的边界

v38 的唯一科学目标是得到更好的、可解释且可追溯的短肽。它继承所有历史实验的证据图和失败分母，
但绝不复制、回填或复用旧 run 的候选及未闭环输出。每个新 run 仍是全新 exact-once 身份。

本框架把“科学合同”和“执行控制”分开：科学合同冻结模型、seed、评价、靶点、预算和 Pareto 语义；
执行控制可以修隧道、恢复精确归属的 worker、调整获准并发和延迟结构调度，但不能改科学变量、重复提交、
停止外来进程或触碰禁用 GPU。

## 2. Agent 架构

```text
Historical Evidence Agent ─┐
Target Qualification Agent ├─> Freeze Agent ─> Proposal/Refinement Agent
Knowledge Card Agent ──────┘                         │
                                                    v
                                      Sequence Metric Agents
                                      MIC / AMP / toxicity /
                                      hemolysis / physicochemical
                                                    │
                                                    v
                                      Sequence Admission Agent
                              hard safety ─> Pareto core ─> safe exploration
                                         └─> refinement when core is small
                                                    │
                                  same admitted cohort, frozen once
                          ┌─────────────────┬─────────┴─────────┐
                          v                 v                   v
                     AceA branch       GyrA branch         PBP2a branch
                     Boltz/Rosetta     Boltz/Rosetta       Boltz/Rosetta
                          └─────────────────┴─────────┬─────────┘
                                                    v
                                      Per-target + cross-target
                                      nonweighted Pareto / replay

Run Controller observes every stage through durable PostgreSQL/object-store counts,
Temporal history, queue pollers, service probes and allowed-capacity receipts.
```

各 Agent 只通过带 SHA 的持久化对象交接。一个靶点分支的结果不能改变另一个分支的输入；结构分支也不能
反向改写已冻结的序列 cohort。

## 3. 历史实验如何继承

`HistoricalEvidenceSnapshot` 在冻结截止时间读取全部终态 run：

- `succeeded` 是可 replay 决策证据；
- `failed` 是失败分母；
- `cancelled` 是取消分母；
- 非终态 run 排除并显式记录 ID；
- 每个 run 保存 target/spec/parent identity、终态事件、阶段计数和证据图 manifest SHA；
- 快照不携带候选序列和模型预测值，不能变成新 run 的输入捷径。

因此 v38 会从历史中学习“哪些执行故障反复出现、哪些阶段最耗时、哪些策略有完整证据”，同时避免把旧结果
泄漏成新实验答案。

## 4. 序列先成熟，再做结构

所有有效、去重 proposal 都先完成 11 项序列评价：LLAMP MIC、AMP-READ MIC、MACREL AMP 概率、
ToxinPred3 score/label、MACREL 溶血概率/label，以及 hydrophobic moment、hydrophobic ratio、最大连续
疏水片段和 pH 7.4 净电荷。这里使用真实 provider 的规范 metric name，不用无法落库验证的概念别名。
结构预算不会用来挽救序列证据薄弱的候选。

`V38SequenceExecutionContract` 将 3 个生成器 × 3 个 seed 冻结为 9 个 cell，每个 cell 请求 100 条，
共 900 个 raw occurrence。`build_score_all_proposal_cohort` 保存每一个有效、无效和重复 occurrence；所有
有效唯一序列按原始 source ordinal 进入评分，不存在“先生成 1000、只取前 100”的隐藏截断。

`persist_score_all_proposal_cohort` 将该合同接入规范数据库：9 个 generator cell 各绑定一个已经成功的
ToolCall，900 个 raw occurrence 无论有效、无效或重复都逐条写入；只有有效唯一序列物化为 Candidate，且
Candidate 的 proposal rank 沿用全局 source ordinal。完整且字节身份一致的重复调用只返回 recovered receipt；
只要检测到部分 occurrence、跨 run ToolCall、未完成 ToolCall、cell/seed/arm 漂移，就整笔 fail closed，禁止
把半批旧结果补齐成新证据。调用方必须用单一数据库事务包围整批写入。

执行侧使用 `generate_v38_sequence_cell` 和 `persist_v38_score_all_generation`。HydrAMP 与 AMPGAN-v2 复用
冻结模型/runtime 字节，但把 request contract 独立派生为每 cell 100 条；AMP-Designer 使用新的
`amp_designer_generator_v38_cli.py` 单 batch adapter。旧 v25/v37 adapter 不修改、SHA 不漂移。9 个 cell
全部返回且身份、数量、raw rank 连续性通过校验后，才允许在一个事务中创建 ToolCall、对象证据、occurrence
和 Candidate；缺一 cell、重复 cell 或任意 batch 不完整均不会产生部分科学 cohort。

`evaluate_v38_sequence_metric` 复用五个冻结真实评分 runtime，但使用独立 v38 logical identity、工作目录、
transition receipt 和 content-addressed compact reference。`persist_v38_sequence_metric` 只投影各插件声明的
规范 observation，要求每一插件覆盖 score-all cohort 中每一个 Candidate；五个插件的 observation 并集必须
精确等于上述 11 项。缺 Candidate、缺 observation、重复 observation、失败、对象 SHA/字节漂移或数据库 cohort
漂移都会拒绝整笔指标持久化。因而进入 admission 的分母是“全部合法唯一序列 × 11”，不是部分成功子集。

### 4.1 为什么不用任意外部活性阈值一刀切

外部活性阈值容易受模型标定、菌株和实验条件影响，可能把整批候选清空。因此 v38 只把以下项目作为硬门：

- 无效、重复、缺失、失败或域外的关键评价；
- provider 明确定义的毒性/溶血安全红旗；
- 防止明显无效数值的操作性 guard。

MIC、AMP 活性和可开发性不使用本批分位数，也不使用任意外部数值作硬生物学门，而是进入非加权 Pareto。
这保留真实权衡，不会因一个阈值导致“一个都进不来”。

### 4.2 三层准入

1. `mature_core`：通过安全/有效性硬门，排序稳定，按确定性非加权 Pareto front 进入核心；
2. `promising_uncertain`：安全但 MIC 模型有冲突、排序不稳或在核心预算外；最多占结构预算 20%；
3. `rejected`：安全红旗、无效、缺失、失败、域外，不能因名额不足而补位。

若成熟核心少于预注册最低数量，Agent 最多执行三轮有界 refinement。每个子代必须保存父候选、未改变父本
对照、知识卡采用理由和 mutation rationale。核心为零时继续 refinement，不降低毒性或溶血标准；轮数耗尽后
仍为零则 fail closed。未使用的结构名额保持为空，不强制填满。

`RefinementChildProposal` 对上述字段做机器校验：子序列必须真实改变、仍是 10–25 aa 合法短肽、至少有一条
采用的知识卡 trace，并与同轮未改父本 control SHA 精确绑定。这样 refinement 不能成为无来源的随机改写。

## 5. 知识卡进入真实决策链

provider task `019fad3e-76b8-7e32-8455-d2e9b31d33e5` 在 proposal/refinement 前检索。每次采用或拒绝都
持久化 `KnowledgeUseTrace`：card ID、query SHA、passage SHA、decision 和 rationale。知识卡只能提出可检验的
改写依据，不能代替 MIC、毒性、溶血或结构证据，也不能直接充当选择分数。

2026-08-17 已对冻结 provider runtime 做只读真实 smoke，context-pack SHA-256 为
`d918d8faac581eebdf665593dbd81f50f24482924ea2ede302b3d273595f0c53`。返回内容包含截短、两亲性斑块、
耐盐/血清/蛋白酶、定点 D-替换、脂化梯度以及效力-溶血-细胞毒性联合优化的正负配对规则。结论是“有用但
必须限域”：通用 AMP 编辑规则可用于 v38 refinement；AceA 直接靶向、胞内递送和条件依赖的内容不能外推为
GyrA/PBP2a 靶向证据。provider 自身也明确声明这些数值不是跨体系共识阈值，这与 v38 禁止任意活性硬阈值
完全一致。

## 6. 多靶点不是把 AceA 复制三遍

靶点面板必须在看到本 run 的新肽结果前冻结。每个靶点需要独立 sequence、coordinate、native pocket、
wrong-pocket control 和 qualification witness；只允许 A/B 级证据。AceA 可作 anchor，但不能是唯一靶点。

同一批已准入序列并行发送到 2–6 个隔离分支。每个分支有相同的 Boltz seed / Rosetta decoy 预算和独立
证据 namespace。最终同时保留 target-agnostic AMP Pareto、每靶点 Pareto 和跨靶点稳健性视图，禁止压成
不透明加权总分。

“并行”必须是可执行的调度合同，不能只是配置字段：结构任务表必须在每个 `parallel_wave` 内按靶点交错，
使 workflow 取出一个有界并发 batch 时同时包含不同靶点。按靶点整块排列、然后对连续项做并发，会悄然退化为
“单靶点内并发，多靶点间串行”，必须由合同测试拒绝。

## 7. Run Controller：多久检查、发什么任务

`pepagent.v38_run_control.RunControlPlan` 使用确定性阶段表：

| 阶段 | 主要 durable 产物 | 进度检查 | 无进展卡点窗 | 最长期限 | 资源 |
|---|---|---:|---:|---:|---|
| history/target/knowledge freeze | 3 个冻结 receipt | 5 分钟 | 20 分钟 | 60 分钟 | 本地 control |
| proposal generation | 预注册 proposal 数 | 5 分钟 | 20 分钟 | 120 分钟 | 本地 generator |
| sequence metrics | 每候选完整序列评价 | 5 分钟 | 15 分钟 | 90 分钟 | 本地 metrics/provider |
| refinement | 父子谱系和新评价 | 5 分钟 | 20 分钟 | 每轮 90 分钟 | 本地 generator |
| sequence admission | cohort decision receipt | 2 分钟 | 10 分钟 | 30 分钟 | 本地 control |
| parallel structure | 每靶点 Boltz/Rosetta | 5 分钟 | 30 分钟 | 12 小时 | 获准 GPU/CPU |
| Pareto/replay | decision + replay receipt | 2 分钟 | 10 分钟 | 45 分钟 | 本地 control |

长 activity 每 30 秒 heartbeat；每 15 分钟核对一次“实际 durable 计数 vs 阶段计划”；每 120 分钟返回一次
用户 review。卡点判断基于最后一次真实落库进展，而不是只看进程存活或日志滚动。

### 7.1 周期任务

- 每次检查：API、PostgreSQL、Temporal、对象存储、active workflow、poller freshness、失败/重试和阶段计数；
- 远端结构阶段：只探测获准 GPU、supervised tunnel 和真实 DB/Temporal/MinIO；
- 两次连续确认“资源空闲、归属清楚且无外来冲突”后，且 backlog 至少达到当前 slot 的 3 倍，才扩容；
- backlog 清零即释放该阶段多余 worker，不让 GPU 空转等待上游；
- 上游未完成时不提前启动结构任务；结构资源不可用时保留 durable backlog，不丢任务也不盲重提。

### 7.2 卡点处置顺序

1. 证据 SHA、run identity 或 exact-once 漂移：立即 fail closed，禁止下游；
2. DB/Temporal/对象存储异常：只读探针 → tunnel → 服务状态，不重复提交 workflow；
3. 队列无 poller：核验 PID/source/release/env/ownership，只恢复 AMPgent-owned worker；
4. 有 poller但 durable 计数在卡点窗内不增长：检查 pending attempt、retry history、stderr 和资源；
5. 普通 execution-only 缺陷：测试、冻结新 source/release，在同一 run 安全恢复 worker；
6. 不能在同一身份下安全恢复的缺陷：旧 run 闭合为不可变失败，再用全新版本 exact-once 恢复。

任何自动处置都不得改变模型、seed、序列身份、评价、靶点、预算、Pareto 语义或安全门。

## 8. 启动门与资源节流

v38 采用阶段化 placement：提交时先要求 history/target/knowledge 和序列阶段的本地 worker 完整；只有序列
admission 形成 durable cohort 后才要求结构资源。这样 GPU 不会在数小时序列计算期间空转。但靶点资格 witness、
当前 source/release、服务健康、exact-once key 和历史快照必须在正式提交前全部冻结。

若真实靶点 witness 不完整或获准资源不可达，控制 run 进入 `waiting_for_prerequisite`，不会把旧目录名称冒充
资质证据，也不会退回 AceA 单靶点。恢复条件满足后从 durable stage boundary 继续。
