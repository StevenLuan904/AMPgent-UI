# AMPgent AceA 执行协议与足迹账本

状态：`active`
维护者：执行本项目的 agent
最后核对日期：2026-08-10（Asia/Shanghai）
适用范围：AMP 生成器、AceA 靶向结构评测、Rosetta 相对能量评测、相关 worker 与正式 run

本文件是项目的执行事实源。每次开始工作、自动唤醒、部署 worker、提交正式 run 或解释结果前，
必须完整阅读。具体 benchmark/config 文件是精确的科学协议；本文件负责串联永久边界、重要足迹、
当前状态和下一步门禁。

## 1. 决策优先级

发生冲突时按以下顺序处理：

1. 用户在当前对话中的明确指令。
2. 安全、权限、身份和来源完整性边界。
3. 已提交的 benchmark/config 及冻结输入、SHA、行数和顺序。
4. 本执行协议的当前状态与操作门禁。
5. 历史 heartbeat、handoff、旧报告和 agent 记忆。

旧 heartbeat 可能滞后，不能据此重跑已完成阶段或绕过新门禁。遇到不一致时先只读核查仓库、
数据库、Temporal 和对象存储，再决定是否行动。

## 2. 不可变科学边界

- `v20/v20b/v20c`：`conflicted`。
- `v20d`：四条均为最终 `conflicting`。Rosetta 只能解释为“若姿势正确，则同协议能量可能有利”。
- REU 仅可在完全相同 Rosetta 协议内作相对比较；不得转换或表述为亲和力、Kd、结合概率或实验效力。
- PepMLM `ΔNLL` 仅为 `low-confidence`、`rank-only`、`out_of_domain` proxy；不得覆盖结构冲突。
- `v22/v23` 已冻结，`v24` 已终结，`v25` AMP-Designer 已锁定；不得重跑、补抽、改阈值或回写。
- `v26` 的约 `1e-15` 末位差是过严工程门禁造成的历史失败，不是科学矛盾；不得改写历史状态。
- `v27/v28/v29` 已完成锁定。HemoPI2 与其他软安全模型的结果不是湿实验安全证据。
- AMPlify 已由用户永久停用。除非用户明确反转，不调试、不运行、不分片、不替换、不纳入评分。
- 所有模型输出均为计算预测，不是实验结果，也不是 AceA 结合或亲和力证据。
- 当前不规划湿实验；不得自行选择实验候选或写入湿实验门槛。

## 3. 数值、身份与 fail-closed 规则

近似数值默认容差：

- `absolute_difference <= 1e-8`，或
- `relative_difference <= 1e-6`。

约 `1e-15` 的有限浮点尾差属于正常数值噪声，不能单独终止研究。只有在差异超过容差、跨越冻结
阈值、改变标签/排序/方向或足以改变结论时升级。

以下内容必须精确一致，不使用数值容差：

- 序列、candidate ID、sequence SHA；
- 行数、行顺序、一一对应关系；
- 输入/输出 SHA、源码/权重 revision；
- 协议设置、随机种子、分类标签；
- generator、seed、候选来源与 tool-call 映射。

仅在以下情形 fail-closed：来源损坏、数据泄漏、不安全加载、输入错误或缺失、映射破坏、非有限值、
安全违规、worker 身份/版本不明，或差异足以改变科学结论。不要因无害数值噪声或低价值软指标失败
而停掉主线研究。

## 4. 服务器、凭据与进程边界

### 4.1 永久操作规则

- `192.168.99.32` 当前被用户明确禁止：不得登录、探测 GPU、提交任务、停止进程或触碰工作负载，
  直到用户明确解除。
- synth `192.168.99.2` / GPU4 不属于当前 AMPgent 任务范围，不访问、不部署、不借用 poller。
- `.19` 上只允许操作可明确归属于 AMPgent/PepAgent 的 worker；不得停止 OmniEpic、训练、Moba 或
  其他用户进程。
- 先启动新 worker 并确认 poller 接管，再停止旧的、已精确识别的同角色 PID；有 active workflow 时
  禁止 worker 切换。
- Temporal 显示 poller 在线，不等于已确认物理主机或代码版本。必须把 poller identity、远端 PID、
  `PEPAGENT_WORKER_ROLE`、`PYTHONPATH` 和活动 release 对齐后才能提交正式 run。

### 4.2 SSH 与凭据

- 服务器访问资料只引用：`D:\DWorkspace\yangyang\皮肤抗菌短肽\tool\SERVER_ACCESS.md`。
- 不把密码复制进本文件、仓库、命令行、进程参数、日志、commit 或聊天回复。
- `.19` 链路需要跳板与内网机两次交互式认证；单纯 `BatchMode/ProxyJump` 不适用时，不应误判为
  服务器宕机。
