# AMPgent

唯一项目状态/规则文档。科学结论以冻结配置、PostgreSQL、Temporal、对象存储和远端实况为准；本文件只保留稳定规则与最新紧凑状态，实验明细以对应 JSON/CSV/receipt 为准。

## 目标与架构

AceA、GyrA、PBP2a、VEGFA、FGF2、ANGPT1 各以 50 条 Pool A 短肽作为资源均衡线；50 不是上限，所有合格新增结果持续保留。无湿实验结论。

`PepMLM(target-conditioned)/PepGLAD/PepFlow/archive → 生成/编辑/杂交 → 12 项 score-all → 校准/challenger → 固定 QD archive/lineage → Boltz → Rosetta 5-decoy → Pool A → 1 ns NPT + 50 ns NVT → Pool S`

- PostgreSQL 是 Candidate、Evaluation、谱系、决策、运行状态的权威源；Temporal 只负责任务调度。
- 对象存储和获准远端目录保存结构、decoy、轨迹、checkpoint、日志等大对象；本机只保留代码、冻结配置和紧凑收据。
- 不按序列跨 run 猜父本；主身份使用 `run_id + authoritative candidate_id`，必要时附 `sequence_sha256`、`model_release_key`、`tool_call_id`。

## 硬门与闭环

- 展示门：ToxinPred3 `Non-Toxin`、Macrel hemolysis `low`、Guruprasad instability `<=50`。
- 疏水比例、最大连续疏水长度只作描述符；禁止用 `q+λD` 代替分项 QD 记账。
- 正式 12 项必须有限且成功；活动支持按冻结同域校准计算，`support >= 2` 才能进入质量候选。
- HemoPI2 是 challenger；冲突必须保留。APEX/PeptiVerse 缺运行时记 `runtime_unavailable/not_assessed`，不得称通过。
- QD 固定四轴 `[net_charge/L, hydrophobic_ratio_modlamp, alpha-helix hydrophobic_moment, length]`、2,160 cells；分别报告 quality、coverage、QD score、cell concentration、novelty、new-cell、replacement 和 `Δphi`。
- 只有 `display ∩ support>=2 ∩ quality/QD` 的候选可物化；历史重复只记 occurrence/rejected，不改写历史 run。目标特异候选须 5 个 Rosetta decoy 的 `dG_separated` 中位数 `< -30 REU` 才能进入 Pool A；无靶点候选按协议豁免 Rosetta。每个 complex 固定 `nstruct=5`，已有 decoy 从现数续算。
- Pool A MD 使用 single best-decoy、单常规重复；必须闭合界面 RMSD、关键接触、氢键/盐桥/水桥占有率、离位判据、MM/GBSA 均值与 95% CI、残基分解。缺项结构化标记 pending，不得冒充 full evidence。

## 数据与资源归属

- `.19 GPU0–7` 与 synth `.2` 仅在实时证明 owner、PID、声明、显存、利用率和磁盘安全后使用；现有 supervisor/runner 负责 exact-once、可恢复续排。
- `.32 GPU2/GPU3` 永久禁止计算和控制；不停止外来任务、不抢占声明占用资源、不启动无收据的裸 worker。
- 远端结构、轨迹、checkpoint、日志不下载、不删除；只同步 compact JSON/CSV/receipt。密码只经外部凭据存储，不进入文档、Git、命令行或日志。
- 每轮只提交本轮自有文件；不把 MD 零增量刷新或其他 dirty worktree 带入提交。

## 当前权威状态（截至 2026-09-04/05 可见收据）

- Pool A 紧凑盘点：498 条严格候选、486 个 80/80 families；AceA/GyrA/PBP2a/VEGFA/FGF2/ANGPT1=`79/100/53/71/81/102`。目标计数是资源均衡信息，不是容量上限。
- Pool A MD 486 identity union：`launched=61`、`md_complete=45`、`full_evidence=44`、`analysis_pending=1`、`running/incomplete=16`、`not_started=425`、`failed=0`；完整证据只计齐全指标与 PG receipt 的候选。最新已知 handles 为 `.19` supervisor/analysis `3986807/3977733`，synth successor-11 `1280301/3311802/3360111`；不得因超时或缺 PID 自动重启，先查不可变终态/继任者。
- ANGPT1 PepMLM QD-neighbor v2b：12 proposals、12/12 formal/display、冻结 witness support>=2 为 9、HemoPI2 12/12 no-conflict；固定 QD=`9 eligible / 2 new-cell / 0 replacement`。PG exact preflight 已有界执行一次但连接在候选查询前以 `WinError 1225/5` 失败，故本轮 `0 Candidate / 0 Evaluation / 0 ToolCall / 0 coarse5 / 0 Pool A`；离线 witness 不等于 PG 写入。见 `reports/angpt1_pepmlm_qd_neighbor_v2b_20260904/close_receipt.json`。
- 2026-09-05 00:15:33 CST 双机备份已完成；`.19:55434` 的恢复验证使用独立库命名空间，`.19:55433` 主库未改动。备份/恢复不是新科学 run，也不改变历史身份。

## 恢复入口

- PG 物化前先以同一输入和确定性 operation identity 做一次有界 dry-run；成功才执行一次并回读 Run/Candidate/Evaluation/ToolCall，失败写 `runtime_unavailable/blocked` 收据并停止重试。不得以本地 witness 或文件存在冒充 PG 证据。
- 已物化但缺 compact MD evidence：只用现有 approved relay/analysis lane，按 `subject_run_id + candidate_id + model_release_key + tool_call_id` 幂等补写缺项；不重跑已有分析、不下载轨迹。
- 远端隧道仅使用仓库批准的外部凭据 helper；先恢复只读观测，再核验远端 PID/owner/cmd、claim/lock、receipt 和 PG identity。禁止手写密码、ProxyJump 或并发第二实例。

## 维护

只更新本文件的稳定规则和紧凑当前状态；逐轮参数、计数、哈希和错误边界写入对应 receipt，不在此重复流水账。
