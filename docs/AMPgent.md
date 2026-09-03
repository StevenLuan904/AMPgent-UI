# AMPgent

唯一项目文档；以用户最新指令、冻结配置、PostgreSQL、Temporal、对象存储、远端实况为准。

## 目标

AceA、GyrA、PBP2a、VEGFA、FGF2、ANGPT1 各以 50 条 Pool A 短肽作为资源均衡线；50 不是名额或容量上限，全部合格新增候选持续收录；保留多端 Pareto、家族多样性、冲突证据；无湿实验结论。

## 架构

`PepMLM(target-conditioned)/PepGLAD/PepFlow/既有archive -> AutoResearch生成/突变/杂交 -> 12项score-all -> challenger -> QD archive/lineage -> Boltz -> Rosetta 5-decoy -> Pool A -> 50 ns MD -> Pool S`

- PostgreSQL：候选、评价、谱系、决策、运行状态的权威源。
- Temporal：调度；不改变科学状态。
- 对象存储/远端目录：大对象；本机仅代码、配置、紧凑收据。
- 结构、decoy、Rosetta/MD 输出仅留获准远端；不下载、不删除。
- Observer/UI：只读 PostgreSQL 聚合。

## 硬门

- 展示：ToxinPred3 `Non-Toxin`；Macrel hemolysis `low`；Guruprasad instability `<=50`。
- 疏水比例、最大连续疏水长度仅作描述符，不设淘汰上限。
- 12 项评价必须成功、有限；challenger 冲突保留独立前沿，不冒充主门。
- 新轮次在 lineage close 前必须逐候选覆盖声明的 HemoPI2/APEX/PeptiVerse；缺 runtime 记 `runtime_unavailable`，不记通过。
- Pool A：无上限；有靶点候选须完整 Rosetta 粗筛且 `dG_separated < -30 REU`；无靶点候选豁免。
- Rosetta：每 complex `5 decoy`；以全部 5 个 `dG_separated` 中位数判定；已有 20/200-decoy 结果保留，未完成任务从现有 checkpoint 补到 5，不重算、不删除。
- Pool S：Pool A 后经完整 MD 与界面/能量分析；仅 S 候选追加独立重复。
- 计算预测不等于活性、安全、亲和力或药效。

## 闭环

1. 固定 QD behavior space：`[net_charge/L, hydrophobic_ratio_modlamp, alpha-helix hydrophobic_moment, L]`；v1 固定 2,160 cells，实验中边界不变。
2. 仅展示门通过、Macrel low/概率 `<=0.5`、校准活动模型支持 `>=2` 的候选参与 coverage；每 cell 仅留活动 percentile 均值最高者。
3. QD elites 与多前沿 archive 选亲；上一代已评分候选须作为输入并入父本，historical 仅排重；de-novo 保留全部质量合格 QD families，仅在同一 family 内优先三模型全支持代表，学习 family-balanced 残基/一阶转移先验；执行点突变、受控杂交、de-novo。
4. 分开报告 best/mean quality、valid-cell coverage、QD-score、最大 cell concentration、archive-relative novelty；新占格、格内替换、同格冲突与 operator `Δϕ` 独立记账，禁止 `q+λD`。
5. 全局序列去重；保留 occurrence；不改写历史 run。
6. 运行 12 项 score-all、活动模型校准、challenger shadow，写父子差值/QD archive/replay/PostgreSQL。
7. 缺失且未运行的有靶点候选进入 Rosetta 5-decoy 队列；计算资源优先给未达 50 的靶点，再按候选质量分配；无论靶点是否已达 50，全部过门结果均进入无上限 Pool A。
8. PepMLM 必须携带 target_key/靶点上下文并记录模型版本、生成参数与父本；与 PepGLAD/PepFlow 分来源记账，不绕过任何下游门。
9. Pool A 每候选只取 5 个 Rosetta decoy 中的 best-decoy，运行一次 `1 ns NPT + 50 ns NVT`；不做常规多 seed/多 decoy MD。输出界面 RMSD、接触/氢键/盐桥/水桥占有率、离位判据、MM/GBSA均值/分块置信区间及残基分解。

Challenger 证据键为 `run_id + candidate_id + model_release_key`；字段为 `evidence_role`、`evidence_family`、`model_release_key`、`applicability_status`、`conflict_status`、value/unit/OOD/limitations/`tool_call_id`。三模型独立保存，不跨 run 按序列合并，不计加权总分。