- 如使用临时 askpass，helper 只能从上述未跟踪文档读取字段，不得内嵌或另存口令；完成后立即删除。
- SSH banner 超时时不终止其他连接、不清理共享 session；稍后重试或请求用户恢复访问。

### 4.3 2026-08-10 已核实的 `.19` 足迹

- 主机名：`admin.cluster.local`。
- 项目根：`/data1/huangyueshan/pepagent`。
- 活动 release：`/data1/huangyueshan/pepagent/platform/current`，当时指向 release
  `339c4e48141830e3bad49ed3d6fb2a472d10ee57f11aefbd40771f9a19e52835`。
- 当时可见的 PepAgent 进程只有 `PEPAGENT_WORKER_ROLE=pepmlm`，使用 `CUDA_VISIBLE_DEVICES=3`。
- Temporal 的 Boltz2/Rosetta poller identity 分别为 `2914797@admin.cluster.local` 和
  `2914804@admin.cluster.local`，但它们未与 `.19` 可见进程完成身份映射。因此不能假设它们在 `.19`，
  更不能在位置未知时提交 v31b。

这些 PID/identity 是审计足迹，不是永久配置；每次提交前必须重新核查。

## 5. Git、测试、归档与用户文件

- 分支：`agent/mvp-v2-autoresearch`；远端：`https://github.com/StevenLuan904/AMPgent.git`。
- 稳定变更必须依次执行：检查 diff → `ruff check .` → `pytest -q` → 有意图地 commit → push。
- 生成新的内容归档并记录 SHA；不得把未提交工作树部署到 worker。
- 用户已有 `docs`、handoff ZIP、既有产物和无关 dirty worktree 默认不可修改。
- 本协议与 `AGENTS.md` 是用户本次明确要求新增/维护的例外；后续只在规则或状态实际变化时更新。
- 不提交凭据、`.env`、模型权重、缓存、数据库或机器专属配置。

## 6. 正式 run 的统一门禁

任何正式 run 提交前必须全部满足：

1. 协议、输入队列、选择规则、seed、预算和停止条件已先 commit/push。
2. 输入文件 SHA、行数、顺序、全局唯一性和逐行 sequence SHA 通过。
3. 所需实现已 commit/push，完整 ruff/pytest 通过，内容归档 SHA 已记录。
4. 本地 API、PostgreSQL、MinIO、Temporal 健康；active workflow 数为 0（除非协议明确允许并行）。
5. control、GPU、CPU worker poller 在线，且物理主机、角色、PID、活动 release/source revision 已核实。
6. worker 不在 `.32`、synth GPU4 或其他禁止资源上；不会争抢或停止他人任务。
7. task queue、工具版本、权重 SHA、环境 SHA 与预注册协议一致。
8. 唯一 formal run 尚未提交；数据库和 Temporal 中均无同协议重复 run/workflow。
9. 提交后立刻记录 run ID、workflow ID、cohort SHA 和提交 commit；之后禁止重复提交。

任一项不满足时保持未提交。poller 在线但位置/版本未知，属于 worker 身份不明，应 fail-closed。

## 7. 当前 v31 Phase A 足迹（已完成，禁止重跑）

- run：`46796d6f-2c94-49fa-82e0-2d7716423b10`。
- workflow：`pepagent-generator-structure-v31-46796d6f-2c94-49fa-82e0-2d7716423b10`。
- 队列：90 条；HydrAMP、AMPGAN v2、AMP-Designer 各 30；每 generator seed 各 10。
- cohort SHA：`6aff2088b09dc57ff1981fec176b4c02f51b5b0ead5819b8332ac589f81db746`。
- audit SHA：`dfb1e855648bdff41e37a8ea691cba637431d218bae37c65efcaf6aca6cf96b8`。
- 每条：1 个 pocket-forced Boltz pose + 8 个 Rosetta ref2015 decoys。
- 完成度：90/90 Boltz、90/90 坐标审计、90/90 Rosetta；最终失败 0。
- 候选报告 SHA：`138fbfdc6cfe10d7fbb68a0165887a2a53924153757db0ce77147285ad09b583`。
- generator×seed 汇总 SHA：`ed9a17d23ecc3c5ea07d92ccc44d167f5cb4e606366d2bfdf7f2cb1e1735386a`。
- generator 汇总 SHA：`281671415a5664646bc897d0aca2f91ed4c66ca07cb6b7635f426120ddcb3592`。

