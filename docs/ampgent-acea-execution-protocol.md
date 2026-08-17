# AMPgent AceA 执行协议与足迹账本

状态：`active`
维护者：执行本项目的 agent
最后核对日期：2026-08-12（Asia/Shanghai）
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

### 1.1 第一性原则与结果优先执行方式

当前工作的首要目标是直接提高短肽候选质量，而不是把基础设施整洁、部署仪式或门禁数量本身当作
交付。候选质量包括生物学合理性、抗菌与膜作用潜力、风险控制、靶点相关结构证据、跨 seed 稳定性和
有用的序列多样性。每项工作都应反问：它能否改善候选、解释改善或失败的原因，或者保存复现该判断
不可缺少的证据；若三者皆否，默认删除、合并或降为非阻塞检查。

严格性只保留在会改变科学意义或资源安全的部分：冻结的科学变量与预算、精确序列/candidate 身份、
输入输出映射、候选选择与比较规则、PostgreSQL 证据落库、对象存储 artifact、database+object-store
replay、不可变失败历史，以及用户规定的资源禁区和外来进程保护。任何会改变这些内容的修复必须使用
新版本身份，不能回填旧 run，也不能静默改变科学合同。

不影响短肽结果的工程门禁应压缩到最小。worker 的正常最低记录只有：physical host、GPU（CPU worker
可记为 CPU）、PID/role、source revision，以及没有外来进程冲突的当前核验。除非额外 release、环境、
dashboard、receipt 或审计字段的缺失可能改变实际执行字节、丢失证据、破坏 replay 或越过资源边界，
否则不得让它们阻塞候选生成。routine 工程缺陷先做只读定位，再直接修复、做与风险相称的测试、记录
变更并继续；无需为每个普通修复反复等待用户确认，也不得在已有安全下一步时停在状态汇报上。

这不是降低科学标准。它把时间从无关仪式转回候选生成、五类序列评价、结构确认和非加权 Pareto
portfolio。追求速度时可以简化部署和记录形式，但不能简化序列身份、科学预算、证据落库/replay、
exact-once 新 run 身份、外来进程保护或 GPU 禁区。

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

- `192.168.99.32` 当前被用户明确禁止：不得登录、探测 GPU、提交任务、停止进程或触碰工作负载；
  该全机禁令明确包含 GPU3 与 GPU4，二者均不得用于 AMPgent，且不得以“仅使用其他卡”为由访问该主机，
  直到用户明确解除。
- 用户于 2026-08-12 明确授权使用除 `192.168.99.32` 外的其他 GPU，包括 synth 主机 GPU；使用前仍须
  精确核实物理主机、GPU、PID、角色、活动 release/source revision 与 AMPgent 归属，且不得停止、争抢
  或干扰 OmniEpic、训练、Moba 及其他用户任务。资源许可不等于 formal run 科学授权。
- 用户随后明确禁止 `.19` 的 GPU4；即使该卡瞬时空闲也不得调度、启动 worker 或用于 capacity fixture。
- `.19` 与 synth 上只允许操作可明确归属于 AMPgent/PepAgent 的 worker。
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
- 大数据位置与生命周期遵循 `docs/ampgent-large-data-location-ledger.zh-CN.md`。本地仓库/工作站默认只保存
  代码、冻结配置、manifest、文档、紧凑报告和内容地址指针；模型、原始生成批次、结构/decoy、数据库
  备份与运行时归档不得在本地无登记累积。
- `192.168.99.19` 可作为 AMPgent 大文件存储主机，精确目录、owner、来源 run/release、SHA/manifest、
  canonical/cache 角色和保留条件必须写入独立位置账本。该存储许可不授权 `.19` GPU4、未经批准的
  formal run、访问他人目录或停止他人进程。
- 正式运行的权威大对象仍进入内容寻址对象存储，typed identities/edges/lifecycle 进入 PostgreSQL；
  `.19` 文件系统副本、本地 CSV/JSON 或报告均不能替代 database-plus-object-store replay。
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
6. worker 不在 `.32`、`.19` GPU4 或其他禁止资源上；不会争抢、停止或干扰他人任务。
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

唯一下一步：定位 Temporal Boltz2/Rosetta poller 的实际主机，确认其不是 `.32` 且不会干扰他人任务，并部署/验证
包含 `7b93e78` 的允许 worker release。完成 worker revision 映射后，重新执行第 6 节全部门禁，再提交
唯一 v31b formal run。不得为了推进而把任务发给位置或版本未知的 poller。

## 9. 每次 heartbeat 的最小检查表

1. 完整阅读本文件“当前 v31b”部分和对应 config。
2. 只读检查 API health、active workflows、目标 run 状态、候选/证据计数。
3. 查询 control/Boltz2/Rosetta poller 的 identity、last access 和 build ID。
4. 若涉及部署/提交，核对 poller 的物理主机、角色、PID、PYTHONPATH、活动 release。
5. 检查 `.32` 与 `.19` GPU4 禁令；其他 GPU 仅在归属、进程和 revision 映射清楚且不干扰他人任务时使用。
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
synth GPU4 were prohibited at that historical checkpoint; the 2026-08-12 resource rule above now
allows non-`.32` GPUs subject to exact ownership/revision mapping and non-interference.

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

Preflight correction: the acceptance verifier now checks all parent graph counts plus the exact
eligible, concordant-red, and selected counts rather than candidate count alone. The final executable
revision is `3cc040f2d0e0f0b162031931f81668076425c518`; its archive is
`var/archives/ampgent-v32-acceptance-implementation-3cc040f.zip`, SHA-256
`a77f557801298e564e1c4d25a7c53eac9a6c3c0a62697ba3ba55832ef664c813`. The earlier archive remains
historical implementation evidence but is not authorized for execution.

The unique acceptance child run completed successfully and is now locked. Run ID:
`f87c4db4-83e5-4c6f-8f4e-3d52f5c40ce3`; database-native workflow identity:
`database-native-amp_multiobjective_acceptance_v32`. The parent candidate count remained exactly
300. The child evidence graph contains two ToolCalls, one dependency, one AgentDecision, two
decision edges, six run lifecycle events, and five content-addressed artifacts. Exact derived replay
passed and all eleven preregistration gates are true.

Artifact SHA-256 values:

- all 300 candidates CSV: `2fe865664555ccd0a197689c0b7c050f99b3cc9909fe05582e85d52f1b3f4f9c`
- selected 24 portfolio CSV: `8df8883d82746ddb642f31d10139461da626e83c76bdbbb19b7708b5da69b601`
- four-lane summary CSV: `7d0d5498a6a8b5e93538cfe05d36e04f490d8da6e5b01eab0f7f7bdbbe6c83ca`
- acceptance manifest JSON: `2bf72488c09f19f77018130db26a4a80998766548606c511b502e32a06eedf72`
- derived replay JSON: `40ac1ae58669c2d131f8ab94f112ba3253c60ed3eeb7689bfa451f8e2667aa77`

The acceptance verdict is `ready_for_v33_preregistration`. This means the v32 evidence governance,
database replay, endpoint-family separation, seed balance, risk exclusion, and charge provenance are
sufficient to write a new v33 protocol. It does not authorize v33 sequence generation, charge
mutation, thresholds, or execution. v32 and its acceptance child run must not be rerun or backfilled.

Completion-state commit `07e24ce6228d1d6a11a4f4ab1d1f986231955cbd` passed ruff and the full
test suite (`258 passed`). Its content archive is
`var/archives/ampgent-v32-acceptance-completion-07e24ce.zip`, SHA-256
`d6684f090e0ead48f1c14a0f0e9e4960593f92ed9090e877a3515810b8c15d64`.

## 15. v32 submitted-manifest evidence closure (2026-08-11 append-only update)

The final requirement audit found that the parent database recorded the submitted manifest's
canonical SHA but did not preserve the full manifest as a content-addressed Artifact. The first
acceptance child therefore still loaded the repository config to reconstruct the Pareto policy.
That is reproducible, but it does not satisfy the stricter user requirement that the completed
process be restorable from PostgreSQL plus object storage alone.

No locked run will be modified. A separately preregistered grandchild evidence-closure run is
defined by `config/benchmarks/amp_multiobjective_evidence_closure_v32.yaml`. It must recover the
exact submission payload from frozen Git commit `686cc713c5649985e7fec6d0b472002b13e11a44`, prove its
canonical JSON SHA equals the parent-recorded
`5b29bcf0dd0de3d02b27ef4ecafb1ec30aa27e7cec4016b1b11b18dbcdfc9b69`, store it as an Artifact,
then replay the parent portfolio and all acceptance outputs using only database rows and
content-addressed bytes. The closure run records its own sealer/auditor ToolCalls, dependency,
AgentDecision, edges, artifacts, and lifecycle events.

At this append, closure implementation tests pass, but execution is not authorized. The next step
is to commit/push/archive the implementation, freeze its revision, rerun all gates, and submit the
single append-only closure grandchild. Until it succeeds, the broader database/object-store-only
completion claim remains qualified.

The closure implementation is frozen at commit
`6074fa4585f74c4b5d61685928d667c4167f92bc`; its archive is
`var/archives/ampgent-v32-evidence-closure-6074fa4.zip`, SHA-256
`cf9f5a27c0a60319f5f5a4e8fcbb4ceb70b1520fe63ffd0a670909631a174f01`. The contract is authorized
for exactly one grandchild run after final service, identity, and duplicate-run gates pass.

The unique evidence-closure grandchild run completed successfully and is locked. Run ID:
`de9f72ae-e490-408d-9432-c71a75a3d499`; database-object workflow identity:
`database-object-amp_multiobjective_evidence_closure_v32`. The immutable run chain is v32 parent
`d695853e-cb94-4608-ad71-e4d7c4df1e85` -> acceptance child
`f87c4db4-83e5-4c6f-8f4e-3d52f5c40ce3` -> closure grandchild
`de9f72ae-e490-408d-9432-c71a75a3d499`, all succeeded. The v32 parent still contains exactly 300
candidates.

The closure evidence graph contains two ToolCalls, one dependency, one AgentDecision, two decision
edges, six run lifecycle events, and two artifacts. The submitted manifest Artifact SHA-256 is
`5b29bcf0dd0de3d02b27ef4ecafb1ec30aa27e7cec4016b1b11b18dbcdfc9b69`; it exactly equals both the
canonical submitted manifest SHA and the SHA recorded in the v32 parent spec. The final closure
Artifact SHA-256 is `1844609968f4e14abf727a7fa08d905f1e778d3d120e016eaae0eeeabe540ea7`.
Its recorded parent graph SHA is
`d1e701a32dd404826639531058b4bd9b6713891b38ea31f9551e9cd7d54793fc`.

The full 300-candidate evidence graph, exact 24-member portfolio, all five acceptance exports,
submitted Pareto policy, exclusions, lane ranks, and v33-readiness verdict are now reproducible from
PostgreSQL plus content-addressed object storage alone. Neither locked ancestor was modified. The
final verdict remains `ready_for_v33_preregistration`, with no authorization to generate or run v33.

Closure completion commit `57c69b2288b4ff971faae19cf5955310a7585ce8` passed ruff and the full
suite (`260 passed`). Its content archive is
`var/archives/ampgent-v32-evidence-closure-completion-57c69b2.zip`, SHA-256
`844215069d7e27ba0c0a25e9d07cca43fbe263b220d1cc73b1f8f4b5b01cc217`.

## 16. 长期目标与问题账本（2026-08-11 append-only update）

用户已授权建立并持续维护项目级长期目标。权威路线文件为
`docs/ampgent-long-horizon-goals.zh-CN.md`。该文件明确记录六个未解决问题：显式正电性设计、
Pareto 搜索充分性、文献知识卡干预、PepShot 结构审阅干预、多靶点泛化，以及由数据库历史证据
驱动的 harness evolving。

当前事实边界：v32 只在 191 条冻结合格候选及其固定指标中构建非支配组合，不能称序列空间或预算
范围已最优；knowledge cards 与 PepShot 虽已有接口/工具，但近期 mutation brief 仍允许或使用
placeholder，二者均未作为正式干预进入 v32。因此后续必须采用同输入、seed、预算的配对/消融实验，
不能把“接口存在”包装成有效性证据。

规划顺序为：v33 显式正电性与搜索充分性；v34 knowledge-card × PepShot 2×2 消融；v35 资格审计后
的多靶点面板；v36+ champion/challenger harness evolution。版本规划不是 formal-run 授权。任何新
生成、筛选、阈值或执行仍须先预注册并通过第 6 节全部门禁。v22-v32 及 v32 两个派生 run 继续锁定。

所有未来 Agent episode、知识检索、结构 review、Pareto archive 变化、harness 分配与晋级决策必须
形成 PostgreSQL typed evidence graph，并把外部知识库/PepShot 证据作为 immutable artifact 和
ToolCall dependency 纳入对应 run。只保存在外部工具数据库、Markdown 或本地目录不算正式接入。

长期目标首次冻结 commit 为 `54e712b38298b1922184ef488cbdc2cbd062c788`；内容归档为
`var/archives/ampgent-long-horizon-goals-54e712b.zip`，SHA-256
`b6b7947a06d7f60d1dc6e2fd2c26f96addf5d95e4d09ed32fd75391484524dca`。

长期目标现使用 L0–L5 能力成熟度和 Q1–Q6 完成证据矩阵管理。当前项目整体仅为 L2：AceA 单靶点、
冻结指标作用域内的数据库可复原计算决策；尚无工具干预有效、跨靶点迁移或 harness 自我改进证据。
契约测试会核对六个问题、v33–v36 精确 config 以及 v35a/v36a 未授权门，防止把 verifier、shadow 或
测试通过误写为科学问题已回答。该矩阵 checkpoint 为 commit
`db252e2b0713c4966fd414ab802f72eac9797b94`；内容归档
`var/archives/ampgent-long-horizon-maturity-db252e2.zip` 的 SHA-256 为
`334ad637931a4a3c31a5f0ee3b1d34ed2905257a2d7b49422d0d61e85abb13de`，验证为 ruff clean、pytest
`362 passed`。该更新不授权任何 formal/synthetic run。

## 17. v33 正电性与搜索充分性预注册草案（2026-08-11 append-only update）

v32 acceptance 仅授权编写预注册。当前已建立未授权草案
`config/benchmarks/amp_charge_search_sufficiency_v33.yaml`，叙事说明为
`docs/ampgent-v33-charge-search-preregistration.zh-CN.md`。草案只把锁定 v32 的 300 条数据库记录用于
generator coverage 与预算可达性诊断，不用其 charge-density quantile 定义分层、生物学目标、parent
选择或最佳区间，也不回写 v32。

目标规则已在 2026-08-11 审查后纠正：v32 自生成电荷分布只作 generator coverage diagnostic，不再
定义 v33 生物学目标。v33 现由原始实验支持为七个同 scaffold 臂：未编辑 baseline；在同一位置分别
引入 1 个 K、1 个 R 及其电荷保持对照；在同一组位置分别引入 2 个 K、2 个 R 及其对照。主干预只从
Q/N/S/T 编辑，避免把 D/E 去负电与加正电混淆。+8 净电荷与 0.50 电荷密度仅为 operational guard，
不是普适最佳阈值。Pareto 充分性使用 3 个开发 seed、2 个确认 seed 和每 seed 25/50/100/150/200 固定 checkpoint；
必须跑完整预算，禁止 adaptive early stop、加权总分、单一 hypervolume 完成声明或 global optimum 声称。

当前状态：`implementation_frozen_not_authorized`。没有 v33 run/workflow，也不得生成或
提交。文献驱动的 deterministic K/R dose block、同位置 control、checkpoint archive、累计新 ε-cell、
dominance witness、PostgreSQL persistence primitives 与 database+object-store-only replay verifier 已
实现并有契约测试。persistence primitives 尚未注册到 Temporal worker，因此当前代码不能形成 formal
workflow；这是刻意保留的执行隔离。实现已冻结为 commit
`fab5cac50b3d709e9435c732173bc22eba81a505`，归档
`var/archives/ampgent-v33-evidence-fab5cac.zip` 的 SHA-256 为
`1519d6b4e26546b5f28b2a5e7f0489f423232591dba25f9c5047eadfc2e3f55e`。唯一下一步是获得用户单独
formal-run 授权，再通过第 6 节全部门禁并注册/部署对应 worker activity。v32 三层 run 链保持不可变。

本阶段文献驱动预注册与纯确定性组件已冻结在 commit
`140c71f4e8bc1823faf64dce4125c53b82d471fd`，完整验证为 ruff clean、pytest `273 passed`。内容归档为
`var/archives/ampgent-v33-preregistration-140c71f.zip`，SHA-256
`c8224ac766c3b10ecefaeb443b42a5a570be0795e6b5b2a7418e3d188d65c1b3`。该 commit 不是可执行 formal-run
revision；PostgreSQL activity 与 object-store-only replay verifier 未完成前，`formal_run.implementation_revision`
继续为空。

本轮数据库证据实现采用以下不可变语义：原始 parent 同时是 `baseline_unedited`，不复制同序列行；其余
六臂是带精确 `parent_id` 的 child。跨 parent/arm 序列碰撞、缺 child、重复 child、artifact 角色挂错
ToolCall、文献/transform/archive 依赖缺失、移除成员无 dominance witness、描述符超容差或 replay SHA
不符均 fail-closed。replay 只允许读取 PostgreSQL 图和对象存储字节，不允许 config/report/CSV 回填。
上述旧句“未完成前”保留为 commit `140c71f` 的历史状态；当前实现已冻结，config 中
`formal_run.implementation_revision` 指向 `fab5cac50b3d709e9435c732173bc22eba81a505`，但
`execution_authorized=false`、`submitted=false`，不能据此执行。

2026-08-11 外部文献复核已把 claim-level evidence manifest 冻结为
`config/evidence/amp_charge_design_literature_v33.yaml`，SHA-256 为
`94096787d62233e9dca77f277bc24ec18ce512e9cb49db740255541f02b897e4`。source-record 审计现使
benchmark 与 manifest 精确闭合为九项 PMID：八项原始实验和一项仅作机制支持的 KR-12 模拟；R9
“高正电不足以推出抗菌活性”的边界反例已从叙事补入 claim-level manifest。每项均冻结 retrieval URI、
NCBI PubMed XML SHA-256、passage locator、证据等级和适用距离。config loader 还要求三组显式冲突
witness：K/R 方向依 scaffold 而变、电荷数量与活性/安全非单调、正电本身不足以推出活性。

正式执行必须把 manifest 与九份精确 source-record 字节作为 literature-freezer artifacts 写入内容寻址
对象存储，并将逐记录 SHA/passage、claim projection、冲突 witness 和 charge-transform dependency 写入
PostgreSQL。当前网页核验不等于这些证据已进入 formal Agent graph；来源字节漂移必须阻断并版本化
manifest，禁止静默刷新、用本地文档回填或依据 v32 自生成分布改变生物学目标。

source-record 审计实现冻结为 commit `0bb8fb65c7bc42f427e9c06e55c2fab4cb8a7e26`；当前 v33 benchmark
config SHA-256 为 `5bcf988937a0a51d39b4304c3e98ef454563abbe887fd1206d114d2e4aebfc54`。该阶段仍为
`execution_authorized=false`、`submitted=false`，没有新序列或计算效果结果。

revision 与账本回填 checkpoint 为 commit `cc999e6d3f45af9dddf656217540ec81bb560c53`；内容归档
`var/archives/ampgent-v33-source-evidence-cc999e6.zip` 的 SHA-256 为
`6cbde73186e00ecf558384cd286a2f6f9a5a1a18aae07186e8d7de585cb998cd`，验证为 ruff clean、pytest
`360 passed`。该 ZIP 仅是仓库 checkpoint，不得冒充 formal Agent evidence artifact。

