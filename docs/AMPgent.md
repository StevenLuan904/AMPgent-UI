# AMPgent

唯一项目状态/规则文档。科学结论以用户最新指令、冻结配置、PostgreSQL、Temporal、对象存储和远端实况为准；实验明细以 JSON/CSV/receipt 为准。

## 目标与架构

AceA、GyrA、PBP2a、VEGFA、FGF2、ANGPT1 各以 50 条 Pool A 作为资源均衡线；50 不是上限，所有合格新增结果持续保留。无湿实验结论。

`PepMLM(target-conditioned)/PepGLAD/PepFlow/archive → 生成/编辑/杂交 → 12项 score-all → 校准/challenger → 固定 QD/lineage → Boltz → Rosetta 5-decoy → Pool A → 1 ns NPT + 50 ns NVT → Pool S`

- PostgreSQL：Candidate、Evaluation、谱系、决策、科学运行状态权威源；Temporal 仅调度。
- 对象存储/获准远端目录：结构、decoy、轨迹、checkpoint、日志；本机仅代码、冻结配置、紧凑收据。
- 身份：`run_id + authoritative candidate_id`；必要时附 `sequence_sha256/model_release_key/tool_call_id`；不按序列跨 run 猜父本。
- 证据按科学阶段而非入库时间归档：`generation→score-all→challenger→QD/lineage→Boltz→Rosetta→MD→Pool S`；后补 Rosetta/MD 追加到原 `run_id/candidate_id`，禁止复制 Candidate 或创建 evidence-only ExperimentRun。物理 CAS 可保持内容寻址，逻辑路径固定为 `runs/<run_id>/candidates/<candidate_id>/evidence/<stage>/`。

## 硬门与闭环

- 展示：ToxinPred3 `Non-Toxin`、Macrel hemolysis `low`、Guruprasad instability `<=50`；疏水比例/最大连续疏水长度仅作描述符。
- 正式12项必须有限且成功；冻结同域校准 `support>=2` 才进入质量候选。
- HemoPI2 challenger 冲突保留；APEX/PeptiVerse 缺 runtime=`runtime_unavailable/not_assessed`；不冒充通过、不加权合成。
- QD 固定 `[net_charge/L, hydrophobic_ratio_modlamp, alpha-helix hydrophobic_moment, length]`、2,160 cells；分报 quality、coverage、QD score、cell concentration、novelty、new-cell、replacement、`Δphi`；禁止 `q+λD`。
- 仅 `display ∩ support>=2 ∩ quality/QD` 可物化；全局序列去重但保留 occurrence/rejected；历史 run 不改写。
- 有靶点 Pool A：5个 `dG_separated` 中位数 `<-30 REU`；无靶点豁免。每 complex `nstruct=5`；已有20/200-decoy保留，未完成者从现数续到5。
- Pool A MD：single best-decoy、单常规重复；闭合 interface RMSD、关键接触、氢键/盐桥/水桥占有率、离位、MM/GBSA均值与95% CI、残基分解；缺项为 pending。

## 数据与资源归属

- `.19 GPU0–7` 与 synth `.2`：仅实时核验 owner/PID/声明/显存/利用率/磁盘后使用；现有 supervisor/runner exact-once、可恢复续排。
- `.32 GPU2/GPU3`：永久禁止计算/控制；不停止外来任务、不抢占声明资源、不启动无收据 worker。
- 远端结构、轨迹、checkpoint、日志不下载、不删除；仅同步 compact JSON/CSV/receipt。密码仅经外部凭据存储。
- 每次只提交本轮自有文件；MD 零增量刷新与其他 dirty worktree 不入提交。

## 当前权威状态

- Pool A：498条严格候选、486个80/80 families；AceA/GyrA/PBP2a/VEGFA/FGF2/ANGPT1=`79/100/53/71/81/102`。
- MD 486 identity：`launched=61, md_complete=45, full_evidence=44, analysis_pending=1, running=16, not_started=425, failed=0`；完整证据须齐全指标与PG receipt。最新已知 `.19` supervisor/analysis=`3986807/3977733`，synth=`1280301/3311802/3360111`；超时/缺PID不自动重启，先查终态/继任者。
- ANGPT1 PepMLM QD-neighbor v2b：12/12 formal/display、support>=2为9、HemoPI2 12/12 no-conflict、QD=`9 eligible/2 new/0 replacement`；PG连接失败，故 `0 Candidate/Evaluation/ToolCall/coarse5/Pool A`；见 `reports/angpt1_pepmlm_qd_neighbor_v2b_20260904/close_receipt.json`。
- 2026-09-05 00:15:33 CST 已完成 `.19`/synth 双机备份与 `.19:55434` 隔离恢复验证；生产 `.19:55433` 未停、未写、未切换；不构成科学 run。

## 恢复入口

- 生产：`.19 127.0.0.1:55433`。备份批次 `20260905_001533_CST`：`.19 /data1/huangyueshan/pepagent/data/migrations/postgresql/20260905_001533_CST`；synth `/sdd_data/pepagent/backups/postgresql/20260905_001533_CST`；6文件哈希/大小一致。
- 验证：`.19 127.0.0.1:55434`，实例 `.../restore_instance_v3`，库名前缀 `ampgent_restore_20260905_001533_`；5库 schema/关键计数已核对。源库 invalid index `public.ix_candidate_sequence_sha256` 明确排除。入口仅供灾备验证，worker/workflow/写流量不得切换。
- 收据：`reports/postgresql_migration_execution_receipt_20260905.json`；库存：`reports/postgresql_migration_inventory_20260905.json`。禁止删除备份、恢复实例或生产数据。
- PG物化：同输入/确定性 operation identity 有界 dry-run→单次执行→回读 Run/Candidate/Evaluation/ToolCall；失败记录状态后停止，不以本地文件冒充PG证据。
- MD补证：仅用现有 approved relay/analysis lane，按 `subject_run_id+candidate_id+model_release_key+tool_call_id` 幂等补缺；不重跑分析、不下载轨迹。
- 隧道仅用仓库批准的外部凭据 helper；先恢复只读观测，再核验 PID/owner/cmd、claim/lock、receipt、PG identity；禁止手写密码、ProxyJump、并发第二实例。

## 维护

本文仅保留稳定规则与最新紧凑状态；精确轮次、哈希、PID、候选明细留在 PostgreSQL 与收据，不追加流水账。