同协议结果是 non-dominated：

- AMPGAN v2：中位代表 Rosetta dG 最有利，约 `-39.74 REU`。
- HydrAMP：中位口袋覆盖最高（`0.80`），肽骨架位移最低。
- AMP-Designer：中位 pair-ipTM 略高（约 `0.173`）。

Phase A 不能证明任何生成器全面胜出，也不能单独决定替代 PepMLM。

## 8. 当前 v31b 足迹与唯一下一步

精确协议：`config/benchmarks/amp_generator_target_structure_v31b.yaml`。
实验 spec：`config/experiments/acea_generator_structure_confirmation_v31b.yaml`。

已冻结确认队列：

- 18 条；HydrAMP、AMPGAN v2、AMP-Designer 各 6 条；全局序列唯一。
- 选择方法：每 generator 独立 Pareto 前沿，再做确定性 normalized-Levenshtein maximin。
- 禁止选择输入：PepMLM、AMP/MIC、安全软分数、加权总分。
- cohort SHA：`0e9801456c1fcd6eddd3d87c6dbff9cd744228ace38208e03559d10af419cc7b`。
- audit SHA：`f73c95cae79de8942841b2ec44612ca7a9c438157173116ecb7e6714a4c85de0`。
- selection revision：`32f86d1c381b66445f23b8ee3646b6499329f4d1`。

确认协议：

- Boltz seeds：`20260911, 20260912, 20260913`；每候选 3 个独立姿势。
- 每姿势 16 个 Rosetta ref2015 decoys；每候选合计 48 个 decoys。
- 所有姿势均做 Rosetta，不得只取代表姿势。
- 主要比较：跨 seed pair-ipTM、口袋覆盖/接触、碰撞、姿势一致性、代表与最低 Rosetta dG 分布。
- 报告候选级、seed 级、generator 级效应和方向一致性；禁止加权总分和强制单胜者。

实现足迹：

- `88e0a5b`：实现 v31b all-pose confirmation protocol。
- `7b93e78`：把 Rosetta limitation 修订为适用于任意 decoy 数的 predicted-pose 语义。
- 最近完整验证：ruff 通过，pytest `245 passed`。
- 内容归档 SHA：`278fec1231efba1baa4cb8d71f59b3f8cb7be0427244aa44186955b9217ce9f1`。

当前状态：`ready in repository, formal run not submitted`。

唯一下一步：定位 Temporal Boltz2/Rosetta poller 的实际主机，确认其不是 `.32` 或 synth GPU4，并部署/验证
包含 `7b93e78` 的允许 worker release。完成 worker revision 映射后，重新执行第 6 节全部门禁，再提交
唯一 v31b formal run。不得为了推进而把任务发给位置或版本未知的 poller。

## 9. 每次 heartbeat 的最小检查表

1. 完整阅读本文件“当前 v31b”部分和对应 config。
2. 只读检查 API health、active workflows、目标 run 状态、候选/证据计数。
3. 查询 control/Boltz2/Rosetta poller 的 identity、last access 和 build ID。
4. 若涉及部署/提交，核对 poller 的物理主机、角色、PID、PYTHONPATH、活动 release。
5. 检查 `.32` 与 synth GPU4 禁令，没有明确解除就不访问。
6. 无变化则安静；只在核心结果、严重异常、需要输入、阶段 CSV 或最终验收时通知。
7. 阶段完成后更新本文件的足迹、run/workflow ID、SHA、测试数和下一步。

## 10. 协议维护责任

本文件不是一次性交接笔记，而是持续维护的执行事实源。当前执行 AMPgent/AceA 工作的 agent 是维护者，并须遵守：

- 新版本预注册、正式 run 提交、阶段完成、核心结果落库、主机或 worker 迁移、工具/权重/source revision 变化、用户改变科学或安全边界时，必须同步更新本文件。
- 会改变下一步执行判断的更新，应与对应代码、配置或结果处于同一稳定提交；若结果文件不入库，也必须记录其绝对/仓库相对路径、SHA、行数与生成提交。
- 每次更新后运行协议契约测试、`ruff check .` 与完整 `pytest -q`，检查 diff 后再 commit/push，并记录内容归档 SHA。
- 不因普通 heartbeat、临时 PID、无变化的健康检查或推测性信息制造版本噪声。短期观测只有在影响安全门禁或当前正式 run 时才进入正文，并标注日期和“观测而非永久配置”。
- 历史足迹只追加澄清，不静默改写。发现旧记录错误时，保留旧状态，另写勘误、证据来源和对后续结论的影响。
- 每次开始工作先核对“当前状态”和“唯一下一步”；任务结束前再次核对，确保协议没有落后于实际提交、run 或用户指令。