2026-08-11 搜索充分性合同升级为 `v33-search-sufficiency-v2`。方法依据冻结在
`config/evidence/pareto_search_sufficiency_methods_v33.yaml`，原始字节 SHA-256 为
`b5c3629cf19d90a6962d048cbe6bf8ff1d6ee7bef7ae449ffe03c649aa5470e6`，规范化内容 SHA-256 为
`20b4298a71510763ef92ddd340fcd3bf52ce04d61e76de938393af37dcf98fc4`。它把“搜索是否充分”定义为固定
全预算完整性、末段 active/cumulative family-local ε-cell 稳定、开发/确认 strict-majority attainment
双向复现、成本观察和逐软模型剔除报告的合取证据；候选身份 turnover、front size、加权总分或单一
hypervolume 均不能单独宣布完成。`1 new cell/50 candidates` 与 `0.10` ε-cell turnover 是本项目预注册
实用阈值，不是论文给出的普适常数。允许的最强结论仅为
`saturated_within_protocol_and_budget`，禁止声称 global optimum。

数据库 verifier 必须从冻结 candidate stream、逐候选 Evaluation、成本和方法 artifact 重新计算全部
archive、active/cumulative ε-cell、双向 attainment、leave-one-soft-model-out 集合及最终合取 verdict；
不能信任导出的 assessment JSON。该升级尚未部署或执行，没有 v33 run、没有新短肽、没有正电性或
搜索饱和效果结果。可执行实现冻结为 commit
`56710db7fbc5f02d79d1a46046d0c14d4e080f30`；config 的 `formal_run.implementation_revision` 已同步，
回填后的 benchmark config SHA-256 为
`486a8ce423d06ab05df3847f1ebe12d73de6bff6a3a0976809da3e8cf11a765b`，全量验证为 ruff clean、pytest
`340 passed`；但 `execution_authorized=false`、`submitted=false` 继续有效。

revision 回填记录 checkpoint 为 commit `87b96532ac3cac6cc0bac785ccae5ca34757fa21`；内容归档
`var/archives/ampgent-v33-search-sufficiency-87b9653.zip` 的 SHA-256 为
`cb32279158e9f2f32827111677ff4aa201f7346909b38ba861eb401ec5339557`。这是实现与预注册合同归档，
不是 formal-run evidence，不改变未授权状态。

## 18. v34 文献知识卡 × PepShot 干预消融草案（2026-08-11 append-only update）

用户要求正式比较知识卡任务 `019fad3e-76b8-7e32-8455-d2e9b31d33e5` 与 PepShot 任务
`019fb910-f2dd-7be1-a7e6-bfe381512c25` 接入前后的效果，并要求所有 Agent 证据进入 PostgreSQL、可由
数据库与对象存储复原。接口只读核查确认：知识库 `amp-system-kb` v0.3.0 已有带 policy、retrieval
trace、evidence refs 的 Design Context Pack；PepShot 已有 verify、坐标审计、受控图片、review schema
与 validate-review 路由。但当前平台 mutation brief 仍使用 `pepshot-placeholder-v1`，知识字段也未形成
正式动态检索 ToolCall 和决策依赖。因此二者均不能声称已完成正式接入或改善 Agent。

当前已建立未授权草案 `config/benchmarks/amp_knowledge_pepshot_ablation_v34.yaml`，叙事说明为
`docs/ampgent-v34-knowledge-pepshot-preregistration.zh-CN.md`。它冻结四臂：baseline、cards-only、
PepShot-only、cards+PepShot；v32 数据库重放中的 24 个 portfolio parent 全量进入，每个 parent 跑四个
无共享记忆 episode。proposal、结构、Rosetta、revision 与评价预算相同，知识/PepShot 额外成本单列；
评价只见 opaque arm label，锁定 adjudication 后才揭盲。工具输出不能作为自身唯一验收端点，禁止
加权总分或只展示成功案例。

外部接口冻结足迹：知识 context schema SHA-256
`1c358a48ca1c4d27554925c02f47d9c72aa273685288935b9fa9c7c7a0c745da`，active policy SHA-256
`25fb7a5a4c8c1d001a2d313acefc065a98a709ee1f784661b3054fc01e146bb1`；PepShot agent contract
SHA-256 `28eb1ad5dc8a1124b4ccf7e228d30eb864222c75516fcd933737e1b60e288522`，request schema
SHA-256 `4860a5404f10500e0844e836eda2f64f43fed702333410276cd7e8dd19ef8957`，review schema
SHA-256 `e08a04a0dba156c0cccee59d668d2458b0c2301c1cf150834cfea26fa2d2b14d`。正式 run 必须把外部
pack/bundle 的原始字节作为内容寻址 artifact 导入对应 AMPgent evidence graph；只引用另一目录或外部
数据库不满足复原要求。

当前状态：`preregistered_draft_not_authorized`。没有 v34 run/workflow，不得生成或提交。24 个 parent
的 candidate ID、sequence SHA、lane/rank 与数据库重放顺序已经冻结，member manifest SHA-256 为
`f1955476cb761d9ca300a8fed00d9bb847e775ee5f4c1ef51d1346376a4f943e`。三个主要端点的 practical
margin 已在输出前冻结：确认后新颖非支配发现率改善 0.25 条/parent、结构冲突召回改善 0.10、无效编辑率
降低 0.10；其余端点最大允许退化分别为 0.10 条/parent、0.05、0.05。它们是 Agent 晋级幅度而非生物学
阈值。数据库持久化层已冻结于 commit `4f152bc31498e0fcf53fa47469dfd2d2791b163d`：新增 typed
`candidate_occurrences`，使相同序列在不同 arm/重试中的每次提出行为都不会被 Candidate 去重吞掉；并
实现精确重试 ToolCall/artifact/Evaluation/AgentDecision、冻结依赖图、96 个盲化判定后才可揭盲的门禁，
以及 database+object-store-only replay verifier。当前 evidence plan SHA-256 为
`94f008863a57ff306b3134e1e81f7b6ed4dac81ca45b03b5d8c9cbc0e32084b5`，覆盖 96 个 episode、770 个
逻辑 ToolCall 和 768 次 raw proposal occurrence。

真实外部接口的离线 adapter 合同已实现并通过隔离测试：知识侧校验冻结 task、原始 schema/policy
字节 SHA、`generated_at`、verified card 和可定位 passage，并生成 context/trace/policy/passage 四类
精确 artifact；PepShot 侧按实际 CLI 的 `valid` receipt 语义核验 bundle identity、artifact 数、priority-
first 顺序、全部请求图片 SHA、review validation 与科学声明边界，并生成 evidence plan 要求的五类精确
artifact。该实现尚未注册为 Temporal activity，也没有实际检索、渲染、review 或候选生成。不得把
persistence/adapters 注册到 Temporal 或提交 run；只有 shadow preflight 通过、另获 formal-run 授权并
完成 worker 身份/版本门禁后才可注册和执行。

该 persistence checkpoint 的记录提交为
`bba75e95b358eca205be0736f3d7b8600765355f`，`ruff check .` clean、完整 `pytest -q` 为 `294 passed`。
内容归档 `var/archives/ampgent-v34-persistence-bba75e9.zip` 的 SHA-256 为
`07ec7cbe1e5649e50df7b899c3cdb8ed04cb9bfa38eea43be573e07018e525af`。

v34 预注册、确定性 arm 分配/配对效应与 replay graph 完整性验证器已冻结在 commit
`29a352abb858e07086ffac943e2b5c939c97d940`；全量验证为 ruff clean、pytest `290 passed`。内容归档为
`var/archives/ampgent-v34-preregistration-29a352a.zip`，SHA-256
`cf5afb9ee7a4c01d1628323523abd15ff9589e52208def845c4b00d0b8ef6eba`。该 checkpoint 只冻结科学合同与
预执行验证器，不包含已注册 Temporal activity，也不授权运行或生成候选。

离线外部证据 adapter 实现冻结于 commit
`3f842967cab0c56e8c933b19afe5da98569de202`，config 中 `formal_run.implementation_revision` 已指向
该提交，同时继续保持 `execution_authorized=false`、`submitted=false` 和空 run/workflow ID。当前
config SHA-256 为 `ece9e8d2853dd727d98fdc8951ad0e5dcca03a99f3ebd2df0c8df7f7f224c365`，外部合同
footprint SHA-256 为 `912c8fd868d409b2ef6326007e5879cd4fbbc83b3c26c81ae986c0a0ae29b4be`，离线
preflight SHA-256 为 `e53c8f894df5f8d32c6cb09661c71e51ca91723d365c5d10f01b8b8cae6ef903`，结论仅为
`ready_for_isolated_shadow_fixture_not_formal_execution`。下一步仍是冻结可执行环境/source manifest
并运行隔离 shadow fixture；不得跳过另行授权与 worker 门禁。全量验证为 ruff clean、pytest
`306 passed`。记录提交为 `90bf0bf`；内容归档 `var/archives/ampgent-v34-adapters-90bf0bf.zip` 的
SHA-256 为 `5fe4157e2901f982ee4b8822a8140f512a2c4fe5ced8d3c7d9aa24a3faec92ee`。

后续 source-manifest 门禁冻结于 commit
`12cd18e9790fe67503709406c007d49cd5f677eb`。知识库 allowlist 共 15 个源码/合同/依赖输入，manifest
SHA-256 为 `402a7be05785ce2fbbf9e8be4d714af1aa6952aee26f60de17f8ee1bf7e4cad4`；PepShot allowlist
共 32 个文件，manifest SHA-256 为
`b9ab9ecb88d6d82c3e93d28909702ddd2b56632c437df5bd60627892258519fa`。清单不含机器绝对路径，复制到
其他根目录仍应得到相同 SHA。config 已指向该实现，当前 config SHA-256 为
`6ba458badbe8bb7e4446c9120b5cd5387f547f81c19812060868318c295e3388`，外部 footprint SHA-256 为
`8f792f0e780ae14a265821bea3a672881982cd3d397fe4627b79eb273a3394ec`，离线 preflight SHA-256 为
`c58b3af9f94c4e8cdf2d08167f647f5bba22121fac953ef2cfcde7447b711406`。该门禁只冻结可执行源码与
环境输入文件，不证明已部署 Python/PyMOL 环境匹配；下一步仍须验证实际可执行环境，再运行隔离 shadow
fixture。`execution_authorized=false`、`submitted=false`，无 Temporal activity、run、workflow 或新序列。
记录提交为 `696f696`；内容归档 `var/archives/ampgent-v34-source-manifest-696f696.zip` 的 SHA-256 为
`0f8f44fce80a43e489df62694520ae4e7b62b9134a832a149d2a72a27ceab91a`。

用户于 2026-08-11 明确新增 provider ownership 边界：AMPgent 只定义消费合同、证据映射与 fail-closed
校验；若 PepShot 或知识库自身不满足其声明的源码、环境或输出合同，必须回到对应工具任务和仓库修复，
不得在 AMPgent 侧写兼容分支、偷偷补包或绕过验证。只读探针确认 PepShot controller 合格，但既有
renderer 缺其环境合同声明的 `gemmi==0.7.5`；临时 AMPgent 平台环境也缺知识库 requirements 中的
pypdf/jsonschema/paramiko，不能充当知识库正式 runtime。修复请求已分别发送到 PepShot 任务
`019fb910-f2dd-7be1-a7e6-bfe381512c25` 与知识卡任务 `019fad3e-76b8-7e32-8455-d2e9b31d33e5`。
在 provider-owned revision、runtime manifest 和真实 fixture 交付前，不安装本地兼容环境、不运行 v34
shadow；这些环境失败也不是工具效果结论。
路径无关的 Python/conda runtime probe 与 requirement/import fail-closed verifier 实现提交为
`43fbe926cc8f3dcc0abc0231502b9079bc1a2368`，ruff clean、pytest `309 passed`；它只是消费侧门禁，
config 暂不指向该提交，须等 provider 发布新 revision 后一并重新冻结。
provider gate 记录提交为 `6e38bdb`；内容归档 `var/archives/ampgent-v34-provider-gate-6e38bdb.zip`
的 SHA-256 为 `6e813011781cba3f6d9ea255381f870d8401fdd81226808c20b034fcd76fc8c9`。

用户进一步明确：对 PepShot 的“不满意”也属于 provider-owned change request，不只限于依赖或接口
报错。若视图、finding、review schema、可复原足迹或科学审阅语义不满足冻结合同，AMPgent 必须把
可复现缺陷与验收标准发送到 PepShot 任务 `019fb910-f2dd-7be1-a7e6-bfe381512c25`，由该任务在自身
仓库修复、测试并发布新的不可变 release；AMPgent 不自行适配。知识卡 provider 同理由任务
`019fad3e-76b8-7e32-8455-d2e9b31d33e5` 负责。进入 Agent 流程的 rejection、变更请求、新 release
和验收 receipt 必须进入 PostgreSQL evidence graph，原始交付进入内容寻址对象存储。

消费侧自身的合同错误仍由 AMPgent 修复。例如 PepShot release 的固定 fixture bundle 只证明该发布
环境及合同可运行，不代表正式 episode 中每个候选 bundle 的身份；候选 bundle 必须各自验证，不能与
release fixture ID 强行相等。

两项 provider-owned 修复现已交付并通过官方 verifier。知识卡 release revision 为
`amp-kb-acea-shadow-6d0eea37f2c145df`（manifest SHA-256
`7fd21012bcbcbe519dd964b6c9c826f16532d257cbb721951cb3ab0c4023e518`）；PepShot release ID 为
`pepshot-34487cf9667a64c3-fe1e5382de8cab09`（manifest SHA-256
`b4f4b848f603f431e5db49bd66e018904c35c9eacf97ae83882d92e6710f2c5d`）。当前下一步仅是把两项 release
完整快照进入内容寻址对象存储，并运行数据库原生、无候选生成的隔离 shadow/replay。该验收不是工具
效果结果，也不授权 v34 formal run。

消费验收与 provider ownership checkpoint 为 commit
`96b4e02732939544a8b7a939de312e7d22ff0ad2`；v34 config 随后在 commit
`d24a55c1d2ab0f8276d09a904118f5d9e8224a3d` 冻结指向该实现。ruff clean、pytest `313 passed`；
config SHA-256 为 `f22e3db1c2c9ad0d9f7ab14cc7f7d676b23cdf20f68b049f785793775116be84`，evidence plan
SHA-256 为 `3f1cfe953cfec2da98145573843fe514570760a91f25247280369f6795c29380`。内容归档
`var/archives/ampgent-v34-provider-release-d24a55c.zip` 的 SHA-256 为
`4196f9ffb4017bcf2ed44e89d8b69c8e5aae611145f41abb6a72a807dad9ab05`。

### 14.4 v34 provider shadow 已完成并锁定（2026-08-11）

- 唯一 shadow run：`941ea473-82d6-4b70-9ede-5162a14bf8ce`；parent：
  `de9f72ae-e490-408d-9432-c71a75a3d499`；状态 `succeeded`。
- 证据图精确计数：0 Candidate、0 Evaluation、4 ToolCall、5 ToolCallDependency、1 AgentDecision、
  3 decision edge、6 Artifact、8 LifecycleEvent。
- shadow contract SHA-256：`03d007fc86f28b70edcd4949c476509403e69cfbc209527975c91a4bd773e7d2`。
- knowledge/PepShot release archive SHA-256：
  `cc04d5c67437f743c4b90595b15d0ba4e361c73b96319db07ea09eea8adce686` /
  `1cb5f3b642242a7c2d5bf0340137875d48438aa5a93e5e2db2ce82b5687556f0`。
- knowledge/PepShot persisted receipt SHA-256：
  `fff7cba95e73645a1241a586b60bb4fd958672f5e770cb42caa1c9992a114a23` /
  `e4e0ff844486ea755fcadeda517979832c3ef075a16d5d5f120df5608cd01cbd`。
- database+object-store-only replay bundle SHA-256：
  `390d5757ee55d7a010b66701b4d6fe0338eb97f4d84b87c20b572f74cc9ae73c`；`exact_replay=true`。
- 唯一结论：`provider_releases_replayable_for_v34_authorization_request`。没有评价工具效果，没有生成
  候选，没有授权 v34 formal run。

PepShot 基础消费 receipt 的旧记录 `2985342d...` 使用了错误 fixture 字段语义；追加更正后的完整 SHA-256
为 `d7ae26187004eb0949251753bea7389f6be6d4dea47713d00bde1ac9c1e7d487`。这是消费侧合同纠错，不是
PepShot 兼容补丁。今后无论是接口失败还是效果不满意，均直接向 PepShot 任务
`019fb910-f2dd-7be1-a7e6-bfe381512c25` 提交 provider change request；AMPgent 禁止自行适配。只有
provider-owned 新 release 通过只读验收后才可继续。

用户于 2026-08-12 明确禁止当前阶段执行 v34 消融，并要求将其保存到以后。v34 的合同、provider
shadow、replay 加固与 capacity 合同继续作为冻结的 deferred checkpoint 保留；不得提交正式 activity、
不得生成 v34 候选，也不得再以 v34 授权作为当前主线阻塞。

精确授权对象已整理于 `docs/ampgent-v34-formal-authorization-request.zh-CN.md`。只有用户明确回复
`授权 v34 正式 2×2 run` 才视为授权；同意继续规划、shadow 或一般性“继续”均不授权正式执行。
授权请求冻结提交为 `c708d48985c2f6252a329b45039b8731c3c2615b`；内容归档 SHA-256 为
`0ab4f6e71791f7ad19d45e8eaf315535eaa581470f2d45aa77340717a2c57cf6`。

v34.1 已补齐 provider change-request 的数据库复原合同。正式 evidence plan 增加固定 governance ToolCall，
即使无退回事件也必须持久化 ownership、冻结 release 与显式空 ledger；若拒绝 provider，必须记录拒绝
run、独立 child run、复现输入、违反合同、验收标准、外部任务 receipt、新 release 与只读复验 receipt。
AMPgent adaptation 必须为 false，active formal run 内禁止热替换 release。database/object-store replay 会
读取 ledger 原始字节并校验生命周期，而非只看 artifact SHA。实现 revision 为
`9e879fec1285d2a1071fde7cd2d874765409aa24`；回填后的 v34 config SHA-256 为
`b6adc410f99185f1f25c6205c57dc89223c0d685f5c2b80084a8cb39106318e6`，evidence plan SHA-256 为
`67020e0241cf2eb0dae954e9dd8767a5321207ea3b1b656aacd69d62f35f4939`，固定 771 ToolCall、1345 dependency。
验证为 ruff clean、pytest `368 passed`。该更新没有运行 PepShot 或 v34，也没有产生新 provider 缺陷。
revision/文档回填 checkpoint 为 commit `f76a8101588f2c34ecfce21ea941aaf30d3db96b`；内容归档
`var/archives/ampgent-v34-provider-governance-f76a810.zip` 的 SHA-256 为
`64b2db2692b2b640e5d3d3b91e03de4ee621a8c1a7f53c873c7689e7b642da54`。

### 14.5 v35 多靶点资格框架（未授权）

v34 等待正式授权期间，只允许推进不使用候选结果的 v35 target qualification 设计。精确合同为
`config/benchmarks/amp_multitarget_qualification_v35.yaml`，叙事说明为
`docs/ampgent-v35-multitarget-qualification.zh-CN.md`。状态为
`qualification_framework_frozen_not_authorized`：未选择具体靶点，未授权靶点审计、候选生成或 run。

下一阶段若获独立授权，先审计不少于 8 个新靶点候选并保存全部失败，随后才可在不读取任何 peptide/
Boltz/Rosetta/AMP/MIC/风险/PepShot 结果的条件下冻结 3–5 个新靶点面板。primary pocket 只接受 A/B
证据级；每靶点必须预先定义 native 与 wrong/decoy pocket，并保留 target-agnostic AMP lane。任何
多靶点结果仅是协议内计算迁移证据，不能称结合、亲和力、选择性或广谱靶向。

v35 qualification framework 冻结 revision 为 `6608c2c690a76d0dcf1e4b974613676204c00b9e`；config
SHA-256 为 `a722f0f74d486237a128327a3158ae71ee143577f3f8b7e4acb46505e38778da`；内容归档 SHA-256 为
`b33c54adeec43c736ba7d0ba340ce7d0cf813426e42b40358662463c591f17eb`。