## 资源

- `.19 GPU0-7`：获准；每次检查 PID/owner/显存/利用率/声明。
- synth `.2`：获准；仅实时空闲卡。
- `.32 GPU2/GPU3`：只读、零调度；GPU0/GPU1 仅实时证明可用后调度。
- Pool A 为最高优先级；50 仅控制资源均衡：未达 50 的靶点优先，均达标后按候选质量与信息增益调度；达到 50 后的优质新增结果仍进入 A 池。资源竞争时可暂停未完成的 Rosetta 后续计算，已完成结构、decoy、收据与 checkpoint 必须保留并续算。
- 不停止外来任务；只控制精确 AMPgent PID；任务 exact-once、可恢复。
- 密码仅外部凭据存储；不进入文档、Git、命令参数、日志。

## 证据

- 只保留影响科学结论、身份、重放或资源安全的门。
- SHA 仅用于对象寻址、批次身份、幂等；不重复人工复核、不作里程碑。
- PostgreSQL 普通协议/超时差异直接重试或放宽窗口；序列身份、去重、历史不可变、资源禁区不可放宽。

## 当前状态

- 冻结交付1,900；PepGLAD严格库87,989/8,657 families，双活动支持61,914；PepMLM六靶点24,576完成12项，12,151过展示门；历史challenger 147,161候选/735,805证据。
- Pool A：498条严格候选、486个80/80 elite families：AceA79/GyrA100/PBP2a53/VEGFA71/FGF2 81/ANGPT1 102；远端结构/decoy全保留。
- Pool A MD：合并队列486；`.19` GPU0–7满载，MD supervisor PID `3986807`、分析PID `3977733`存活；launched32、MD/MMGBSA18、完整界面+PG证据17、失败0、待MD468、待完整证据469。正式17条均未离位；界面RMSD均值0.304 nm、接触0.642、MM/GBSA均值-110.05 kcal/mol。详见 `reports/pool_a_md_50ns_expansion_20260903/live-summary-all-486/summary.json`。
- PepFlow reciprocal 1–2 aa micrograft六靶点共192条PG新颖/full12/HemoPI2，157条过展示门、128条双活性支持、98条QD合格/待Rosetta，新增22格；均已物化（3,264 Evaluation），未提交结构。APEX/PeptiVerse=`runtime_unavailable`；192个factorized父本无法唯一映射PG Candidate，lineage edge=0，不按序列猜测。

| target | PG run | display | support>=2 | QD/new | queue |
|---|---|---:|---:|---:|---:|
| AceA | `7c17057c-cf24-57e3-b1b2-96aea38da78c` | 32 | 24 | 24/4 | 24 |
| PBP2a | `b3d942e0-9054-5905-8982-392a75fad1b1` | 29 | 30 | 27/5 | 27 |
| VEGFA | `9f1fe76d-a92a-5553-ab5b-4914f85d8b52` | 28 | 1 | 1/1 | 1 |
| FGF2 | `76bd7925-a605-54ef-b1a9-a1a0b0e2baff` | 22 | 17 | 8/4 | 8 |
| GyrA | `e880a6fa-2989-5ef9-b428-4a6ddb919283` | 25 | 32 | 25/4 | 25 |
| ANGPT1 | `195dfbb1-cf6b-5d09-bb50-c1bd14590721` | 21 | 24 | 13/4 | 13 |
- 六靶点 reciprocal micrograft scheduler manifest：AceA24/PBP2a27/VEGFA1/FGF2 8/GyrA25/ANGPT1 13，共98条；全局序列与 `run_id+authoritative_candidate_id(UUID)` 均98/98唯一，identity unresolved/drift=0，PG结构证据、Pool A精确命中及 active/pending task-key 命中均0，全部 `new_ready`。当前活动GPU 8、新调度上限0，`dispatch_allowed=false`，未提交任务。可由 `analysis/build_reciprocal_micrograft_scheduler_manifest.py` 重放；CSV hash=`e012dd83b1195104818f544a968bf35740cdd57acc3e1fda892ba057bc49b31e`，旧 hash=`baee08c17ba9f5a89ed110cce68dfdd43f221db6e31afbb9115401bbdc5ed4cc` 的差异仅来自显式 target round-robin 排序。详见 CSV 与 audit receipt。
## 维护

只更新本文件；删除过时状态；不追加流水账。精确运行明细只写 PostgreSQL 与 JSON/CSV 收据。