维护完成的定义是：规则可由测试检查、关键足迹可由 SHA/ID 追溯、当前阻塞和唯一下一步明确，且没有复制任何凭据。

## 11. 结果解释模板

报告任何核心指标时同时写清：

- 数值与方向：更高/更低在该协议中意味着什么；
- 可比较范围：是否仅限同协议、同 seed 或同模型；
- 不确定性：seed 变化、IQR、方向一致性、姿势一致性；
- 支持的决策：可否进入确认、是否 non-dominated、是否需要更多结构 seed；
- 不支持的声称：实验活性、安全、AceA 结合、亲和力或全面胜者。

默认结论形式：“在冻结的同协议计算评测中，A 在指标 X 上更有利，但 B/C 在 Y/Z 上保持优势；结果为
non-dominated，需要预注册确认阶段判断稳定性，不构成实验或结合证据。”
# 12. v32 multiobjective Agent protocol (2026-08-11 append-only update)

User authorization now permits a new v32 iteration after v31 Phase A; it does not authorize a
v31/v31b rerun or rewrite. v31b remains frozen and unsubmitted. The exact v32 contract is
`config/benchmarks/amp_multiobjective_portfolio_v32.yaml`.

v32 is a fresh AMP-Designer run with three new seeds. It introduces separate membrane-descriptor,
AMP/MIC, toxicity/hemolysis-risk, and balanced Pareto portfolio lanes. It forbids a weighted total,
a forced winner, PepMLM, AMPlify, score-based refill, and any experimental activity/safety claim.
Net charge is recorded as provenance only and is forbidden from selection, ranking, mutation, or
tie-breaking. Explicit positive-charge design is reserved for a separately preregistered v33.

Database evidence is a formal completion gate. Every generation and metric invocation must create
an in-run ToolCall; every candidate must reference its generation ToolCall; every Evaluation must
reference its exact metric ToolCall; metric calls must depend on all source generation calls; the
portfolio AgentDecision must retain all input and output edges; raw output, environment, adapter,
portfolio, and replay artifacts must be content addressed; all state changes must have lifecycle
events. CSV/JSON reports are exports and cannot be used to repair or backfill database evidence.

The formal workflow must finish with a database-only replay bundle that reconstructs the exact
candidate order, metric joins, concordant-risk exclusions, Pareto depths, lane membership, lane
ranks, and portfolio output SHA without reading intermediate working-directory files. Missing or
ambiguous nodes/edges, non-finite required values, SHA mismatch, or replay mismatch fail closed.

Implementation state at this append: workflow, persistence edges, deterministic portfolio, and
database replay verifier are implemented locally; `ruff` passes and the full suite reports
`255 passed`. Worker identities now include role, physical host, PID, and explicit source revision.
The v32 implementation is frozen at `a12fc0d84b2e4fe3587eb1e351089f6a0d3b7172`; the formal run is
still unsubmitted. Do not submit until the
allowed local control/metrics/portfolio workers are mapped to that revision, all service gates are
rechecked, and PostgreSQL plus Temporal contain no prior v32 run/workflow. Host 192.168.99.32 and
synth GPU4 remain prohibited.

Pre-submission gate update: source archive for repository commit `2c2d5d2` is
`var/archives/ampgent-source-2c2d5d2.zip`, SHA-256
`812aa8404d7fae7620e13fafd66ff445ed9a1ec4424f8a287fe4fa6a9c78c62f`. The allowed local host
`StevensOMEN9` currently maps control PID 22496, metrics PID 49700, and portfolio PID 10608 to
explicit Temporal identities ending in source revision
`fefaa3ce7c3b243e444fbd3037ab8a5829431759`. These PIDs are dated observations, not permanent
configuration; the exact identities and last-access timestamps must be checked again immediately
before submission.

The final preflight audit found and fixed lost-response retry hazards in generator freezing,
AgentDecision persistence, and replay verification. Revision
`a12fc0d84b2e4fe3587eb1e351089f6a0d3b7172` recovers the already-committed rows instead of
advancing to a different raw subsequence or duplicating decisions.

Retry-safe implementation archive: `var/archives/ampgent-v32-implementation-a12fc0d.zip`, SHA-256
`255a412a79aa4e146b84429bda7ef0491cdc3130a281e820f4f932fba9a391c6`. The current allowed local
mapping is control PID 34500, metrics PID 87616, and portfolio PID 67356 on `StevensOMEN9`; all
three Temporal identities explicitly end in revision
`a12fc0d84b2e4fe3587eb1e351089f6a0d3b7172`.