v35 现新增 `v35.target-qualification-replay.1` typed 离线 ledger/verifier：至少 8 个 shortlist 项必须按
顺序保存完整通过/失败分母；A/B primary hard gate 后，使用 AceA-anchor-aware deterministic maximin
重算 3–5 个 panel member，并精确核对 source/sequence/structure/pocket/selection-witness artifact SHA。
任何 AMP/MIC、风险、Boltz、Rosetta、PepShot 或生成肽结果进入 target selection 都 fail-closed。
typed `TargetQualificationAudit`、`TargetPanelSelectionWitness`、`TargetPanelSelectionMember`、migration
`0011_target_qualification_lineage`、retry-safe repository primitive 与 database-row + object-store-only
projection verifier 已在仓库实现。它们会拒绝跨 target/run 证据、AgentDecision/ToolCall 脱链、artifact
漂移、冻结后追加 ledger 行和重试漂移。共享 PostgreSQL 尚未部署 migration，也未完成隔离合成数据库
acceptance，因此不得审计/选择真实靶点；没有新 target 名单或泛化结果。
typed persistence revision 为 `6767f603be82ff3370bd655eed67cc29e7b81080`；migration SHA-256 为
`08e486d8d4d267ba57b763a27aefed8db5c139e31e5c212e1eb46fe11c00d472`；回填后的 v35 config
SHA-256 为 `2a7b54a1ac1c7ace73cb3c39b3f6ab3eed6676dda033e73c866fe4883f9ec027`。全量验证为 ruff clean、
PostgreSQL migration 区间离线 DDL 可生成、pytest `355 passed`。
revision 回填 checkpoint 为 `09ec7cf025636cf1b67f83b5d6243c7aa497bf3f`；内容归档
`var/archives/ampgent-v35-persistence-09ec7cf.zip` 的 SHA-256 为
`3d8f923264e46c1c7f02c37fe9ddc1faa4f9694590a61978acf16ea18551d520`。
v35a 合成数据库闭环验收已预注册但未授权：精确合同为
`config/benchmarks/amp_target_qualification_synthetic_acceptance_v35a.yaml`，授权说明为
`docs/ampgent-v35a-synthetic-acceptance-authorization.zh-CN.md`。它冻结 8 条匿名合成 shortlist、完整
通过/失败分母、3 条面板成员、7 类负向探针以及 0 Candidate/0 Evaluation 边界。未经用户逐字授权短语，
不得部署 migration、提交 acceptance run、读取真实 target 或执行 panel selection；通过合成验收也不
授权真实多靶点研究。预注册实现 revision 为
`41aba8ba08405cde65479bfd802fd2c6b2891598`；回填后的 v35a config SHA-256 为
`b85d8542d1ab2f7f18b6c803fe8f6fea042dfcc0b1967ff3797b76af067befcb`；全量验证为 ruff clean、
PostgreSQL migration 区间离线 DDL 可生成、pytest `359 passed`。
revision 回填 checkpoint 为 `a9516bdb8d6521c505c5428ac24b6cd3af513f08`；内容归档
`var/archives/ampgent-v35a-preregistration-a9516bd.zip` 的 SHA-256 为
`8d4e3bfd1b4280725253fe4e7c1c543e541f33daffce216ca284ff72ec30f384`。
typed ledger/offline replay 实现 revision 为
`e47e0d3cf94d6b9d0b63c5a799694c13aeb819ca`；回填后的 v35 config SHA-256 为
`c9641143982940a0a05127e8b2e0081837a499b13770fc4c0ac6ecbad63a0c81`，全量验证为 ruff clean、
pytest `352 passed`。该实现仍不是数据库部署、target audit 或 panel execution 授权。
revision 回填 checkpoint 为 `d79858dc3aa42399e439abaabc7d2e0fbe42bc70`；内容归档
`var/archives/ampgent-v35-target-replay-d79858d.zip` 的 SHA-256 为
`31b549ee748bd07edd083351732c8c4f76f1fbb4c8f8326d20716d05b12ad10b`。

完成锁定提交为 `834ef57`；完成态 shadow config SHA-256 为
`a8e4e4e3fafcb638c292bbb042eaa88fc4900c163d32e654778417e880893547`。内容归档
`var/archives/ampgent-v34-provider-shadow-834ef57.zip` 的 SHA-256 为
`652f1801c09f9babb6cd7295e3f1df7960b023b766913ef1deb45af20508274c`。

## 19. v36 Harness Evolution 治理与 typed replay 底座（未授权）

长期路线的 harness evolving 已冻结为治理框架，精确合同为
`config/benchmarks/amp_harness_evolution_v36.yaml`，叙事说明为
`docs/ampgent-v36-harness-evolution.zh-CN.md`。状态为
`typed_schema_and_offline_verifier_implemented_not_deployed_not_authorized`：没有历史 replay、shadow
challenger、前瞻
champion/challenger、候选生成或 formal run 授权。

固定演化循环为：完整失败分母归纳 → 单一最小变化假设 → 只使用历史决策时点可见证据的
counterfactual replay → 不影响正式动作的 shadow → 同输入/seed/预算/停止规则的盲化前瞻对照 →
按作用域晋级、保留为专用策略、拒绝或追加式回滚。禁止读取 holdout 结果调阈值、active run 热切
policy、加权总分、单一 hypervolume 晋级或把软模型自洽当改进。

六类 typed 实体、迁移 `0010_harness_evolution_lineage`、retry-safe repository primitive 与
database+object-store-only offline replay verifier 已在仓库实现。prospective trial 使用独立 typed
`adjudication_run_id`，replay 会核对谱系、历史分区、配对、shadow 控制权、端点、盲化顺序、artifact
及 run/ToolCall/AgentDecision 归属。共享 PostgreSQL 尚未迁移，合成数据库 acceptance 尚未执行；下一
独立阶段只允许部署迁移和验证合成闭环，不得据此运行真实 replay/challenger 或生成短肽。任何 PepShot
缺陷仍直接退回任务
`019fb910-f2dd-7be1-a7e6-bfe381512c25`，AMPgent 禁止自行适配。

治理框架冻结 revision 为 `f7b58f9`；config SHA-256 为
`286bc3888f675ef5dc794e40aad8903ad173674dcaf554d1936e185962f2043e`；内容归档
`var/archives/ampgent-v36-governance-f7b58f9.zip` 的 SHA-256 为
`20b43305c7cc6586c977262638f3273da776eb3db4898926edb8e88bb2182c66`。

typed replay 实现已冻结并 push 到 commit `c185476a0db34bb2cf802aba89299a8593520abc`；当前 config
SHA-256 为 `8ce6fe07689851c354ecb01cc620f081d80c9ede03ee6a81e7e6a3964a0f2528`。内容归档
`var/archives/ampgent-v36-typed-replay-c185476.zip` 的 SHA-256 为
`a0e6f32464e37d44193a4fe2efd1cdc16a7dc6b19d5f70d643d12d1ce87d5c3c`。验证为 ruff clean、
pytest `334 passed`，且 migration `0009_candidate_occurrences:0010_harness_evolution_lineage` 的
PostgreSQL offline DDL 成功生成。全历史 offline DDL 会在旧 migration
`0002_tool_call_replay_input` 的 reflection 上停止，因此共享 PostgreSQL 部署仍须独立授权，并在真实
事务环境执行完整 migration、rollback 与合成 database/object-store replay acceptance。当前禁止把
repository 实现、合成 fixture 或迁移区间 DDL 解释为真实 harness 运行或改进。

v36 的下一独立阶段现已精确预注册为 v36a，合同为
`config/benchmarks/amp_harness_synthetic_acceptance_v36a.yaml`，授权说明为
`docs/ampgent-v36a-synthetic-acceptance-authorization.zh-CN.md`。它只允许部署迁移并写入两个相互隔离
的纯合成 scope，分别验证作用域晋级和祖先回滚的三阶段闭环；合计 0 Candidate、0 Evaluation，禁止
读取历史候选、激活真实 harness 或影响任何正式决策。通过也只表示数据库闭环可用，不证明 harness
改善。当前状态为 `preregistered_not_authorized`；只有用户明确回复
`授权 v36a 合成数据库闭环验收` 才可执行。父 v36 合同当前 SHA-256 为
`d3524f360ae68a3b3751397c76976d64890dafb316ef24d5050c0f0fb1795c98`。v36a typed preflight 实现
revision 为 `1905974f0a8f8818e7591cf3b38d70df5344c975`，v36a config SHA-256 为
`62a18e0f13f3bd248176ab91cf1300fd82c4da9770e40d8d4b5d07366a4a5387`，全量测试 `346 passed`。
revision 回填 checkpoint 为 `6299b233eef751004eec946f4ee2eab1edacdc1b`，内容归档 SHA-256 为
`b1a7e1f4c4a2ee40a4f2838461ac4ebaf61cabd0f2a7df92aeabe04f535a5e41`。该归档不是数据库验收结果。

## 20. 2026-08-12 GPU 资源边界与只读容量快照

用户已将资源许可更新为：`192.168.99.32` 全部资源及 `.19` GPU4 禁用，其他 GPU 可用于 AMPgent。
该许可解除此前对 synth GPU4 的项目级禁令，但不授权尚未获批的 formal/synthetic run，也不允许停止
或争抢现有任务。

只读快照显示：

- `.19` 有 8 张 RTX 3090；快照时 GPU4、GPU5 无计算进程且约有 24 GiB 空闲，但用户随后禁止 GPU4，
  因而该主机当前只有 GPU5 可作为潜在 AMPgent 空闲卡。现有 PepMLM worker
  PID `810968` 使用 GPU3，`PYTHONPATH=/data1/huangyueshan/pepagent/platform/current/src`，活动 release
  为 `339c4e48141830e3bad49ed3d6fb2a472d10ee57f11aefbd40771f9a19e52835`。
- synth 有 8 张 RTX 3090；快照时 GPU5、GPU6 无计算进程且约有 24 GiB 空闲，GPU4 有约 12.7 GiB
  已占用，不能仅因瞬时利用率为 0 而复用。Boltz2 worker PID `2914797` 固定
  `CUDA_VISIBLE_DEVICES=6`，Rosetta worker PID `2914804` 为 CPU worker；两者均从
  `/sdd_data/pepagent/platform/current/src` 加载，活动 release 为
  `034e558367d75e04f91684a6e4e3c91d3f5359cdb0455ecada41888a8bf35d6c`。
- Temporal poller identity 与上述 synth PID 已完成对应：Boltz2 `2914797@admin.cluster.local`，Rosetta
  `2914804@admin.cluster.local`。但当前 active workflow 为 0，且 v34 formal run 尚未获得精确授权，
  因此不得为了提高利用率而制造任务、重跑 v32 或提交 v34。

GPU 空闲状态是瞬时观察，任何启动前都必须重新执行 `nvidia-smi`、禁用卡检查、进程归属和 release/source revision
门禁。单卡多进程或新增 worker 必须在 formal run 之前冻结并记录，不得在 active run 中动态改变并发。

资源边界与只读容量审计冻结于 commit
`36a1c6d2cb3f93b92006372b33da7010cf99cfdf`；全量验证为 ruff clean、pytest `368 passed`。内容归档
`var/archives/ampgent-gpu-boundaries-36a1c6d.zip` 的 SHA-256 为
`70b51bde4c34468e78fb8830cad7a51b01bdc6628a55d294bb0b06091f9dde6b`。该 checkpoint 没有启动或停止
任何远端进程，没有提交 run，也没有访问 `192.168.99.32` 或 `.19` GPU4。

## 21. 2026-08-12 当前结果优先主线

用户当前优先级是尽快得到质量足够高、可直接查看的短肽结果，而不是证明知识卡或 PepShot 的工具增益。
因此当前主线分为：

1. 从已锁定 v32 PostgreSQL + 对象存储 portfolio 做只读候选组合导出，保持原 lane、顺序和限制，不重新
   选择、不回写；
2. 新建独立 v37 单臂 rapid champion generation 合同，使用当前最佳可用 Agent 流程，但不设置工具
   off/on arm、不声明工具有效；
3. v37 以 AMP/MIC、膜作用、毒性风险、AceA 结构/Rosetta 和序列多样性作多目标 portfolio，禁止加权
   总分；所有 proposal occurrence、ToolCall、Evaluation、AgentDecision、artifact 和重试必须落库并可
   database+object-store-only replay；
4. 在冻结 v37 config、实现、预算、seed、停止条件、worker/release 映射和防重复门禁后，才提交唯一
   v37 formal run。资源允许高并行，但 `.32` 与 `.19` GPU4 继续禁用，且不得干扰他人任务。

本次用户指令授权把当前研发主线切换到结果导向的 v37 设计与执行准备；它不解冻或回写 v32，也不授权
任何 v34 消融。

2026-08-12，v37 正式实现门禁完成并经独立对抗复审通过。执行实现闭环 revision 为
`fd263e8afc984960067fad94821d12a5b3effd73`，授权状态机 revision 为
`c4ef99ff3743408910a61cd4c0f0f5b6ef845fa2`；全仓 `ruff` clean、最新 `pytest 572 passed, 1 skipped`，其中唯一
跳过的 PostgreSQL 双会话 exact-once 测试已由主任务在真实本地 PostgreSQL 隔离 schema 中另行实跑为
`1 passed`。复审确认 21 个 canonical ToolCalls、57 条 dependencies、5 个 decisions、7 个 stop events，
并对重复 Boltz seed、删除或错绑 knowledge/PepShot/Rosetta 物理调用、ToolCall-event 漂移、自洽伪造
committed graph、runtime receipt 漂移、非 canonical Evaluation 和 provider/adapter 字节突变执行了 17 项
fail-closed 对抗测试。用户的结果优先持续推进指令现冻结为 v37 唯一正式执行授权；benchmark config 已置
`execution_authorized: true`、`submitted: false`，`implementation_revision` 冻结到执行闭环 revision；容量
合同仍保持不授权并仅作为只读资源合同。该状态仅允许在重新生成内容寻址 preflight、部署
migration、完成允许主机/GPU/PID/release 映射和全部动态门禁后执行一次 exact-once submit；尚未提交 run。

### 21.1 2026-08-12 v37 execution-gate checkpoint

- v37 remains the only authorized result-first formal run. It is still `submitted: false`; no
  replacement or duplicate run/workflow may be created.
- PostgreSQL was backed up before migration. Backup SHA-256 is
  `5A4691844CB577FF37C9600E84936330F88D209CA9A2DC79A1B418263236EA65`. The shared database is now
  at migration `0013_formal_submission_exact_once`; canonical hashes for all 15 pre-existing
  tables were identical before and after migration, all ten new evidence tables were empty, and
  the locked v32 database/object-store replay remained exact and read-only.
- Authorization checkpoint commit is `284993f`; its content archive SHA-256 is
  `98DD69D5755D701176ACC6BD047A62948C1AC4A9BFF5D9D3C5D9F6F5B0EEA37C`. Preflight and migration
  checkpoint commit is `528c1b9`.
- The physical synth mapping previously observed for Boltz2 PID `2914797` and Rosetta PID
  `2914804` must be revalidated immediately before submission. Poller identities alone do not
  satisfy the gate. Remote jump access most recently timed out before authentication; no remote
  command was run and no prohibited host or GPU was accessed.
- A deterministic actual-byte runtime descriptor freezer has been added for v37. It preserves
  provider metadata, inventories executable/source/model bytes, binds package locks, rejects
  symlinks/reparse points and encoding corruption, and self-validates through the generic launch
  guard. It never launches a provider or submits a workflow.
- Knowledge and PepShot tasks have received provider-owned delivery requests for immutable base
  descriptors and complete runtime snapshots. AMPgent must not invent these identities or add a
  compatibility layer. Metrics may freeze AMPgent-owned adapter environments only from exact live
  bytes plus committed manifests and package locks; AMP-READ still requires its committed runtime
  manifest.
- Latest repository validation after the runtime freezer: Ruff clean; pytest `583 passed, 1
  skipped`. Formal submission remains blocked until every runtime descriptor validates and the
  allowed worker host/PID/GPU/release mapping is current.
- Runtime freezer checkpoint commit is `86f9a99`; content archive
  `var/archives/ampgent-v37-runtime-identities-86f9a99.zip` has SHA-256
  `6EB4060A1B654EE99C16394BD3195E76DF9F50F8B0D51648631B4DC28B334BB2`.

### 21.2 2026-08-12 v37 immutable-capacity and exact-once checkpoint

- The frozen capacity contract is now a fifth immutable formal-submission input. Its exact bytes
  have SHA-256 `34f83c5a6df92a1d07779014c407211daefc80210581a840b7cea19cea46c3f0` and
  must be persisted to object storage, represented by a database Artifact, and included in the
  Temporal request. Missing or drifted capacity bytes fail closed.
- PostgreSQL formal-run reservation now takes a transaction-scoped advisory lock derived from the
  frozen benchmark ID and version before checking for an existing run. Retrying the same formal
  key recovers the original identity; a concurrent different manifest key for the same benchmark
  and version is rejected. No v37 formal run has been submitted by this checkpoint.
- The implementation is pushed at commit `f8937a9cfeee0f932bd0304b86ab91cc60065423`.
  Ruff is clean and the full suite is `586 passed, 1 skipped`; the skipped real-PostgreSQL
  two-session test was also run independently against the local PostgreSQL service and passed.
  Content archive `var/archives/ampgent-v37-capacity-exact-once-f8937a9.zip` has SHA-256
  `CF8C049C71E3C09F1F25AAC9EB652CDBCEB90CEEED5FDFAA1C15B728BA1AEC45`.
- Knowledge runtime release `amp-kb-runtime-base-c040cc601e4426093c72` failed read-only consumer
  acceptance: required top-level launch bindings were absent and its absolute paths contained
  mojibake rather than the real NFC workspace path. The defect was returned to knowledge provider
  task `019fad3e-76b8-7e32-8455-d2e9b31d33e5` for a new immutable release. AMPgent must not add a
  compatibility layer or accept the failed release.

### 21.3 2026-08-12 v37 provider/runtime and database-only replay closure

- Provider-owned Knowledge v3 and PepShot v2 releases are now accepted without an AMPgent
  compatibility layer. Formal activities consume each descriptor's frozen native invocation and
  launch prefix; candidate order, provider lineage, policy/release hashes, knowledge cards,
  passages and adoption edges are checked fail-closed before persistence.
- All five sequence-metric runtimes, including the deterministic physicochemical runtime, execute
  through byte-bound launch guards. External metric rows must preserve exact candidate order and
  all emitted values must be finite.
- The worker-placement snapshot is a sixth exact submission input. It must be fresh, report zero
  active workflows, bind Temporal poller identity and last access to physical host/PID/role/queue,
  source revision, release/environment/weight SHA, and use only the frozen eligible GPU topology.
- Pipeline dispatch/start/finish/backpressure evidence is recorded from real Temporal activity
  receipts. Database replay reconstructs generator/seed/raw-rank order and stage outcomes, then
  binds the worker snapshot, seven submission artifacts, preflight, formal key and workflow request
  to PostgreSQL evidence plus object-store bytes. No external manifest argument may repair replay.
- Runtime and replay hardening commits are `d694b2aae0c5dd931acc2d7d6caa4ba887122188`
  and `147249218554f22ac45a461f420dec8344dfdce7`. The frozen benchmark now points at the latter;
  revision-freeze commit is `da587f7`. Full validation is Ruff clean and `615 passed, 1 skipped`.
  Content archive `var/archives/ampgent-v37-replay-closure-812198b.zip` has SHA-256
  `1D24F434EEB072D24E5ED27AEA6B6CF1B97774265AFEA18F14F50993047DFB15`.
- No v37 formal run has yet been submitted. The only next action is dynamic service/duplicate/worker
  placement preflight, deployment of the exact frozen release to allowed resources, and one
  exact-once submission. `192.168.99.32` and `.19` GPU4 remain prohibited; unrelated processes must
  not be stopped or displaced.

### 21.4 2026-08-12 v37 executable generator release

- All three frozen generator runtimes have completed real local pre-formal smoke execution. HydrAMP
  and AMPGAN v2 each produced the requested two-row smoke output; AMP-Designer completed its native
  fixed 1,000-proposal smoke budget. The self-hashed smoke receipt is
  `config/environments/v37_generator_runtimes/runtime-smoke-acceptance.json`. This receipt admits the
  executable paths only; it is not Agent database evidence and makes no peptide-quality claim.
- HydrAMP now has separate immutable provider and consumer launch acceptances. Its archive is
  streamed and materialized under fixed member, file-size, total-size and compression-ratio limits;
  traversal, duplicate/case-fold collision, Windows device names, non-NFC names, links and reparse
  points are rejected. The materialized inventory/tree SHA and cleanup receipt are included in the
  canonical generator ToolCall evidence.
- AMPGAN v2 now carries the exact derived `data/dbaasp/clean.csv` runtime asset and semantic
  provenance that binds the source assets, provider derivation implementation and license footprint.
- Every generator ToolCall must have exactly one launch receipt tied to the immutable command,
  runtime manifest, source/model bytes and materialization evidence. The four HydrAMP acceptance and
  historical evidence files are persisted as original content-addressed objects, registered as
  PostgreSQL Artifacts and enumerated by database-only replay. Local files cannot repair missing
  evidence.
- Executable implementation revision is
  `c6b4405aa3944e877b8336b6fa532ea990df45f8`; revision-freeze commit is `74281fc`. Full validation is
  Ruff clean and `661 passed, 3 skipped`. Content archive
  `var/archives/ampgent-v37-generator-runtime-74281fc.zip` has SHA-256
  `E53ADBC051E7674D5AFDF56830ECC59F6FF7D59719CDBBD90F4EB7124E0DD5AE`.
- The immutable deployment archive has SHA-256
  `7d5edaed73df47b4e7735f539e72348f73d20e8b1db4f8440c2a681719f0e9d5` and embeds the exact executable
  implementation revision. The v37 formal run remains unsubmitted. A fresh read-only check found
  zero active workflows, but the visible Boltz/Rosetta pollers still belong to an older release,
  the metrics poller is a v32 identity, and v37 control/generator/provider pollers are absent.
  Therefore the only next action remains allowed-host deployment plus exact physical PID/GPU/release
  mapping, followed by the full preflight and one exact-once submission.

### 21.5 2026-08-12 v37 formal execution and path-recovery ledger

- The first `v37.0.0-preregistered` formal execution is preserved as an immutable failed run:
  PostgreSQL run `59c18f4a-f7f2-461d-a230-481a6fa35bb3`, Temporal workflow
  `pepagent-rapid-champion-v37-b853e3b4f2d042406733f7d8e0dada4fa6108328a5e141a48d26b6074b822268`,
  Temporal run `645d0b87-3a77-4498-b1cb-eacc96461a0e`. It failed before candidate persistence
  because the AMP-Designer subprocess received paths relative to a per-call working directory.
  Its exact database evidence is 6 lifecycle events, 1 succeeded Knowledge ToolCall, 2 linked
  artifacts, and zero candidates/evaluations/decisions. It must not be deleted, retried in place,
  repaired from local files, or described as a scientific generator failure.
- The execution-only correction resolves the request and output paths before constructing every
  generator subprocess command. Scientific inputs, generator budgets, seeds, selection rules,
  metrics, structure budget and portfolio policy are unchanged. The recovery benchmark version is
  `v37.0.1-path-recovery`; implementation commit is
  `46c4a1d3fccaf7714d85b9a6febff4885557582e`, freeze commit is
  `60b975b0e28ab532c44f043c87d3590eb6a69de3`, and immutable deployment archive SHA-256 is
  `1ec615a67e2e5433b7f604ecca5ca1468a66ba687c971f7c4a8b81d926257679`.
- The recovery placement was independently joined to current Temporal pollers: local control,
  generator, provider and metrics; synth GPU5/GPU6 Boltz2; `.19` GPU5 Boltz2; and synth CPU
  Rosetta. Every placement loaded source `46c4a1d...`, and the prohibited `.32` host and `.19`
  GPU4 were absent. Old `9d59a14...` AMPgent workers were terminated only after zero active
  workflows and exact PID/revision ownership checks.
- Fresh recovery preflight passed all seven gates with submission-preflight SHA-256
  `718efc9dd5a901f9088595dca7055e1aa30a81575c68a49120028c4221e687a1`. The unique recovery formal
  run is PostgreSQL run `c7291e95-2d45-4dca-bc94-c1a551ba0ddd`, workflow
  `pepagent-rapid-champion-v37-9b85a88b1fce3d7cb21a7cb9797fd3be22d448aa25895d4edcf883834d312e99`,
  Temporal run `f6ceaa72-9589-4f38-bb1f-f167023c53a8`, formal key
  `9b85a88b1fce3d7cb21a7cb9797fd3be22d448aa25895d4edcf883834d312e99`, and manifest SHA-256
  `6b23c0afabb0451622e13beda53827f1bf7faf0d75f2b957b9db37e6fb09926f`.
  It is the only permitted recovery run and must never be submitted again.
- Current state is `failed_immutable`; section 21.6 records the exact cause and the only permitted
  versioned persistence recovery. No generated peptide result from this run may be claimed.
- The recovery execution ledger and revision-contract alignment are pushed at checkpoint
  `a2341ef`. Validation is Ruff clean and `661 passed, 3 skipped`; content archive
  `var/archives/ampgent-v37-path-recovery-a2341ef.zip` has SHA-256
  `8310978d8ad5bee89056630002eea65ebcc9d1b59aadc6ef75973e9a1036d0e2`.

### 21.6 2026-08-12 v37 persistence-recovery ledger

- The `v37.0.1-path-recovery` run is now an immutable failed run. All nine frozen generator-seed
  activities completed generation, but the first canonical generation persistence activity failed
  before candidate commit with `v37 artifact roles differ from replay contract`. PostgreSQL remains
  at zero candidates and zero evaluations for this run. The run and its local generator outputs
  must not be retried in place, backfilled, or treated as scientific results.
- Exact reconstruction from Temporal event 65 and the durable PostgreSQL attempt ledger showed that
  the actual six roles and frozen six-role contract were identical. The defect was a deterministic
  validator error: `Counter` was constructed from the artifact payload mapping itself, so payload
  dictionaries were interpreted as counts. It was not a generator, metric, database, Temporal, or
  scientific-quality failure.
- The minimal fix validates `Counter(artifact_payloads_by_role.keys())` and adds positive,
  missing-role, and unexpected-role regression coverage. The implementation revision is
  `723823b5e64b37233fc2f41b8803b596c5039111`. Full validation is Ruff clean and
  `664 passed, 3 skipped`.
- A new exact-science recovery contract is frozen as `v37.0.2-persistence-recovery`. Generator
  models, all nine seeds, fixed budgets, sequence metrics, Pareto rules, knowledge/PepShot releases,
  Boltz seeds, Rosetta decoys, stop conditions, and scientific boundaries are unchanged. It is
  authorized but not yet submitted. Before one exact-once submission, deploy a content-addressed
  release containing the fix, revalidate all services and duplicate gates, and map every worker to
  physical host/PID/role/queue/source revision. `192.168.99.32` and `.19` GPU4 remain prohibited.

### 21.7 2026-08-12 v37.0.2 formal execution ledger

- The only `v37.0.2-persistence-recovery` formal run was submitted exactly once after all static,
  dynamic, duplicate-run and worker-placement gates passed. PostgreSQL run ID is
  `4beae6b1-3dee-4f49-941c-600e7b85a627`; Temporal workflow ID is
  `pepagent-rapid-champion-v37-0e9464703e969de4c98b126a7a51cbfc634768f63401f21be4c76532d1b82b8e`;
  Temporal run ID is `a1d2f66b-9930-4c43-8704-bce598372044`. Formal submission key is
  `0e9464703e969de4c98b126a7a51cbfc634768f63401f21be4c76532d1b82b8e`, submitted manifest
  SHA-256 is `7806a3bdd9e5b7a1e7b5e36f6b466682868d050076a2be01236c5c44afcdc6f9`, and fresh
  submission-preflight SHA-256 is
  `513f9f8e75e9f1a38fdf1a8e596b0dd11caee3f529833ef9514d111ab13ca79f`.
- The content-addressed worker release is
  `df5a018be13a82848e91de3b3119a9b485626550e9ff43658f39cc874e20614a`, loading source revision
  `723823b5e64b37233fc2f41b8803b596c5039111`. The frozen placement contains local control,
  generator, provider and metrics workers; synth GPU5/GPU6 Boltz2; `.19` GPU5 Boltz2; and synth
  CPU Rosetta. Independent physical inspection found no foreign process on the three allowed GPUs.
  Neither `192.168.99.32` nor `.19` GPU4 is present.
- Current state is `running`; the knowledge ToolCall has succeeded and all nine fixed generator-seed
  budgets are being collected before canonical ordered persistence. This run must never be submitted
  again. Monitor PostgreSQL evidence counts and Temporal failures; do not backfill from local outputs
  or create a replacement run. Append completion, failure or replay facts here only after they are
  observed from PostgreSQL, Temporal and content-addressed object storage.
- Submission-ledger checkpoint commit is `62ca8cf`; content archive
  `var/archives/ampgent-v37-persistence-run-62ca8cf.zip` has SHA-256
  `db9c1ddd4df52c07e4167676b440f696b0719aaf875a765899965286a9fc7624`. Ruff is clean and the
  submission-contract subset is `40 passed`. The full suite was started during two concurrent
  AMP-Designer generation activities but exceeded the 120-second local check window; the exact
  deployed implementation had already passed the complete `664 passed, 3 skipped` suite before
  submission. This timeout is resource contention, not a test assertion failure.

### 21.8 2026-08-12 v37.0.2 infrastructure interruption and immutable failure

- The only `v37.0.2-persistence-recovery` run is now `failed_immutable`; its PostgreSQL run,
  Temporal workflow/run and formal submission key in section 21.7 must never be submitted again,
  reset, backfilled, deleted or described as completed. At failure it contained exactly 800
  candidates, 8,000 proposal occurrences, 17 ToolCalls, 74 evidence artifacts and zero
  evaluations or Agent decisions. These rows remain append-only partial-failure evidence and are
  not a peptide-result cohort.
- All nine generator-seed subprocesses produced their fixed 1,000-row raw outputs. Docker
  Desktop/WSL2 then deadlocked while the final AMP-Designer activity was committing
  content-addressed evidence to MinIO. The scoped recovery restarted only Docker Desktop and its
  sole registered `docker-desktop` WSL distribution, preserved named volumes, local workers and SSH
  tunnels, and did not access any prohibited host or GPU. The same workflow resumed without a
  replacement submission or local-file backfill.
- PostgreSQL proves that `v37:generate:amp_designer:20270379` attempt 1 recorded `started` and its
  launch receipt but no terminal event because Temporal observed a five-minute heartbeat timeout.
  Temporal then started attempt 2, which recorded launch and aggregate receipts and succeeded. The
  old ledger projector ignored the interrupted attempt and saw terminal attempts `[2]`, so the
  ninth `persist_v37_generation_batch` failed non-retryably with
  `v37 durable attempt ledger is not contiguous`; the workflow and run were then marked failed.
  This is an execution-evidence semantic defect, not a generator-quality result.
- The only permitted next execution is a separately versioned
  `v37.0.3-interrupted-attempt-recovery` with identical scientific models, seeds, budgets,
  metrics, structure protocol and Pareto policy. Its execution contract must persist a typed
  `v37.attempt_interrupted` event when a later Temporal attempt supersedes a started attempt that
  lacks a terminal event; the replay ledger must distinguish `interrupted` from real `failed`,
  require contiguous attempts, fence late zombie terminals and require only the final attempt to
  succeed. It must be frozen, tested, committed, archived, deployed as a new content-addressed
  release and pass every duplicate/service/worker-placement gate before one exact-once submission.
  No v37.0.2 database rows or local outputs may seed or repair the new run.
- Resource prohibition is fail-closed: `192.168.99.32` is entirely off limits, explicitly including
  GPU3 and GPU4, and the host must not be contacted even to use another card. `.19` GPU4 also
  remains prohibited. Allowed resources still require exact host/GPU/PID/role/release ownership and
  must not displace unrelated work.

### 21.9 2026-08-12 v37.0.3 interrupted-attempt recovery freeze

- The execution-only recovery is frozen as `v37.0.3-interrupted-attempt-recovery`. The three
  generators, nine seeds, raw and selected budgets, five sequence-evaluation families, 48-candidate
  structure budget, three Boltz seeds, 16 Rosetta decoys per pose, knowledge/PepShot releases,
  Pareto policy and all scientific interpretation boundaries are byte-for-byte or semantically
  unchanged except for the required version binding.
- The implementation persists the later attempt start and all inferred prior interruptions in one
  PostgreSQL transaction protected by a lineage-scoped advisory lock. Terminal writes take the
  same lock, so a superseded zombie attempt cannot race a typed interruption into a contradictory
  ledger. Replay validates event type against payload status and identity, requires every
  interruption to name an actual later-attempt start, rejects duplicate interruption rows, requires
  contiguous attempt numbers and accepts only a final succeeded attempt.
- The interrupted-attempt implementation checkpoint is `247f75102d396b846ec4a326711b361851eac49a`; the deployable
  implementation revision is `a7a0e671fb0234f9365bb083ce40c761cc2d0ccb`, the first immutable
  revision that contains both that recovery logic and the v37.0.3 manifest-version schema. Deploying
  `247f751...` would fail closed on the new version before execution and is therefore prohibited. Targeted recovery
  validation is `66 passed`, and repository validation before the version freeze is Ruff clean with
  `668 passed, 3 skipped`. The v37.0.2 partial database evidence remains immutable and is not reused.
- v37.0.3 is authorized only as the single exact-science recovery described above and remains
  unsubmitted. Freeze commit is `a7a0e671fb0234f9365bb083ce40c761cc2d0ccb`; benchmark SHA-256 is
  `bd5194fd57d0249d080c2f1a2fb7b3e5508b38c842239d14dbccba4ac831fa8a`, and experiment-spec
  SHA-256 is `be9f96f4e75cb13fde345713f657c2ce564c473eb70ebbcc684814bc791219f5`.
  Content archive `var/archives/ampgent-v37-interrupted-attempt-recovery-a7a0e67.zip` has SHA-256
  `9835e99c67563e43433519074e38218e5b03ff8d1dc676dfefce0e0f767f0435`. Before submission it still
  requires a new content-addressed deployment release, zero duplicate run/workflow, healthy services, and fresh
  physical host/GPU/PID/role/queue/release mapping for every worker.
- `192.168.99.32` remains a whole-host prohibition explicitly including GPU3 and GPU4; no login or
  probe is allowed. `.19` GPU4 also remains prohibited. These constraints are formal preflight
  failures, not scheduling preferences.

### 21.10 2026-08-12 v37.0.3 local worker identity recovery

- The Windows worker manager now distinguishes Temporal's retained historical poller records from
  live local workers by parsing the exact local PID identity and checking that PID without weakening
  unknown-host or remote fail-closed behavior. It also binds the actual base CPython executable,
  frozen dependency import paths and executable SHA instead of assuming that the virtual-environment
  launcher wrapper is the long-lived worker process.
- Windows process snapshots explicitly force UTF-8 output and strict UTF-8 decoding. This closes the
  manager crash caused by the NFC Chinese workspace path being decoded with the host legacy code
  page. The four processes from the incomplete launch were stopped only after zero active workflows,
  exact command-line ownership and exact `a7a0e671...` Temporal identities were verified.
- Fresh local receipts now bind control PID `2388`, generator PID `32192`, provider PID `34076` and
  metrics PID `84708` on `StevensOMEN9` to source
  `a7a0e671fb0234f9365bb083ce40c761cc2d0ccb` and release
  `f9b3e30a6547e9254fc5e51d20e9eaceaf88200c63a7a6d2e45ab95a41197e92` under
  `var/workers/v37-003-live2`. These PIDs are dated observations, not permanent configuration, and
  must be revalidated for final placement.
- Repository validation after the manager correction is Ruff clean and `670 passed, 4 skipped`.
  Manager checkpoint commit is `db4caacdefb754d38566c33c4f96530bbd48584f`; content archive
  `var/archives/ampgent-v37-local-worker-db4caac.zip` has SHA-256
  `21206d872b22ecc610e8e5db8437c6b26df93587893ec451e3aa81709d4e454b`.
  v37.0.3 remains unsubmitted. The next action is deployment and exact inspection of the allowed
  synth GPU5/GPU6, `.19` GPU5 and synth CPU workers, followed by fresh placement, all seven dynamic
  gates and one exact-once submission. `192.168.99.32` remains wholly prohibited, explicitly
  including GPU3/GPU4, and `.19` GPU4 remains prohibited.

### 21.11 2026-08-12 v37.0.3 immutable provider-runtime failure

- The unique `v37.0.3-interrupted-attempt-recovery` formal run is immutable failed. PostgreSQL run
  ID is `97af297c-f0b5-4ba2-859e-56ab811562d3`; Temporal workflow ID is
  `pepagent-rapid-champion-v37-5e5c943009dac4e3b66078c99bd03e25585569325a3e24d86e666b33ab0f4c55`;
  Temporal run ID is `2ae087b3-9515-4571-9c41-b4673c172882`; formal key is
  `5e5c943009dac4e3b66078c99bd03e25585569325a3e24d86e666b33ab0f4c55`.
- It failed before candidate generation. PostgreSQL contains exactly 0 Candidate, 0 proposal
  occurrence, 0 ToolCall, 0 Evaluation, 0 AgentDecision, 0 dependency, 0 decision edge and 0
  evidence-artifact edge for the run. Both knowledge attempts recorded the same observable error,
  `AssertionError: SRE module mismatch`. Their typed attempt events are complete and their two
  launch-receipt artifacts match MinIO bytes and SHA, but those receipts are engineering audit
  evidence only and cannot seed or repair another run.
- The confirmed mechanism is cross-interpreter environment pollution: the local worker exported
  its frozen Python 3.11 standard-library paths through `PYTHONPATH`, and the independent knowledge
  provider Python 3.12 inherited them before importing `argparse`/`re`. This is an execution-runtime
  isolation failure, not a peptide, knowledge-card or scientific-quality result. The run and every
  output associated with it must not be resubmitted, backfilled, deleted or reused.

### 21.12 2026-08-12 v37.0.4 subprocess-environment recovery freeze

- The only permitted next version is `v37.0.4-subprocess-environment-recovery`. It changes only
  subprocess environment isolation. Generator models, all nine seeds, raw/selected budgets, five
  sequence metric families, 48-candidate structure shortlist, three Boltz seeds, 16 Rosetta
  decoys per pose, provider releases, Pareto policy and stop conditions remain unchanged.
- Provider and metric subprocesses now drop the parent worker's `PYTHONPATH`, `PYTHONHOME`,
  `PYTHONSTARTUP`, `PYTHONUSERBASE`, `VIRTUAL_ENV` and `__PYVENV_LAUNCHER__`; declared adapters may
  not override those keys. Generator subprocesses receive only their frozen generator source root,
  never the worker bootstrap path. The source revision enforces policy
  `isolated_provider_python_no_worker_bootstrap`.
- Implementation revision `22f564e` includes the environment fix and v37.0.4 manifest binding.
  A real smoke deliberately polluted the parent environment with the frozen Python 3.11 library,
  then launched the frozen knowledge Python 3.12 with the isolated environment and successfully
  imported `argparse` and `re`. Repository validation is Ruff clean with `675 passed, 4 skipped`.