v32 formal submission is now immutable and must not be repeated. Run ID:
`d695853e-cb94-4608-ad71-e4d7c4df1e85`; workflow ID:
`pepagent-multiobjective-v32-d695853e-cb94-4608-ad71-e4d7c4df1e85`; submitted manifest SHA-256:
`5b29bcf0dd0de3d02b27ef4ecafb1ec30aa27e7cec4016b1b11b18dbcdfc9b69`. Monitor and repair only
within the frozen scientific protocol; never create a replacement run.

## 13. v32 formal completion (2026-08-11 append-only update)

The unique v32 formal run completed successfully and is now locked against rerun, refill, threshold
change, or result rewrite. PostgreSQL status is `succeeded`; Temporal status is `completed`. The run
started at `2026-08-10T17:49:45.017983Z` and finished at
`2026-08-10T18:20:36.897062Z`.

The frozen evidence graph contains 300 candidates, 10 ToolCalls, 6000 Evaluations, 24 typed
ToolCallDependency edges, and one succeeded AgentDecision. The preregistered concordant-red rule
excluded 109 candidates and retained 191 eligible candidates. The final non-weighted portfolio has
24 globally distinct selections: six each in membrane, activity/MIC, risk-control, and balanced
lanes. No positive-charge objective or tie-break was used.

The portfolio artifact SHA-256 is
`d50b0b77e8e04f86f6b8d48fa3bc24f9d96a43aa9016a315f4004cca0db6d0e3`. The database-only replay
bundle SHA-256 is `4c3eef0a74f6db34503d605154c5d2ea7aa5035cc706c1d33d7001b363315634`;
its recorded `exact_replay` result is true. The complete decision, lane ranks, exclusions, source
calls, metric calls, dependencies, artifacts, and candidate/evaluation joins are recoverable from
PostgreSQL plus content-addressed object storage; CSV or working-directory files are not required.

Scientific interpretation remains limited: this is a computational multiobjective hypothesis
portfolio, not experimental AMP activity, MIC, safety, AceA binding, or affinity evidence. Several
activity- or membrane-oriented selections retain single-model hemolysis warnings; the risk-control
lane trades predicted activity for lower soft-risk evidence. These conflicts are part of the result.

The current next step is result acceptance and a read-only v32 interpretation/export layer derived
from the database. Any new sequence generation or explicit positive-charge design requires a new,
committed v33 preregistration. v31 Phase A, v31b, and v32 remain frozen and must not be rerun.

Completion-state commit `91c3db5af21003fdd818898720febb24bfe3ae1a` passed `ruff check .` and
the full suite (`256 passed`). Its content archive is
`var/archives/ampgent-v32-completion-91c3db5.zip`, SHA-256
`bf6ab2c0197495d6412694d0ab475d66031b89591e24f9196b455cf0e4b17d95`.

## 14. v32 database-native acceptance layer (2026-08-11 append-only update)

The next authorized engineering stage is a read-only acceptance child run governed by
`config/benchmarks/amp_multiobjective_acceptance_v32.yaml`. It preserves the completed v32 parent
run exactly and records all new interpretation/export evidence under a new `ExperimentRun` whose
`parent_run_id` points to the locked v32 run. Cross-run mutation, candidate copying, rescoring,
threshold changes, and parent backfill are forbidden.

The child run must derive five content-addressed artifacts from PostgreSQL plus object storage only:
the 300-row candidate table, the 24-row portfolio table, the four-row lane summary, an acceptance
manifest, and a derived replay bundle. The export and replay verifier are typed ToolCalls in the
child run with a dependency edge; the acceptance verdict is an AgentDecision with exact tool edges.
Local CSV/JSON files are disposable copies of those artifacts.

At this append, implementation and contract tests pass (`258 passed`, ruff clean), but execution is
not authorized and no child run has been created. The next step is to commit/push and archive the
implementation, freeze its source revision into the acceptance contract, rerun all gates, and only
then submit the single database-native acceptance child run. A successful verdict means only
`ready_for_v33_preregistration`; it does not authorize v33 generation or execution.

The implementation is now frozen at commit `9b70351250c30687c459a1297a7ff8ffa5b2291f`.
Its content archive is `var/archives/ampgent-v32-acceptance-implementation-9b70351.zip`, SHA-256
`5ec361056521272664025936583485670ca4fc149d0f9ecb60a539a91d94bd4e`. The acceptance contract is
authorized for exactly one child run after the final API/PostgreSQL/MinIO/Temporal and duplicate-run
gates pass. The parent v32 run remains immutable.