- v37.0.4 is authorized but unsubmitted. Before one exact-once submission it requires a new
  content-addressed archive/release, new worker deployment and physical placement, a real provider
  smoke under that release, healthy API/PostgreSQL/MinIO/Temporal, zero duplicate run/workflow and
  all seven current gates. No v37.0.3 preflight, bundle, database row, artifact or local output may
  be reused.
- `192.168.99.32` remains a whole-host prohibition, explicitly including GPU3/GPU4, and must not be
  contacted even for inspection. `.19` GPU4 also remains prohibited. Only owned, revision-mapped
  allowed resources may be used without disturbing unrelated work.
- The v37.0.4 freeze checkpoint is commit `7740b13`. Benchmark SHA-256 is
  `af934bb5fa9a5ea7b7c47774641dea190b89fcbf3fd1215fc62ac13a619cc249`; structure-spec SHA-256 is
  `b8c89fd5d4f255e985fc61f706b1e6ca5c0b5cf1ecc16c123180fa16df63c149`. Content archive
  `var/archives/ampgent-v37-subprocess-recovery-7740b13.zip` has SHA-256
  `18cee29c0756363babfadeca70f444faad8705081ca28663edc4d846a37bca13`.
- The new deployable archive and extracted release are content-addressed as
  `e1f1d0a3e7211a83cc1fdd62e2989ba2511844f9eb8ed791b85caf87c130a3dd`, with source marker
  `22f564e0fdde67aed97779d9185dbe929661c882`. Archive bytes, release marker and source marker were
  independently rechecked. Loading AMPgent from the extracted release succeeded, and the real
  frozen knowledge provider Python again imported `argparse`/`re` successfully while the parent
  environment was deliberately polluted with the frozen Python 3.11 standard library. This proves
  the deployable artifact contains the isolation fix; it does not by itself authorize submission.

### 21.13 2026-08-12 v37.0.4 worker migration checkpoint

- Local control, generator, provider and sequence-metric workers were migrated to source
  `22f564e0fdde67aed97779d9185dbe929661c882` and release
  `e1f1d0a3e7211a83cc1fdd62e2989ba2511844f9eb8ed791b85caf87c130a3dd`. New pollers were observed
  before the four exact old PIDs were stopped. Current local PIDs are control `24644`, generator
  `35996`, provider `85900` and metrics `97464`; all four immutable receipts pass the local worker
  inspector. Their v37.0.4-only bootstrap identities are launch material, not submission authority;
  the final placement snapshot and submission preflight remain outstanding.
- Remote migration stopped at the non-interference gate without changing remote state. On synth,
  GPU5 is occupied by foreign PID `4076870` using about 15.9 GiB and GPU6 by foreign PID `4076871`
  using about 17.3 GiB; both originate from another user's `ecd_pred` environment. They are not
  AMPgent-owned and must not be stopped, inspected beyond the ownership gate, or competed with.
  Consequently no v37.0.4 archive was uploaded or activated remotely and no remote worker was
  started or stopped. The existing `.19` GPU5 Boltz worker and synth CPU Rosetta worker remain on
  the old source/release pending a complete safe migration.
- Temporal had zero active workflows during the local migration. No v37.0.4 formal run has been
  submitted. The next permitted action is read-only occupancy monitoring; after synth GPU5/GPU6
  become available, repeat the full remote ownership/release gate, migrate the complete remote set,
  build the final placement/preflight, and only then perform the single exact-once submit.
- The whole-host prohibition on `192.168.99.32`, including GPU3/GPU4, and the `.19` GPU4 prohibition
  remain absolute and were not contacted or used during this checkpoint.
- This migration checkpoint was committed and pushed as `3e11066`. Repository validation was Ruff
  clean with `675 passed, 4 skipped`; content archive
  `var/archives/ampgent-v37-worker-migration-3e11066.zip` has SHA-256
  `c23158f41027db94bff7f8971aa1aa9ba55f5cd33dc79d2aaa018be849963370`.

### 21.14 2026-08-13 大数据归属与本地存储规则

- 用户要求本地工作站尽量不保存大文件，并授权 `192.168.99.19` 保存 AMPgent 大文件。仓库级规则已写入
  `AGENTS.md`，独立位置账本为 `docs/ampgent-large-data-location-ledger.zh-CN.md`。
- 账本区分权威证据与执行副本：formal Agent 流仍以 PostgreSQL typed evidence graph 加内容寻址对象
  存储为权威；`.19` 可存模型、独立运行时、结构中间产物和运行缓存，但每个具体对象必须登记精确
  路径、owner、来源 run/release、SHA/manifest、canonical/cache 角色和生命周期。
- 该授权只扩大大文件存储位置，不扩大计算或科学权限。`.19` GPU4 仍绝对禁止，`192.168.99.32`
  仍为整机禁止访问/探测/使用；不得访问他人目录、停止他人进程或据此提交未经批准的 formal run。
- 既有本地 `var/`、`runtime/`、`output/`、`outputs/` 与用户产物保持原状；未完成逐项所有权、大小、
  SHA 和 replay 核验前不做批量迁移或删除。后续新大文件默认不在本地无登记累积。
- 规则实现 checkpoint 为 commit `0bb6112`。Ruff clean，pytest `676 passed, 4 skipped`。紧凑治理归档
  `var/archives/ampgent-large-data-policy-0bb6112.zip` 为 52,426 bytes，SHA-256 为
  `07cf6c903e4c51e403129d889d7a001b7678d176a1395efe7abaf0458c32cb65`；该小型归档只包含本次 5 个
  已跟踪规则/契约文件，不包含模型、数据库、运行结果或用户产物。

### 21.15 2026-08-13 持续工程环境与瓶颈评估规则

- 用户要求 agent 不等待人工追问，而是在活跃研发、恢复和正式执行期间定期做只读工程环境核查，判断
  当前任务不能 scale 或没有产生 durable progress 的真实原因。该规则已写入 `AGENTS.md`。
- 固定诊断顺序为：API/PostgreSQL/对象存储/Temporal 控制面 → formal/duplicate 门禁 → evidence persistence
  与 replay → worker 物理主机/PID/角色/release → GPU → CPU/Rosetta → 存储与网络 I/O → pipeline
  barrier/backpressure → Agent 分析/决策延迟。前级尚未允许 dispatch 时，不得把瓶颈误报为后级算力。
- 证据至少包括当前 health、active workflow、数据库证据计数增量、queue/poller last access、精确资源归属、
  worker release receipt 和阶段吞吐。仅凭容器 `Up`、历史 poller、瞬时 GPU idle 或墙钟时间不能定性。
- 扩 worker、进程或并行 agent 前必须证明关键路径可利用新增并发、冻结协议允许、资源不属于他人且不会
  破坏数据库/对象存储的顺序和 replay。无 active workflow 不自动表示“可运行”或“健康”。
- 当前 2026-08-13 只读快照用于说明该方法：PostgreSQL/MinIO 健康，Temporal 经实际容器地址可查询且
  只有历史 failed v37 workflows；本地 API 未监听；v37.0.4 无 formal run。synth GPU5/GPU6 的最新可信
  placement 快照仍显示外部 `ecd_pred` 占用，`.19` GPU5 worker 尚未迁移至 v37.0.4。因此当前关键路径是
  控制面恢复和允许 worker/release 对齐，之后才是 Boltz GPU 容量；该快照是日期化观察，不是永久配置。
- 规则 checkpoint 为 commit `26480cd`；Ruff clean，pytest `677 passed, 4 skipped`。紧凑内容归档
  `var/archives/ampgent-bottleneck-assessment-26480cd.zip` 为 52,097 bytes，SHA-256 为
  `6fd61fa1a724295eadbcd9cc792843438368a0bf38f5d1504ffa8e290323dc38`。

#### 21.15.1 API 端口勘误与关键路径修正

- 上述“本地 API 未监听”来自错误检查 `127.0.0.1:8000`，不是服务故障。仓库 runbook 的权威 API
  地址为 `127.0.0.1:8080`；2026-08-13 复核 `/healthz` 返回 `{"status":"ok"}`，监听 PID 为
  `18168`。旧观察保留用于说明错误，不能继续作为瓶颈证据。
- 同次只读复核确认 PostgreSQL/MinIO 健康，Temporal 可从实际容器地址查询且没有 active v37 workflow；
  v37.0.2/v37.0.3 的不可变失败计数无漂移。因此当前关键路径不是 API、CPU 或 Agent 思考，而是允许的
  remote worker 统一迁移到 v37.0.4 release、精确 placement/preflight，以及 synth GPU5/GPU6 外部占用。
- 后续 health 检查必须从 `docs/runbook.md` 或冻结 service config 解析端口，不能凭记忆硬编码；连接失败前
  先验证目标地址，避免把错误探针解释为服务故障。

### 21.16 2026-08-13 `.19` GPU5 v37.0.4 worker 迁移

- 周期性瓶颈检查确认 `.19` GPU5 有 24,110 MiB 空闲、利用率 0 且无计算进程；旧 PID `162983`
  可由 immutable receipt 精确识别为 AMPgent-owned v37.0.3 Boltz worker。检查未访问 `.19` GPU4 或
  `192.168.99.32`。
- v37.0.4 内容寻址 archive 从本地已验证副本上传到
  `/data1/huangyueshan/pepagent/bootstrap/platform-e1f1d0a3e7211a83cc1fdd62e2989ba2511844f9eb8ed791b85caf87c130a3dd.tar.gz`；
  远端 SHA-256 为 `e1f1d0a3e7211a83cc1fdd62e2989ba2511844f9eb8ed791b85caf87c130a3dd`，大小
  1,114,245 bytes。位置与生命周期已登记到 `docs/ampgent-large-data-location-ledger.zh-CN.md`。
- 新 `.19` GPU5 Boltz worker PID `269615` 已从 immutable release `e1f1d0a3e7211a83cc1fdd62e2989ba2511844f9eb8ed791b85caf87c130a3dd`
  启动，source revision 为 `22f564e0fdde67aed97779d9185dbe929661c882`，environment SHA-256 为
  `5800a86d19e219bd5c6ddddf58250706c2d7120d9161089b959eecfffce68296`，Boltz weights SHA-256 为
  `090e82ac8c92f5e943fa1b39e7410a44027bea7243c0bbb3caa67a77fc1428e1`。物理检查无 foreign process，
  Temporal 已观察到精确新 poller。随后仅停止了已核验的旧 `.19` PID `162983`。
- 同次 synth 只读快照显示 GPU5/GPU6 仍由外部 OmniEpic Python PID `1762511`/`1762519` 占用，
  各约使用 11,566 MiB。没有停止、探测其内部任务或争抢资源；synth 旧 worker/release 未迁移。
- API、PostgreSQL、MinIO、Temporal 健康且无 active v37 workflow；v37.0.4 formal run 仍未提交。
  当前剩余关键路径收敛为 synth GPU5/GPU6 释放后的完整 remote worker/release 对齐、最终 placement
  snapshot 和全部 submission preflight。
- 迁移 checkpoint 为 commit `becdf71`；Ruff clean，pytest `678 passed, 4 skipped`。紧凑内容归档
  `var/archives/ampgent-v37-host19-migration-becdf71.zip` 为 51,348 bytes，SHA-256 为
  `b314a4ebc80b9ba15dae1338569fb974b9aed0ffcad4155df1bdf67f821034d5`。

### 21.17 2026-08-13 GPU 边界勘误与跨任务协调

- 用户最新指令纠正了资源边界：`.19 GPU4` 没有被禁止；`.32 GPU2/GPU3` 才是双方共同的绝对禁区。
  旧章节中关于“.19 GPU4 禁止”或“.32 整机禁止”的文字保留为历史事实，但自本节起不再是当前执行规则。
- 已与 Codex 任务 `019fcd9b-a14e-7741-a3ff-2fd0e1d3d4c7` 完成只读资源协调。对方确认 `.19 GPU4`
  当前空闲并可立即划给 AMPgent 独占，`.19 GPU5` 的 AMPgent worker PID `269615` 继续保留，OmniEpic
  不会调度到这两张卡。
- `.32 GPU0/GPU1` 近期仍由 OmniEpic 的正式训练/采样链预留，不分配给 AMPgent；`.32 GPU2/GPU3`
  双方均不得访问、探测或使用。synth GPU5/GPU6/GPU7 与 `.19 GPU1/GPU2/GPU3/GPU6/GPU7` 均有
  对方或其他任务，不能视为 AMPgent 可独占容量，且不得停止或抢占。
- v37 容量合同升级为 `v37-capacity-v2`：eligible Boltz placement 为 `.19 GPU4`、`.19 GPU5`、
  synth GPU5、synth GPU6，最大 worker/activity 并发为 4。synth 两张卡仍须等外部任务释放并重新通过
  精确门禁；本次只立即部署 `.19 GPU4`。科学候选数、seed、评价、结构预算、Rosetta decoy 和 Pareto
  判定不变；变化只涉及已预注册运行前的资源拓扑与派发容量。
- 新 worker 必须来自包含本次资源合同的内容寻址 release，并重新记录物理主机、GPU、PID、role、queue、
  source revision、release、environment、weights 和 foreign-process 门禁。该变更不允许重复提交 formal run。

### 21.18 2026-08-13 `.19 GPU4/GPU5` capacity-v2 部署

- 资源合同修订 commit `ace90cd0e383c079caff7735bd7e664f2ca31c70` 已 push。容量合同 SHA-256 为
  `bdc8e3cb294d92009509efbb6a859475d49bb5bd3a702e73b374a0f97c2fef19`；Ruff clean，pytest
  `677 passed, 4 skipped`。紧凑内容归档 `var/archives/ampgent-v37-capacity-ace90cd.zip` 为 120,223 bytes，
  SHA-256 为 `35c2ab9fae54a7bdd52723000bf2b9d2a2d02a087faae9f08fc010bb1f9cec96`。
- 由该提交构建并验证的 immutable platform archive 为 1,123,713 bytes，SHA-256 为
  `926a1c9cc9c1c52ffd12404190b3397bd0b2649dee941cc5a0cb8ff142cc8eba`。远端副本位于
  `/data1/huangyueshan/pepagent/bootstrap/platform-926a1c9cc9c1c52ffd12404190b3397bd0b2649dee941cc5a0cb8ff142cc8eba.tar.gz`，
  激活 release 与 source revision 的绑定验证通过；本地临时 release archive 已删除。
- `.19 GPU4` 在启动前再次确认 24,110 MiB 空闲、利用率 0、无计算进程。新 Boltz worker PID `288726`
  已启动。`.19 GPU5` 的旧 worker PID `269615` 经旧 immutable receipt 验证为 AMPgent-owned、无 foreign
  process，且 Temporal 无 active workflow 后才正常停止；新 GPU5 worker PID 为 `289268`。
- GPU4/GPU5 两个 worker 当前均绑定 physical host `192.168.99.19`、source
  `ace90cd0e383c079caff7735bd7e664f2ca31c70`、release
  `926a1c9cc9c1c52ffd12404190b3397bd0b2649dee941cc5a0cb8ff142cc8eba`、environment SHA-256
  `7386ade33154f183492bf438260c683458161beddd07ebf3a6e0aa983c48dbeb` 和 weights SHA-256
  `090e82ac8c92f5e943fa1b39e7410a44027bea7243c0bbb3caa67a77fc1428e1`；物理检查均无 foreign process，
  Temporal 已观察到两个精确新 poller。
- `.32 GPU0/GPU1` 仍由协调任务预留，`.32 GPU2/GPU3` 仍为绝对禁区；synth 资源仍有外部任务，未触碰。
  当前安全可用的 AMPgent Boltz 容量为 `.19 GPU4/GPU5` 两张卡。v37.0.4 formal run 仍未提交；提交前还需
  将其余必需 worker 迁移到同一 source/release、等待历史 poller 变旧并重新生成完整 placement/preflight。

### 21.19 2026-08-13 submission GPU gate 勘误与最终 `.19` release

- 21.18 部署后复核发现 benchmark 的 `pre_execution_gates` 仍含旧的“.32 整机与 .19 GPU4 禁用”字符串。
  该文字与用户最新资源边界及 capacity-v2 相冲突，因此在 formal submit 前 fail closed；没有提交 workflow，
  也没有产生候选或科学证据。
- 最小勘误 commit `8bdeb39fcc0df7c635e13a4aefa56a6c6a2bb4e3` 已 push：门禁现为
  `no_worker_uses_192_168_99_32_GPU2_or_GPU3_or_uncoordinated_shared_resources`。它不改变候选、seed、
  评价、结构预算、Rosetta 或 Pareto 规则。相关 38 项 v37 preregistration/submission 测试通过。
- 勘误内容归档 `var/archives/ampgent-v37-gpu-gate-8bdeb39.zip` 为 6,600 bytes，SHA-256 为
  `68cbf211d90861791326c3d2dd3d331f5ef261e6913bc80e599b38945fdccb24`。新的 immutable platform archive
  为 1,124,763 bytes，SHA-256 `cda153111e3e4f6bbb01720f0587e899b178cf9ec2626cdae65bcaf17b3146f3`，
  已验证上传并激活；本地临时 release archive 已删除。
- 在 Temporal active workflow 数为 0 且旧 GPU4/GPU5 worker 的 immutable receipt、PID、release 和
  `foreign_process_present=false` 均复核后，仅停止了 AMPgent-owned PID `288726`/`289268`。新 `.19 GPU4`
  worker PID 为 `290062`，新 `.19 GPU5` worker PID 为 `290212`；两者均绑定 source
  `8bdeb39fcc0df7c635e13a4aefa56a6c6a2bb4e3`、release
  `cda153111e3e4f6bbb01720f0587e899b178cf9ec2626cdae65bcaf17b3146f3`、environment SHA-256
  `bd8f9aca3cafda51bfb1b8e48e9204fccd183da5436f2ac3fa0fae9775160946` 和既有 Boltz weights SHA-256
  `090e82ac8c92f5e943fa1b39e7410a44027bea7243c0bbb3caa67a77fc1428e1`；两个物理检查均无 foreign
  process，Temporal 已看到两个精确新 poller。
- `.32 GPU0/GPU1` 仍未分配，GPU2/GPU3 仍为双方绝对禁区；其他共享卡未触碰。formal run 仍未提交。
  当前剩余门禁是本地 control/generator/provider/metrics 与必需 Rosetta worker 的同 release 对齐，以及
  排除仍在同一 Boltz queue 上的历史/共享 poller 后生成完整 placement 与 exact-once preflight。

### 21.20 2026-08-13 implementation/worker revision identity correction

- Read-only gate audit found that v37 had conflated the frozen scientific implementation revision
  with the independently deployable worker source revision. This was an engineering identity
  blocker: valid workers on the corrected GPU-gate release would be rejected even though the
  candidate, seed, metric, structure, Rosetta and Pareto contracts were unchanged.
- The benchmark now freezes two distinct identities. `formal_run.implementation_revision` remains
  `22f564e0fdde67aed97779d9185dbe929661c882`; `execution.worker_source_revision` is
  `8bdeb39fcc0df7c635e13a4aefa56a6c6a2bb4e3`, matching the current immutable `.19 GPU4/GPU5`
  worker release `cda153111e3e4f6bbb01720f0587e899b178cf9ec2626cdae65bcaf17b3146f3`.
- Static preflight schema `1.3`, live preflight, exact-once submission, Windows worker planning and
  database/object-store replay now independently enforce the frozen worker revision. Replay also
  rechecks the exact frozen task-queue mapping; a syntactically valid but wrong revision or queue
  fails closed.
- This correction deliberately points to an earlier, already published commit and therefore does
  not create a circular self-hash. It does not authorize a replacement run, change scientific
  budgets or reuse any failed v37.0.0-v37.0.3 output. The formal run remains unsubmitted pending
  exact local/Rosetta worker alignment, removal of stale queue ambiguity, final placement and all
  submission gates.
- The correction checkpoint is commit `5a86a8a`; repository validation is Ruff clean with
  `679 passed, 4 skipped`. Compact content archive
  `var/archives/ampgent-v37-worker-identity-5a86a8a.zip` is 141,630 bytes with SHA-256
  `073d24de432a784943379fcdb62d376e780d4164bb240695ab09cec5350d5711`.

### 21.21 2026-08-13 v37.0.4 immutable persistence failure

- The unique v37.0.4 formal run was submitted exactly once as run
  `57a30fb0-e373-40ab-a629-1b22756bc70f`, workflow
  `pepagent-rapid-champion-v37-20cb54e347c0f5f58ffe1a401aa94de5e36898edb5bf8c601434cf1ad006505f`.
  It started at `2026-08-13T08:53:59.744756Z` and failed at
  `2026-08-13T09:15:48.143457Z`. It is immutable failed evidence: do not rerun, backfill, delete,
  retry in place, or reuse its uncommitted generator outputs.
- PostgreSQL contains 1 succeeded knowledge ToolCall and 2 linked evidence artifacts, but 0
  Candidate, 0 proposal occurrence, 0 Evaluation and 0 AgentDecision. Temporal shows all nine
  frozen generation activities completed, followed by failure of
  `persist_v37_generation_batch` with `v37 attempt lifecycle identity is invalid`. Therefore this
  run produced no admissible peptide cohort or scientific result.
- Read-only diagnosis localized the engineering defect to attempt-ledger projection: valid
  `v37.launch_receipt_persisted` and `v37.aggregate_launch_receipt_persisted` rows share the
  logical attempt lineage but do not use the `v37.attempt-event.1` payload schema;
  `build_v37_attempt_artifacts` validates that schema before excluding the receipt event types.
  This is a persistence-verifier ordering defect, not a generator, metric, GPU, peptide-quality or
  scientific-protocol failure.
- The only permitted next action is an isolated code/test correction and a new versioned recovery
  proposal. No replacement formal run is authorized by this entry. Any future recovery must retain
  the exact scientific budget and failed evidence, pass an adversarial fixture containing both
  receipt event types, rebuild immutable releases/placements/preflight, and receive an independent
  exact-once identity; local or Temporal generator return values may not repair PostgreSQL.
- The isolated projection repair is recorded in commit `e5d0171`. It filters to the four attempt
  lifecycle event types before applying the `v37.attempt-event.1` schema contract and adds an
  adversarial fixture containing both launch-receipt event types in the same lineage. Validation is
  Ruff clean; the targeted ledger suite is `13 passed`, and the pre-commit full suite was
  `680 passed, 4 skipped`. Compact content archive
  `var/archives/ampgent-v37-attempt-projection-e5d0171.zip` is 79,219 bytes with SHA-256
  `94dce2940983bf41b582ccd8981f979bbac18f4338f3d7eb046c735c4d803a4f`. GitHub push was attempted
  after the commit but was temporarily blocked by network connectivity; the local commit remains
  the authoritative repair checkpoint until push succeeds.

### 21.22 2026-08-13 current `.32` scoped GPU boundary

- The user's latest instruction makes `192.168.99.32 GPU0/GPU1` available to AMPgent subject to a
  fresh exact process-ownership and non-interference check. GPU2/GPU3 retain the stricter shared
  absolute prohibition: do not access, inspect, schedule, stop or indirectly use them. Every `.32`
  inspection must explicitly target only indices `0,1`; unscoped GPU enumeration is forbidden.
- Cross-task coordination with Codex task `019fcd9b-a14e-7741-a3ff-2fd0e1d3d4c7` acknowledged the
  new boundary. No existing process was stopped, migrated or preempted while updating it.
- This resource correction does not authorize a new formal run and does not alter the immutable
  failed status of v37.0.4. The frozen v37.0.4 configs and failed evidence must not be rewritten.

### 21.23 2026-08-13 non-`.32` GPU availability snapshot

- A coordinated read-only snapshot at `2026-08-13T22:28:00+08:00` did not access `.32` and did not
  stop, migrate or preempt any process. In addition to AMPgent's existing `.19 GPU4/GPU5` workers,
  `.19 GPU6` was the only newly available GPU: 15 MiB used, 0% utilization, no compute process, and
  no process environment declaring `CUDA_VISIBLE_DEVICES=6`. It may be considered for a future
  versioned worker placement only after a fresh direct ownership/occupancy/release preflight.
- `.19 GPU0/GPU1/GPU2/GPU3/GPU7` remain occupied or conflicted. In particular, `.19 GPU3` has an
  existing compute workload plus a stale AMPgent process declaration and must not receive another
  worker. synth GPU0--GPU4 are occupied or ownership-opaque; synth GPU5/GPU6/GPU7 remain reserved by
  long-running external Boltz runners despite low instantaneous memory use. None is safely
  assignable to AMPgent at this snapshot.
- Direct SSH recheck from this workstation timed out during jump-host banner exchange, so no remote
  connection was terminated and the coordinated snapshot is the current evidence. Availability is
  ephemeral: do not deploy from this paragraph alone. No new run or worker was started.
- Current-boundary checkpoint commit `2fcd757` is pushed. Validation is Ruff clean; the full suite
  is `680 passed, 4 skipped`, and the focused policy/protocol suite is `6 passed`. Compact content
  archive `var/archives/ampgent-gpu-boundary-2fcd757.zip` is 78,814 bytes with SHA-256
  `4a50fe1b2e93fc19b0d93d75c3a75e0e6a042f496f1cba0408c4a900d5a2ea18`.

### 21.28 2026-08-14 v37.0.6 unique formal run

- The execution-only recovery `v37.0.6-metric-observation-projection-recovery` was submitted
  exactly once at `2026-08-14T15:27:05Z`. The unique database run is
  `be7fcf5b-d3a8-49c7-8369-286d34a04599`; the Temporal workflow is
  `pepagent-rapid-champion-v37-316344bc35c74431b1c3680a94f21b686114f2c49ae3abbf65bbf229bcdb6acc`
  with Temporal run ID `f05913ea-ac0d-4f3f-8b0b-47f789a21af5`. It must not be submitted again,
  replaced, backfilled, or retried in place.
- Frozen science is unchanged: 900 candidates, 11 declared sequence observations per candidate,
  48 structure-shortlist candidates, three Boltz seeds per candidate, 16 Rosetta decoys per pose,
  an unweighted Pareto portfolio, database/object-store replay, and no ablation. The only behavior
  change is projection of provider observation supersets onto the declared metric set; auxiliary
  ToxinPred3 `toxinpred3_ml_score` remains in raw content-addressed evidence and does not enter
  Evaluation, selection, risk, or Pareto semantics.
- Submission inputs passed the minimal result-relevant preflight. Benchmark SHA-256 is
  `1ecadf9537f2bc6db05c27cf0999f6cc4f3f31295ef09ff2ce47f2c40e4ea518`, structure spec SHA-256 is
  `70a675a70bb6b430b87ac0a280f8798820bf203a202d993b53e46ad4a04a34f4`, worker source is
  `f6c754566405494739f7318afc47ed92ca3d9eda`, and immutable release SHA-256 is
  `571916a54a132bce8b5639328e849b5f24116a0464272bb6bafa998f15fb21e6`.
- Active workers are local control/generator/provider/metrics, `.19 GPU4/GPU5` Boltz, and synth CPU
  Rosetta. Every selected worker was checked as AMPgent-owned with no foreign process conflict.
  `.32` was not used for this run; `.32 GPU2/GPU3` remain absolute no-access/no-probe resources.
- Initial state is `running`. The knowledge ToolCall and its runtime receipts are already persisted.
  Subsequent progress and completion are reported only from actual Candidate, proposal occurrence,
  Evaluation, structure evidence, Pareto decision, and replay counts in PostgreSQL/object storage.
- Formal-run checkpoint commit `2b8a746` is pushed. Its compact tracked-content archive is
  `var/archives/ampgent-v37-006-formal-2b8a746.zip`, 1,405,803 bytes, SHA-256
  `39c9b347e6f4afdfb71dc301eafabbd2b3695790dd7827040ef91056f8ba0ce1`.

### 21.29 2026-08-14 v37.0.6 immutable failure and next execution repair

- The unique v37.0.6 run `be7fcf5b-d3a8-49c7-8369-286d34a04599` and Temporal run
  `f05913ea-ac0d-4f3f-8b0b-47f789a21af5` failed before candidate persistence. Its immutable
  database footprint is one succeeded knowledge ToolCall, two evidence artifacts, zero Candidate,
  zero proposal occurrence, zero Evaluation, and zero AgentDecision. It has no interpretable peptide
  result and must not be rerun, retried in place, backfilled, deleted, or used as a result source.
- Temporal history identifies `generate_v37_batch` activity ID `5`, HydrAMP seed `20270373`, as the
  terminal failure. Both attempts reached the generator worker but exceeded the 300-second heartbeat
  timeout. The activity performed the large HydrAMP model materialization before installing its
  30-second subprocess heartbeat, so a slow/concurrent materialization was incorrectly treated as a
  dead worker. This is an execution-liveness defect, not a generator, sequence, metric, or GPU result.
- The failed workflow left two still-running AMP-Designer subprocess trees tied exactly to this run.
  After verifying their run ID and parent lineage, PIDs `51472`, `52172`, `46236`, and `45384` were
  stopped; a follow-up process scan found no remaining process carrying the failed run ID. No foreign
  process was touched.
- The next versioned recovery may only add heartbeat coverage around HydrAMP materialization and
  cancellation-safe cleanup of generator subprocesses. It must preserve every scientific variable,
  seed, 900/11/48/3/16 budget, unweighted Pareto rule, and replay requirement. A future formal run
  requires a new exact identity and must never reuse v37.0.6 working-directory output.
- Of the nine logical generation batches, only the three AMP-GAN v2 seeds `20270374/75/76` returned
  successfully to Temporal. HydrAMP `20270371/72` and AMP-Designer `20270377/78` were scheduled but
  have no durable terminal result; AMP-Designer `20270379` was not scheduled before the barrier
  failed. None of these returns or local files entered the PostgreSQL evidence graph, so they remain
  non-results and are forbidden inputs to a later run.
- Execution-only repair commit `4289acf` is pushed. It adds 30-second Temporal heartbeats during
  HydrAMP materialization and terminates a spawned generator subprocess on cancellation or other
  post-spawn failure. Focused Ruff is clean; the repair suite is `22 passed, 1 deselected`, and the
  final critical checks are `4 passed`. Compact tracked-content archive
  `var/archives/ampgent-v37-007-heartbeat-fix-4289acf.zip` is 1,407,708 bytes with SHA-256
  `af1c185bcb76033b6f6a96445478df383828a214ef0ffb4e50eb398598d70823`.

### 21.30 2026-08-15 v37.0.7 unique formal run

- The execution-liveness recovery `v37.0.7-generation-heartbeat-recovery` was submitted exactly
  once at `2026-08-14T16:14:07Z`. The unique database run is
  `046f9867-450b-4b66-ac95-af4f37e9673a`; the Temporal workflow is
  `pepagent-rapid-champion-v37-1552c99fc2be29ef88d3d7861add6a88503fc2fe084a177bf5820527c851e237`
  with Temporal run ID `0c09bb7c-4cd0-4f56-9986-dfede4a3041c`. Never submit it again, replace it,
  retry it in place, backfill it, or reuse output from v37.0.0--v37.0.6.
- The new version changes no scientific field relative to v37.0.6: the same models, nine generator
  seeds, 900 candidates, 11 declared sequence observations per candidate, 48 structure candidates,
  three Boltz seeds, 16 Rosetta decoys per pose, unweighted Pareto portfolio, database/object-store
  replay, and no ablation remain frozen. Its only change is heartbeat coverage during HydrAMP model
  materialization plus cancellation-safe subprocess cleanup.
- Benchmark SHA-256 is `c81911de68acec45c6fdb0ae74924845271a877223116a03c20abd5a8623af6a`;
  structure spec SHA-256 is `85bb6030b913d961ad4a105d4d56fee42b8d986790c2feb92bcd05ba1ba1fadd`.
  Worker source is `4289acfdca1750a37415e81f9e168e40e50b9ee6`; immutable release SHA-256 is
  `b37f5eafa94435c04ca10291eda04dd804490f798aee005e5c7d61922f69c774`.
- The active topology is local control/generator/provider/metrics, `.19 GPU4/GPU5` Boltz, and synth
  CPU Rosetta. All selected processes are AMPgent-owned with no foreign conflict. `.32` is not used;
  `.32 GPU2/GPU3` remain absolute no-access/no-probe resources. Initial DB and Temporal status is
  `running`; progress is reported only from durable Candidate, occurrence, Evaluation, structure,
  decision, and replay evidence.
- Formal-run checkpoint commit `fb0f844` is pushed. Compact tracked-content archive
  `var/archives/ampgent-v37-007-formal-fb0f844.zip` is 1,417,862 bytes with SHA-256
  `2c727c8252fc19ff5dc741c42b29484d5a96b6ac17f1070ae6dfe2fecb251128`.

### 21.31 2026-08-15 v37.0.7 immutable failure and v37.0.8 recovery

- The unique v37.0.7 run `046f9867-450b-4b66-ac95-af4f37e9673a` and Temporal run
  `0c09bb7c-4cd0-4f56-9986-dfede4a3041c` failed before candidate persistence. Its immutable
  database footprint is one succeeded knowledge ToolCall, two evidence artifacts, zero Candidate,
  zero proposal occurrence, zero Evaluation, and zero AgentDecision. It has no interpretable peptide
  result and must not be rerun, retried in place, backfilled, deleted, or used as a result source.
- Temporal history identifies `persist_v37_generation_batch` activity ID `12` as the terminal
  failure. The v37.0.7 worker release was built from source `4289acfd...`, which contains the
  generation-heartbeat repair but predates the manifest loader's v37.0.7 version Literal. The worker
  therefore rejected the frozen v37.0.7 manifest during persistence. This is an execution-schema
  identity defect, not a generator, sequence, metric, structure, or GPU scientific result.
- The independent recovery identity is `v37.0.8-worker-schema-recovery`. It changes only the version,
  structure-spec identity, implementation identity, and worker source needed to recognize that new
  manifest. Every model, generator seed, 900/11/48/3/16 budget, selection rule, unweighted Pareto
  rule, database/object-store replay requirement, and no-ablation constraint remains identical to
  v37.0.7. No working output from v37.0.7 may be reused.
- The worker-loader source is commit `42e495c8b76e2c0eb0fa89cefcf1768322679142` and the bound
  benchmark checkpoint is commit `fa16e1d`. The frozen v37.0.8 benchmark SHA-256 is
  `9f939d0b06151390fc7257cbd27eb1fcd4629ac44a57e5bdb8b49593cbc3c7f3`; structure-spec SHA-256 is
  `f29a854ad35be090a2b866d33b6a7abeeea09cd275b7917564fff10818ee116b`. Focused validation is Ruff
  clean and `3 passed`. A formal run may be submitted exactly once only after workers are rebuilt
  from the bound loader source and the active placement is rechecked.

### 21.32 2026-08-15 v37.0.8 unique formal run

- `v37.0.8-worker-schema-recovery` was submitted exactly once at
  `2026-08-14T16:56:51.054968Z`. The unique database run is
  `1bc90f87-c01a-4187-a9b8-660dcd9aab43`; the workflow is
  `pepagent-rapid-champion-v37-af9647e8c389220b0a21ad6bb4411f360adfab2584aea85071ccaa91c7eb9250`
  with Temporal run ID `b74466c6-1e3e-4086-b680-3ef260389e18`. Never submit it again, create a
  replacement, retry it in place, backfill it, or reuse working output from v37.0.0--v37.0.7.
- Frozen science remains nine generator seeds, 900 candidates, 11 declared evaluations per
  candidate, 48 structure candidates, three Boltz seeds each, 16 Rosetta decoys per pose, an
  unweighted Pareto portfolio, database/object-store replay, and no ablation. The only recovery
  difference is worker manifest recognition of the new version identity.
- The active worker source is `42e495c8b76e2c0eb0fa89cefcf1768322679142`; immutable release SHA-256
  is `ba515bf492d6306143305f03279cbf67beed4d23cec25f0fc2cf8f061a668898`. The execution topology is
  local control/generator/provider/metrics, `.19 GPU4/GPU5` Boltz, and synth CPU Rosetta. All selected
  remote workers were verified AMPgent-owned with no foreign-process conflict; `.32` was not used.
- Submission preflight reported `ready_to_submit_unique_run` with no failed gate. Initial state is
  DB `running` and Temporal `RUNNING`, with one succeeded knowledge ToolCall, two evidence artifacts,
  and zero Candidate/Evaluation/Decision. Subsequent progress is reported only from durable
  Candidate, occurrence, Evaluation, structure, decision, and replay evidence.
- Formal recovery checkpoint commit `713d7fa` is pushed. Compact tracked-content archive
  `var/archives/ampgent-v37-008-formal-713d7fa.zip` is 1,428,183 bytes with SHA-256
  `5e8c06ecb722175e6bbbf511b7185eafe4e59b5f0fda4217a88c5963598ce8b0`.

### 21.33 2026-08-15 v37.0.8 immutable failure and metric-reference repair

- The unique v37.0.8 run `1bc90f87-c01a-4187-a9b8-660dcd9aab43` and Temporal run
  `b74466c6-1e3e-4086-b680-3ef260389e18` failed and remain immutable. The durable database footprint
  is 900 Candidate, 9,000 proposal occurrence, 20 succeeded ToolCall, 900 Evaluation, 87 linked
  evidence artifacts, nine dependencies, and zero AgentDecision. The only complete Evaluation family
  is 900 `amp_read_log10_mic_um` observations; there is no complete sequence panel, structure
  shortlist, Pareto portfolio, or interpretable final peptide result. Never rerun, retry in place,
  backfill, delete, or reuse its candidates, working outputs, or orphan objects.
- Temporal activity ID `21`, `evaluate_v37_sequence_metric` for
  `physicochemical_developability`, completed computation but its 2,263,234-byte completion blob
  exceeded the 2,097,152-byte server limit by 166,082 bytes (7.919%). The full 900-record metric
  object exists in object storage but never entered the PostgreSQL evidence graph, so it is an
  orphan and is not a result source. This is an execution-transport defect, not a peptide, metric,
  model, or GPU scientific failure.
- Execution-only repair commit `36b40b7329813f6ebc6ea1963a51555052f0a139` stores the complete
  metric result and provenance as canonical content-addressed JSON and returns only a compact typed
  SHA/URI receipt through Temporal. The persistence activity retrieves the full object, verifies
  byte length, SHA, canonical payload identity, plugin, and transition receipt, then follows the
  unchanged ToolCall/Evaluation/artifact/replay path. Scientific values, models, seeds, 900/11/48/3/16
  budgets, unweighted Pareto semantics, and no-ablation policy are unchanged. Focused Ruff is clean
  and the new reference tests pass.

### 21.34 2026-08-15 v37.0.9 unique formal run

- `v37.0.9-temporal-metric-result-reference-recovery` was submitted exactly once at
  `2026-08-14T18:25:41.136215Z`. The unique database run is
  `ee86c78a-d316-4cc6-870a-be93f22b769f`; workflow is
  `pepagent-rapid-champion-v37-a116d44c28b9bb604587054d0045594c0a11651d5f4924822154f29387f5d445`
  with Temporal run ID `24c1665c-e454-43df-9eeb-40b92121b926`. Never submit it again, create a
  replacement, retry in place, backfill, or reuse v37.0.0--v37.0.8 working output.
- Frozen science remains nine generator seeds, 900 candidates, 11 declared evaluations each,
  48 structure candidates, three Boltz seeds each, 16 Rosetta decoys per pose, unweighted Pareto,
  database/object-store replay, and no ablation. Only metric-result transport changed: full evidence
  is content-addressed in object storage while Temporal carries a verified compact reference.
- Benchmark SHA-256 is `750d63ec69977aa24f417e7d3827e69eb86df67c5a6cd444c851e41e31af66f4`;
  structure SHA-256 is `330418085b45d0863e3937439e1a11b8a877b7dc71207a9c99b531471d17b045`.
  Worker source is `365f2460c08636b6ca596dd6ed481996b27fa04b`; immutable release SHA-256 is
  `ed4cbe1826d8b75a93ba29a3241156dd89f463537f5da7c9f1574bcc9d9d8636`.
- Submission preflight was `ready_to_submit_unique_run` with no failed gate. Initial DB/Temporal
  state is `running`/`RUNNING`, with one succeeded knowledge ToolCall, two linked artifacts, zero
  Candidate/Evaluation/Decision, nine started generation attempts, one succeeded and eight pending
  on attempt one. Progress is reported only from durable scientific evidence.
- Formal checkpoint commit `6eba4fa` is pushed. Compact tracked-content archive
  `var/archives/ampgent-v37-009-formal-6eba4fa.zip` is 1,440,785 bytes with SHA-256
  `d7595d6270a5cf1b6b04d4d525e8be68ed92024d85b981dcb5ea3d1dcd3cebb0`.

### 21.35 2026-08-15 v37.0.9 immutable failure and lifecycle concurrency repair

- The unique v37.0.9 run `ee86c78a-d316-4cc6-870a-be93f22b769f` and Temporal run
  `24c1665c-e454-43df-9eeb-40b92121b926` failed and remain immutable. Its durable footprint is
  900 generated Candidate, 9,000 proposal occurrence, 21 succeeded ToolCall, 2,700 succeeded
  Evaluation, 91 linked evidence artifacts, 18 dependencies, and zero AgentDecision. Complete
  Evaluation families are AMP-READ (900), ToxinPred3 hybrid (900), and ToxinPred3 label (900).
  There is no complete sequence panel, structure shortlist, Pareto portfolio, or final peptide result.
- All five metric computations completed on attempt one and returned compact 842--863-byte Temporal
  references; the prior metric-result size repair therefore worked. Failure occurred when concurrent
  persistence transactions both used unlocked `max(sequence_no)+1` lifecycle allocation. Hemolysis
  lost two races for run sequences 9042 and 9044 and exhausted retries with a PostgreSQL unique-key
  violation. This is database event-ordering concurrency, not a peptide, metric, model, or GPU failure.
- Unlinked hemolysis, physicochemical, and LLAMP metric-result objects remain orphan evidence and may
  not be backfilled or reused. The complete v37.0.9 Candidate set and its partial evaluations likewise
  must not seed a recovery run. Preserve the run and objects as immutable failure evidence only.
- Repair commit `447cb3928c8763681b3043cbc27a1dc83d56828e` acquires a stable transaction-scoped
  PostgreSQL advisory lock for each lifecycle aggregate before allocating `max(sequence_no)+1`.
  This serializes only the short same-aggregate event-number allocation; metric compute, object-store
  writes, Evaluation persistence, and different aggregates remain concurrent. A deterministic 32-way
  adversarial test produces exactly sequences 1--32 without evidence loss; the full suite is
  `709 passed, 4 skipped` and Ruff is clean.

### 21.36 2026-08-15 v37.0.10 unique formal run

- `v37.0.10-lifecycle-sequence-concurrency-recovery` was submitted exactly once at
  `2026-08-14T19:23:38.125519Z`. The unique database run is
  `ae0e52be-bf0f-4c41-a8a4-8fbd061bbf78`; workflow is
  `pepagent-rapid-champion-v37-0ff398195e60f5b334284199e52e205a9331ca043635d9092b5b09bd984f7061`
  with Temporal run ID `d9013db6-562f-40ed-b022-c09b830e9b58`. Never submit it again, create a
  replacement, retry in place, backfill, or reuse v37.0.0--v37.0.9 working output.
- Frozen science is unchanged: nine generator seeds, 900 candidates, 11 declared evaluations each,
  48 structures, three Boltz seeds each, 16 Rosetta decoys per pose, unweighted Pareto,
  database/object replay, and no ablation. Only same-aggregate lifecycle sequence allocation is
  serialized; scientific computation and evaluation persistence remain concurrent.
- Benchmark SHA-256 is `f5a029b91f7701bc85f63ff2e9970a735d4ef53bad1ff60013e4857dbe6dbc1a`;
  structure SHA-256 is `cd9877f19fc5fd9fe685fa7760634b8681b87dcfcaee641deec36122e85ff538`.
  Worker source is `6c458612e09d57af5d3bf60ea6454dcb8d49d6a0`; release SHA-256 is
  `72046ad3ab5882b62411009a8fff94582be1bdaedefa6db854eaf5abb45785e1`.
- Submission preflight was `ready_to_submit_unique_run` with no failed gate. Initial DB/Temporal
  state is `running`/`RUNNING`, with one succeeded knowledge ToolCall, two linked artifacts, zero
  Candidate/Evaluation/Decision, nine started generation attempts, one succeeded and eight pending
  on attempt one.
- Formal checkpoint commit `4052ca3` is pushed. Compact tracked-content archive
  `var/archives/ampgent-v37-010-formal-4052ca3.zip` is 1,453,044 bytes with SHA-256
  `7c233b70c0390618e9924f58366ad4e927db723e105d018c9cdff8d93f2abae6`.

### 21.37 2026-08-15 v37.0.10 immutable failure and supervised `.19` service tunnel recovery

- The unique v37.0.10 run `ae0e52be-bf0f-4c41-a8a4-8fbd061bbf78` and Temporal run
  `d9013db6-562f-40ed-b022-c09b830e9b58` failed and remain immutable. Its durable footprint is
  900 Candidate, 9,000 proposal occurrence, 26 succeeded ToolCall, all 9,900 required Evaluation
  rows (11 declared metrics for every candidate), 118 evidence links, 46 dependencies, and two
  succeeded AgentDecision rows. The sequence-stage decision excluded 257 concordant toxicity-plus-
  hemolysis red flags, retained 643 eligible candidates, and deterministically selected the frozen
  48-member structure shortlist. This is a complete sequence panel and a structure shortlist, not a
  final structural Pareto portfolio or an experimental activity, safety, affinity, or binding result.
- Boltz activity dispatch began, but both `.19` workers failed before `_begin_database_attempt` could
  acquire a database attempt record because remote `127.0.0.1:55432` refused connections. Boltz
  inference never started: there are zero Boltz ToolCall rows, zero structure Evaluation rows, and no
  orphan coordinates or structure scores. The missing PostgreSQL reverse forward was an execution-
  dependency liveness failure, not a candidate-quality, model, seed, GPU, or structure-science failure.
  Never rerun, retry in place, backfill, delete, or reuse v37.0.10 candidates, evaluations, shortlist,
  working outputs, or activity identities in a recovery run.
- Repair commit `59c28ed259a6870b236804a13751f7e744a11ab8` replaces the temporary `.19` forwards with one
  hidden supervised SSH session that holds PostgreSQL `55432`, Temporal `17233`, and object-store
  `19000` together, uses keepalives and a bounded reconnect loop, and fails worker launch before GPU/
  PID claim unless all three remote loopback services accept TCP connections. A forced termination of
  the exact AMPgent SSH child demonstrated watchdog reconnection in under 20 seconds; subsequent real
  remote probes passed PostgreSQL `SELECT 1`, Temporal active-workflow query, and MinIO health.
  No `.32` resource or foreign process was accessed or stopped.
- Independent `v37.0.11-supervised-remote-service-tunnel-recovery` preserves every v37.0.10
  scientific field and the 900/11/48/3/16 budget, unweighted Pareto semantics, database/object-store
  replay, and no-ablation policy. Benchmark SHA-256 is
  `1b80183e6cb28535d0619fb82051561e0acf72fffbcb3e5d625efa3cc29e8fd5`; structure SHA-256 is
  `1f4b67dfb9f2bd609bc95e24ef64662ec06df8c35a984add44fdffad397fbad4`.
  Worker source is `9784a6629a4c41cf9f986660ab6b3a99dec11090`; immutable release SHA-256 is
  `1b1654e70c7bfaca0d570f7240403915e4f396f726fd64e4c321d0855f14a530`. The live topology is local
  control/generator/provider/metrics, `.19 GPU4/GPU5` Boltz, and synth CPU Rosetta; real `.19` probes
  passed before and after migration, active workflows were zero, and unique-run preflight reported
  `ready_to_submit_unique_run` with no failed gate.
- v37.0.11 was submitted exactly once at `2026-08-14T20:29:40.785147Z`. The unique database run is
  `1b1dedea-fd1d-4dfa-816b-f69421ea8158`; workflow is
  `pepagent-rapid-champion-v37-ff1c1369cc260452aa403b952f623ba664aa7335a35bab831ce38e946d54addb`
  with Temporal run ID `7fd3ad92-89bc-45eb-9338-a484a7ed0db9`. Never submit it again, create a
  replacement, retry it in place, backfill it, or reuse v37.0.0--v37.0.10 working outputs. Initial
  DB/Temporal state is `running`/`RUNNING`: one succeeded knowledge ToolCall, two evidence artifacts,
  zero Candidate/occurrence/Evaluation/Decision, nine generation attempts started, one succeeded,
  eight pending, and no failed or retried ToolCall.
- Formal checkpoint commit `dacf477` is pushed. Compact tracked-content archive
  `var/archives/ampgent-v37-011-formal-dacf477.zip` is 1,466,112 bytes with SHA-256
  `b8dbaeafde2624c9129bc10cb638de1a717ac560f8efbd6412e4b5be70ca4df5`.

### 21.38 2026-08-15 v37.0.11 immutable failure and Boltz runtime repair

- The unique v37.0.11 run `1b1dedea-fd1d-4dfa-816b-f69421ea8158` and Temporal run
  `7fd3ad92-89bc-45eb-9338-a484a7ed0db9` failed at `2026-08-14T20:51:14Z` and remain immutable.
  Its durable footprint is 900 Candidate, 9,000 proposal occurrences, all 9,900 declared sequence
  Evaluation rows, 26 succeeded ToolCalls, 118 evidence links, two decisions, and the deterministic
  48-member structure shortlist. There is no Boltz/structure evidence and no final Pareto decision.
- The supervised PostgreSQL/Temporal/object-store tunnel remained live and the sequence stage
  completed. Boltz activity 35 instead failed twice before inference because the frozen worker
  environment did not contain the configured executable
  `/data1/huangyueshan/pepagent/envs/gpu-worker-py311-v1/bin/boltz`. This is a worker runtime and
  placement-verification defect, not a peptide, model, seed, GPU-compute, or structure-science result.
  Never rerun, retry in place, backfill, delete, or reuse v37.0.11 candidates, evaluations, shortlist,
  working files, or activity outputs.
- Recovery preparation now fails Boltz worker launch before GPU inspection or PID claim unless the
  exact managed console script is executable, the `boltz` Python package imports, and `boltz predict
  --help` exposes every required frozen CLI option. The adapter also rejects a missing executable
  explicitly instead of manufacturing a path that fails later inside a formal activity. This is an
  execution-only repair; it does not alter sequences, models, seeds, metrics, structure budgets,
  Pareto semantics, or evidence requirements.
- A future recovery additionally requires rebuilding or repairing the `.19` managed Boltz environment
  and completing a real provider smoke before worker launch. Resource availability or a passing
  static gate is not formal-run authorization; no replacement run is authorized by this entry.

### 21.39 2026-08-15 Boltz real-smoke cache dependency

- The `.19` managed environment now contains the exact `boltz` console script and importable Boltz
  2.2.1 package. A real GPU4 AceA-pocket smoke then reached the provider executable but exited before
  inference because Boltz attempted to fetch its CCD/model cache from the public network and `.19`
  has no outbound route. This is a second deployment dependency exposed by the required real smoke,
  not a peptide-science or GPU-compute result; the smoke produced no output artifact.
- The authoritative existing cache is on synth at
  `/sdd_data/pepagent/models/boltz2/cache`: `boltz2_conf.ckpt` is 2,286,561,469 bytes with SHA-256
  `090e82ac8c92f5e943fa1b39e7410a44027bea7243c0bbb3caa67a77fc1428e1`, and `mols.tar` is
  1,855,662,080 bytes with SHA-256
  `39e076d96dbec6b4e86982bbda16f3a53a2a60c9bdc17828d88f6f9a0c7d1fd7`. These large files remain
  server-owned; a streaming synth-to-`.19` transfer is in progress through the local SSH client and
  does not materialize either file in the workspace. Final paths are not promoted until their full
  SHA-256 matches.
- The next deployment smoke must use the verified `.19` cache and complete one real AceA-pocket
  prediction before any new worker/release placement is accepted. This repair does not authorize a
  new formal run and does not change the frozen 900/11/48/3/16 science contract.
- The server-to-server cache transfer completed without local large-file materialization. Both large
  objects passed the SHA-256 checks above, the CCD archive expanded to 45,227 molecule files, and a
  second real GPU4 smoke completed an AceA-pocket prediction with seed `20270380`. Its compact result
  is `/data1/huangyueshan/pepagent/runs/v37-boltz-runtime-smoke-20260815-3/output.json`, 1,952 bytes,
  SHA-256 `892373b095e8a4b5fa777df98fdd0f76ed61f82e85f24918502e9b06853abf73`; it references the CIF,
  PAE/PDE, pLDDT, confidence, processed-structure and constraint artifacts. The smoke reported
  confidence `0.3135782778`, ipTM `0.3917916715`, peptide-target pair ipTM `0.2151030749`, and complex
  ipLDDT `0.2859012485`. These are runtime-smoke observations for a sentinel peptide, not selectable
  v37 scientific evidence and not a champion claim. The Boltz executable/package/cache/GPU runtime
  repair is now complete; a future formal recovery still requires a new immutable version and
  explicit formal-run authorization.

### 21.24 scheduled idle-capacity wake rule

- `.19` may be inspected read-only during every scheduled patrol. The monitor
  `deploy/windows/check_ampgent_gpu_capacity.ps1` probes `.19 GPU0--GPU7` and only the explicitly
  allowed `.32 GPU0/GPU1`; it never probes or enumerates `.32 GPU2/GPU3`. A GPU is reported idle
  only when memory and utilization are below the frozen conservative bounds, there is no compute
  process and no matching `CUDA_VISIBLE_DEVICES` declaration.
- The script writes only the small state file `var/state/ampgent-gpu-capacity.json` and emits
  `WAKE_REQUIRED=true` when reachability, observation status or the idle GPU set changes. The existing 30-minute heartbeat is the
  supported mechanism that invokes the script and wakes this thread; the script does not submit a
  run, start a worker, terminate a process or mutate scientific evidence. A wake triggers fresh
  placement/release review, not automatic deployment.
- Monitor checkpoint commit `e979745` is pushed. Validation is Ruff clean and `682 passed, 4
  skipped`. Compact archive `var/archives/ampgent-gpu-idle-monitor-e979745.zip` is 82,237 bytes with
  SHA-256 `918c2d333049cd8f4704a25b783166e2d5496738321b39f182a70db124ddb9b1`.
- The first scheduled patrol exposed and corrected a monitor-only false conflict: shell permission
  errors while reading unrelated `/proc/*/environ` files were entering the declaration list. The
  corrected probe suppresses those read errors before matching the exact allowed GPU index.
- After correction, the scoped `.32` snapshot reports GPU1 idle (15 MiB, 0%, no compute process or
  device declaration). GPU0 has the same instantaneous memory/utilization but is not idle because
  PID `2001800`, `bash tools/watch_v4_sample_milestones.sh`, declares `CUDA_VISIBLE_DEVICES=0`;
  therefore GPU0 must not be used or displaced. `.19` remained unreachable through the jump host,
  so its earlier GPU6 availability is not current authorization. No GPU2/GPU3 probe occurred.

### 21.25 2026-08-14 第一性原则恢复目标

- 用户要求调整执行风格：以尽快产生质量更高、可解释且可查看的短肽为唯一当前主线，不再把
  worker 证书、部署仪式或门禁完备度当作独立成果。安全且在范围内的 routine fix 应直接实施并继续，
  不能因为等待普通确认而空转。
- v37.0.4 及更早失败 run 仍保持不可变。下一独立恢复版本的目的仅是让已修复的持久化路径尽快完成
  一次新身份的端到端 champion run；不得复用失败输出，也不得改变原定 900 条候选、五类序列评价、
  48 条结构短名单、每条 3 个 Boltz seed、每 pose 16 个 Rosetta decoy、非加权 Pareto portfolio 与
  database+object-store replay 的科学预算和判定语义。
- 恢复前的最低工程记录收敛为 host、GPU/CPU、PID/role、source revision、无外来进程冲突，以及
  API/PostgreSQL/MinIO/Temporal 足以接收和持久化本次 run 的健康状态。只有能够改变执行字节、科学
  输出、证据完整性、重复提交或资源安全的异常才阻塞；其余部署元数据和审计细节可在不影响主线时
  追加，不能延迟首批 Candidate/Evaluation 落库。
- 完成标准不是“所有门禁文件齐全”，而是新版本从生成到评价、结构、portfolio 的科学证据持续落入
  PostgreSQL，并能由数据库和对象存储复原；随后以候选质量、冲突、方向稳定性和多样性解释结果。
  在没有这些可解释候选之前，不把工程修复或 GPU 在线称为阶段性科研成果。

### 21.26 2026-08-14 v37.0.5 唯一正式运行

- 结果优先恢复版本 `v37.0.5-attempt-receipt-projection-recovery` 已在不改变科学预算、模型、seed、评价、结构协议、Pareto 语义或停止条件的前提下完成冻结。benchmark SHA-256 为
  `39d750ca9f1fe3de45aae4cd763845569b1f2501a4b40c8ffdc4f246758a6854`，结构 spec SHA-256 为
  `42757a1ba22d4e8c8c5e73246bc19814e814baf4b3f59b239a0197f31be80017`。
- 唯一正式 run 为 `1655ba61-f380-4669-8b03-ccda4ae33c7d`，workflow 为
  `pepagent-rapid-champion-v37-ab6d1d6b70b82262dc2d4408f6644cfcd0fabfbf03942af6bfc6ac830611844e`，
  Temporal run ID 为 `669d7577-98bc-4daf-ac0b-1e297175e0e2`。它于
  `2026-08-14T13:54:29.026483Z` exact-once 提交；禁止重复提交、创建替代 run、原地重试、回填或复用
  v37.0.0--v37.0.4 的未持久化输出。
- 执行 worker source revision 为 `5bd1f9595ca8767230ee9a2b8a12686862ebf665`，immutable release SHA-256 为
  `b58a5591a9eefa933cfa75ffce3e9c9d74bdd57336a499cdfe058da0888f60b2`。最小 placement 记录为本地
  control/generator/provider/metrics、`.19 GPU4/GPU5` Boltz 和 synth CPU Rosetta；全部已核验 PID/role、
  source revision 与无外来进程冲突。`.32 GPU2/GPU3` 仍为绝对禁区，本次运行未使用 `.32`。
- 当前状态为 `running`。知识调用已经首先进入 PostgreSQL；九个冻结生成批次均在确定性执行计划中，
  当前实现按并发上限 8 分批，前 8 个先派发，第 9 个在首批屏障完成后自动派发。该屏障只影响吞吐，
  不改变 900 条科学预算；后续工程演进应改为保持顺序的滑动并发，避免空闲槽等待最慢任务。科学进度只按
  Candidate、proposal occurrence、Evaluation、结构证据、Pareto decision 与 database/object-store replay
  的实际持久化计数报告，worker 在线和预检通过不单独算作短肽成果。
- v37.0.5 合同、第一性原则风格、执行身份与提交事实的紧凑内容归档为
  `var/archives/ampgent-v37-005-formal-fb4f6c3.zip`，96,377 bytes，SHA-256
  `e0572610dab5e958fec32994c4c0f8c96bc45d435c83c36ea15dda952d08fb65`。归档不包含大型模型、运行工作目录
  或结构产物；这些大对象继续由既定远端位置和内容寻址对象存储承载。

### 21.27 2026-08-14 v37.0.5 失败事实与 v37.0.6 指标投影恢复

- v37.0.5 唯一正式 run `1655ba61-f380-4669-8b03-ccda4ae33c7d` 已于
  `2026-08-14T14:21:33.549365Z` failed 并永久保持不可变。数据库实际足迹为 900 Candidate、9000
  proposal occurrence、19 succeeded ToolCall、83 evidence artifact、0 Evaluation、0 AgentDecision；
  因评价尚未持久化，不能从该 run 给出可解释短肽排序、结构结论或 Pareto portfolio，也不得原地重试、
  回填、删除或复用其未持久化结果。
- 只读诊断确认五类序列评价均已开始，首个完成的 ToxinPred3 runtime 对每条候选返回冻结合同声明的
  `toxinpred3_hybrid_score`、`toxinpred3_label`，并额外返回 provider 自带的
  `toxinpred3_ml_score`。旧 persistence projection 错误要求 provider 输出集合与冻结声明集合严格相等，
  因而将合法 provider 超集误判为漂移；这不是生成器、候选身份、模型执行或 GPU 失败。
- 修复 commit `e5e9d50bcd1d5b63b02cca1d80beca0478ce9376` 要求每条记录完整包含冻结声明指标并拒绝重复名，
  但只把声明指标按确定顺序写入 Evaluation。额外 provider 指标继续保留在 content-addressed 原始 artifact/
  replay 中，不被静默提升为本 run 的评价、筛选或 Pareto 变量。focused Ruff clean，相关 pytest 40 passed。
- 独立恢复版本 `v37.0.6-metric-observation-projection-recovery` 已预注册；benchmark SHA-256 为
  `1ecadf9537f2bc6db05c27cf0999f6cc4f3f31295ef09ff2ce47f2c40e4ea518`，结构 spec SHA-256 为
  `70a675a70bb6b430b87ac0a280f8798820bf203a202d993b53e46ad4a04a34f4`。除版本身份、结构 spec 绑定和
  implementation revision 外，它与 v37.0.5 的全部科学变量、seed、900/48/3/16 预算、11 个 required
  metrics、非加权 Pareto 和 replay 合同逐字段相同。worker source revision 单独更新为包含该修复与恢复
  合同的 `f6c754566405494739f7318afc47ed92ca3d9eda`；下一步只做最小 release/placement 更新后 exact-once
  提交一个 v37.0.6 run，严禁再次提交 v37.0.5。
- 修复、恢复合同和失败事实 checkpoint 为 commit `bfe7a51b953cfe1fd667bb56064328b51ef24c8c`；其紧凑
  tracked-content 归档 `var/archives/ampgent-v37-006-recovery-bfe7a51.zip` 为 1,404,863 bytes，SHA-256
  `34a08a2e8b9315802aed9fcad8b921fd1c0ef16114ee8c90219721bec9796508`。归档不包含运行工作目录、模型权重
  或结构大对象。

### 21.40 2026-08-15 v37.0.12 唯一正式运行

- 用户于本轮明确指令“执行”，授权提交新的唯一正式恢复 run。独立身份
  `v37.0.12-boltz-runtime-smoke-recovery` 仅绑定已经通过真实 GPU4 AceA-pocket smoke 的 Boltz
  2.2.1 可执行环境与离线模型/CCD cache；九个生成 seed、900 Candidate、每条 11 个声明评价、
  48 条结构短名单、每条 3 个 Boltz seed、每 pose 16 个 Rosetta decoy、非加权 Pareto、
  database/object-store replay 与不做消融均逐字段继承 v37.0.11。benchmark SHA-256 为
  `e262d307a1017ccaf4013b696ecca9016e37606ab018c7b41971ed015cba0ca8`，结构 spec SHA-256 为
  `4565a988c76af6059f4427c7adf286a1838470a36c52466da81bc65a183d8c82`。
- worker source revision 为 `e64e310517af1d3fc16437552fff476fd70a87a7`，immutable release SHA-256
  为 `090825489cf6c8e43288728e0b9eff32e9dca8600f8f6223500c2620ccfd56b8`。执行拓扑为本地
  control/generator/provider/metrics、`.19 GPU4/GPU5` Boltz 与 synth CPU Rosetta；七个 worker
  均由新 source/release poller 精确匹配，GPU4/GPU5 启动前无计算进程或外来 CUDA 声明，三条
  `.19` 反向服务隧道均在线，真实 smoke output SHA-256 仍为
  `892373b095e8a4b5fa777df98fdd0f76ed61f82e85f24918502e9b06853abf73`。未访问 `.32 GPU2/GPU3`。
- placement SHA-256 为 `73ab4f5c45a870357b5b5087388fac241c4ef339a7f93685e197ebb8761f32b0`；
  execution bundle、static preflight 与 submission preflight 文件 SHA-256 分别为
  `b75e1844b37630b8ced96bb22ad78d24770d4f24d7ddcc71bc3662a0304e907b`、
  `03f5302a21b296fd2cea4d9cf954f1e2f47cc4fa2a5cdec93511018f936ccf8d` 与
  `8da09fdd0a5ae06899e3f990d4a4f9e551d6f83d3a9ad49f269d345e220fc1ee`。所有动态门禁通过；
  canonical submission-preflight identity 为
  `2d8ed95836475459fad40de7dab604ea7589f028883f3846cfa52f8169f25502`。
- 提交命令仅执行一次。唯一数据库 run 为 `69d50a9d-ccdb-4345-89ed-2e00f02fe9b8`，workflow 为
  `pepagent-rapid-champion-v37-b4e7d029cc07d24b15274991cc985daba92685818eee7a98f347d3e3dcde0c9d`，
  Temporal run ID 为 `9a58ac69-0aa1-48e2-bed0-6f5a9d5efe78`，提交时间为
  `2026-08-15T14:09:12.748949Z`。初始 DB/Temporal 状态为 `running`/`RUNNING`：一个 knowledge
  ToolCall、两个 evidence artifact、零 Candidate/occurrence/Evaluation/Decision；九个生成 attempt
  已开始，一个完成，八个 attempt 1 activity 在运行。严禁再次提交、创建替代 run、原地重试、
  回填或复用 v37.0.0--v37.0.11 工作输出。
- 正式 checkpoint commit `ac94818b750e797af3bea49000c0ef67da73c4c1` 已推送。紧凑 tracked-content
  归档 `var/archives/ampgent-v37-012-formal-ac94818.zip` 为 1,480,196 bytes，SHA-256
  `92684167729a47a9896d613281e04beb2c1678684af5890c924f24f3cbf0fa8b`；不包含模型、运行工作目录或
  科学大对象。

### 21.41 2026-08-17 v37.0.15 用户终止与 v38 framework-only 升级

- 用户明确要求停止当前版本、先优化 Agent 框架且暂不运行短肽生成。v37.0.15 run
  `a9e8dfc7-9da4-4c99-b014-f2b597d22adc` 的 Temporal workflow 已以
  `user_requested_stop_for_sequence_first_historical_multitarget_framework_upgrade` 终止，PostgreSQL
  已闭合为 `cancelled`。不可变持久化基线为 900 Candidate、9000 proposal occurrence、11468
  Evaluation、282/282 succeeded ToolCall 和 2 AgentDecision；候选状态为 852 generated、22
  rosetta_scored、26 structure_scored。没有删除、回填、复用或替代 run，也没有提交新的 formal run。
- 新的 v38 framework-only 合同定义三段式 Agent：历史终态证据快照；知识卡驱动的全量序列评价、迭代
  refinement 和序列成熟度准入；同一成熟序列 cohort 向多个已资质化靶点的隔离结构分支并行分发。
  历史快照必须完整包含 succeeded/failed/cancelled 分母，但只保存身份、哈希和持久化计数，禁止复制
  历史候选或结果。新框架复用 v36 typed harness lineage 和 v35 target qualification/panel witness。
- 序列成熟度明确使用双 MIC、毒性、溶血和理化可开发性，并加入域外检测、模型一致性和排序稳定性；
  阈值必须来自外部冻结证据，禁止用本批分位数产生生物学阈值。知识 provider task
  `019fad3e-76b8-7e32-8455-d2e9b31d33e5` 前置到 proposal/refinement，每次采用或拒绝都保存查询、
  passage 哈希和理由。知识支持不是选择分数。
- 多靶点面板必须在看到新肽结果前冻结，至少两个唯一靶点；AceA 可以是 anchor，但不能是唯一靶点。
  所有分支使用相同成熟序列输入、相同预注册结构预算、独立 evidence namespace、native/wrong-pocket
  controls 和完整失败分母，一个靶点结果不得改变另一分支。当前
  `formal_run_authorized=false`、`formal_run_submitted=false`。

### 21.42 2026-08-17 v38.1 分层准入、多靶点证据与控制 run

- 用户随后授权实现并启动新版 Agent。v38.1 不再把不易跨模型、菌株和实验条件校准的外部 MIC/活性数值
  作为硬生物学门；有效性、缺失/域外、毒性和溶血仍严格处理，MIC、AMP 活性和理化可开发性进入非加权
  Pareto。成熟核心不足时最多执行三轮带父子谱系和知识卡理由的 refinement；核心为零不降安全门，结构
  名额可以留空。最多 20% 的结构预算可给安全但不确定的固定探索 lane，禁止强制补满。
- 第一面板冻结为 GyrA/LEI-800 与 PBP2a/allosteric。两者均绑定独立 target、coordinate SHA、native pocket、
  wrong-pocket control 和证据 namespace；当前 AceA 只有一个合格 pocket，因此未被伪造为首轮多靶点分支。
  GyrA 坐标为 RCSB `8QQI`，SHA-256
  `f316a9c7efb6ca84224400ba944cd21627ad3fa4ab7e7f2ac45ea44d3f46e0d2`；PBP2a 为 `3ZFZ`，SHA-256
  `4613740b7fb41a89913b28681998e3d75ac84ba7e0d3813549e94de1e6982fc7`。
- provider task `019fad3e-76b8-7e32-8455-d2e9b31d33e5` 已做真实只读 smoke，context-pack SHA-256 为
  `d918d8faac581eebdf665593dbd81f50f24482924ea2ede302b3d273595f0c53`。通用 AMP 改写规则可进入 refinement，
  AceA 特异内容不得冒充 GyrA/PBP2a 靶向证据；每次采用或拒绝都需保存 query/passage SHA 和理由。
- 新 schema `experiment_run_target_branches` 与 `run_stage_checkpoints` 已迁移到 PostgreSQL。控制 run
  `b931b9df-c618-4d89-a1d1-ec52acc6e74e` 已冻结 54 个终态历史 run、两个 target branch 和 knowledge identity，
  当前停在 `proposal_generation` 前：formal science workflow 未提交，Candidate/occurrence/Evaluation/Decision
  均为 0。这不是短肽生成完成或失败，而是诚实的 staged-preflight run。
- 控制器每 5 分钟检查 durable 进展，每 15 分钟持久化阶段计划 review，每 120 分钟触发用户 review；
  30 秒 heartbeat 用于未来长 activity。远端容量探测在 durable tick 之后运行。当前卡点是兼容 v38 的
  score-all/refinement/multi-target executor 尚未部署，且本次获准结构 GPU 不可达；禁止用 v37 的 first-100、
  单靶点 workflow 冒充 v38 正式 run。
- score-all 序列执行合同已在 commit `0b6e110` 实现：9 个生成 cell 各请求 100 条，900 个 raw occurrence
  中的有效、无效和重复分母全部保留，所有有效唯一序列均按 source ordinal 进入评价，不存在 first-K 截断。
  refinement 子代必须绑定父候选、未改父本 control SHA、真实序列变化、mutation rationale 和至少一条 adopted
  knowledge trace。全量回归为 767 passed、4 skipped。该里程碑仍是执行合同，不代表 generator/Temporal/
  persistence worker 已部署，也不改变当前 0 Candidate 的控制 run 事实。
- 序列准入现已收敛为可执行的 cohort 决策，而不是为每个序列设一个容易清空全批的 MIC 数值线。LLAMP
  与 AMP-READ 必须都成功，并作为两个独立的非加权 Pareto 轴；二者的分歧保留为权衡证据，不使用固定
  差值阈值。只有 provider 合同中的 `macrel_hemolysis_label=low` 与
  `toxinpred3_label=Non-Toxin` 是安全硬门；缺失、失败、重复或域外仍 fail closed。
- 只有第一非支配前沿可以进入 `mature_core`，被全面支配的安全序列不会因为结构名额未满被硬塞进核心。
  `promising_uncertain` 最多占结构预算 20%；核心不足 12 条时结构 dispatch 完全冻结，并生成有界的知识卡
  refinement work order。每个 work order 绑定 provider task/context-pack、父序列哈希、未改父本 control、
  改写目标和完整 11 指标重评义务；最多三轮，绝不降低安全门或强制填满。

### 21.43 2026-08-17 v38 授权 GPU 连接恢复

- `.19` 原 AMPgent 三服务 reverse tunnel 的 SSH 子进程卡在 banner；仅在精确父子 ownership 核验后终止
  该 SSH 子进程，由原 watchdog 自动建立新连接。远端 `55432`、`17233`、`19000` 均重新接受连接，
  MinIO health 为 HTTP 200；未停止 worker 或外来进程。
- Windows 当前保留端口覆盖旧的 `2222/2223`。新增只负责 SSH 连接、不运行 `nvidia-smi` 的本地 watchdog，
  用 `32222` 连接 `.19`、`32223` 连接 `.32`。容量监控仍逐卡显式调用；`.32` 只检查 GPU0/GPU1，
  GPU2/GPU3 从未访问或探测。
- 恢复后的快照中 `.19 GPU0--GPU7` 与 `.32 GPU0/GPU1` 全部可观测，但每张卡均有计算进程、显式
  device 声明或高利用率，因此空闲集合仍为空。控制器现在区分 `unreachable` 与 `busy`；连接恢复不等于
  可用容量，也不触发 formal science workflow。

### 21.44 2026-08-17 v38 全序列准入证据闭环

- v38 control worker 新增 `evaluate_v38_sequence_admission` 与
  `persist_v38_sequence_admission`。前者从 PostgreSQL 读取本 run 全部唯一候选和逐候选 11 项声明评价，
  缺失、重复或失败证据 fail closed；后者从 content-addressed object store 解析紧凑引用、重新按数据库
  证据计算并逐字节比对，再持久化 ToolCall、Artifact、AgentDecision 及其输入/输出 evidence edges。
- 排序稳定性不引入新的外部数值阈值：对冻结的 MIC、活性、毒性风险、溶血风险和可开发性非加权
  Pareto 轴执行完整集合及逐轴 leave-one-objective-out 前沿成员复算。稳定性不足只进入
  `promising_uncertain`/refinement 证据，不得降低毒性与溶血安全门，也不得为了填满结构预算放行。
- admission artifact 同时包含成熟核心、探索 lane、拒绝分母、结构是否允许 dispatch，以及在核心不足时
  绑定 provider task/context-pack、父本序列和未改父本对照的最多三轮 refinement plan。该里程碑只完成
  序列阶段 evidence closure 与 worker 注册；正式 science workflow、immutable worker release 和双靶点
  结构编排尚未完成，因此 control run 持久化科学计数仍保持为 0。

### 21.45 2026-08-17 v38 序列优先 Temporal 前缀

- 新增 `V38SequenceFirstAgentWorkflow`，以唯一提交 preflight 为入口，严格执行 9 个冻结 generator cell，
  一次性持久化 900 个 raw occurrence 及全部有效唯一候选，再让五类 metric plugin 覆盖每个候选的完整
  11 项声明评价。workflow 在 runtime 同时核对候选投影数和最终 Evaluation 数，禁止 first-K 截断或漏跑
  某一类打分器。
- 五类评价闭合后 workflow 才调度 sequence admission：数据库证据被重新计算并写入 content-addressed
  artifact、ToolCall、AgentDecision 和 typed edges。输出明确区分
  `sequence_refinement_required` 与 `sequence_admitted_for_multitarget_structure`；本里程碑不会把序列前缀
  完成伪报为结构或正式科学 workflow 完成。
- control worker 已注册该 workflow。refinement 子代执行、双靶点 Boltz/Rosetta 隔离分支、最终 target-agnostic/
  per-target/cross-target Pareto 与 database/object-store replay 仍是下一关键闭环；这些完成并冻结 immutable
  worker release 与 exact-once preflight 前，formal science workflow 继续保持未提交。

### 21.46 2026-08-17 v38 refinement 子代持久化门

- 新增 `persist_v38_refinement_children`，严格消费独立 producer 的冻结输出，不在 AMPgent 内实现知识 provider
  兼容层。每个子代必须通过 `RefinementChildProposal`：属于预注册 parent、轮次一致、序列真实变化且为合法
  10--25 aa 短肽、至少一条 adopted knowledge trace，并与未改父本 control SHA 精确绑定；每个 work order
  的子代数量必须逐项完整覆盖，不能静默少产或跨 parent 填补。
- activity 原子持久化 refinement ToolCall、content-addressed 原始输出/plan/parent-control artifact、父 ToolCall
  dependency、全部 child occurrence、唯一新 Candidate 和 run lifecycle event。重复子序列保留 occurrence 分母但
  不复制 Candidate；新子代标记 `score_all_sequence_metrics_required=true`，结构在重新完成全部 11 项评价并
  readmission 前保持禁止。
- `persist_v38_sequence_metric` 现允许对一个明确的 run 内候选子集执行完整 plugin 评价，因此 refinement 新子代
  可逐轮全量重评；它仍拒绝跨 run、未知 candidate 或 plugin 内候选覆盖不完整。当前仍缺独立、冻结、可审计的
  refinement producer activity，故不会用未声明的本地启发式编辑器伪造知识卡生成能力。

### 21.47 2026-08-18 v38 provider-owned refinement 编排闭环

- `V38SequenceFirstAgentWorkflow` 现要求 preflight 冻结唯一的 refinement provider Activity 名、task queue、
  provider task、release revision 和 runtime manifest SHA。AMPgent 只调度该 provider-owned Activity 并验证
  输出，不用本地规则或兼容层冒充知识生成器；provider task/context-pack 身份随每个 work order 传递。
- 当首轮 admission 阻止结构 dispatch 时，workflow 读取 admission artifact 内的精确 refinement plan，调用
  provider，原子持久化全部子代 occurrence 和父子谱系，再仅对新唯一子代执行五类 plugin、11 项指标的完整
  score-all。随后基于数据库中父本与子代的完整证据重新 admission；最多三轮，安全门不降、父本保留、不得
  强制填满。若一轮只产生重复子代，流程停止为 `sequence_evidence_concluded_without_structure`，不会空转。
- consumer 校验进一步要求子代回报的 parent sequence 与 work order 完全相同，且每条 knowledge trace 必须
  指向该 task 冻结的 provider task。当前代码已闭合 Temporal 编排协议，但 provider-owned Activity 的独立
  release、poller、真实 smoke 和全链路 immutable worker release/preflight 尚未完成；因此 formal science
  workflow 仍保持未提交。

### 21.48 2026-08-18 v38 provider 交付门与阶段计数修正

- 现有 knowledge runtime 只有只读 `context-pack`/`verify` invocation，没有产生 refinement 子代的 provider-owned
  Activity；因此不得把已有 AceA context-pack adapter 误报为序列编辑器。新增
  `config/experiments/v38_refinement_provider_acceptance.yaml`，将 provider task、独立 Temporal Activity/queue、
  request/response、逐子代 knowledge trace、immutable release、真实 poller/smoke 与取消清理要求写成正式验收门。
  状态保持 `requested_not_delivered`，交付并通过只读验收前禁止正式 science submission。
- 控制器默认 sequence-metrics durable target 从错误的 8100 修正为 `900×11=9900`。这是阶段推进正确性修复：
  任一候选缺少双 MIC/活性、毒性、溶血或理化指标时都不能提前进入 admission/structure；refinement 新子代仍按
  实际唯一数量额外增加 `11×N` Evaluation，不纳入初始 9900 的静态分母。
