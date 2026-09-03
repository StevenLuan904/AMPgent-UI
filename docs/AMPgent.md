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
- Pool A MD：合并队列486；`.19` GPU0–7满载，MD supervisor PID `3986807`、分析PID `3977733`存活；launched33、MD20、MM/GBSA/完整界面+PG证据18、失败0、待MD466、待完整证据468。正式18条均未离位；界面RMSD均值0.297 nm、接触0.648、MM/GBSA均值-114.33 kcal/mol。详见 `reports/pool_a_md_50ns_expansion_20260903/live-summary-all-486/summary.json`。
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
- QD-gap-directed reciprocal micrograft v2：VEGFA/FGF2 各16 proposals，formal12/display=16/16、16/16；正式 QD eligible=2/9，均为 incumbent replacement，new-cell=0；HemoPI2 reviewed=16/16，no-conflict=10/0，APEX/PeptiVerse 为 `runtime_unavailable` shadow。exact-once materialization 各16 Candidate、192 formal Evaluation、272总 Evaluation；结构队列严格 VEGFA2/FGF2 9，未提交 Rosetta/GPU/MD。扩展 scheduler manifest 至109条后 sequence/authoritative UUID=109/109唯一，unresolved/drift=0，dispatch_allowed=false；CSV hash=`0c9e15a234cf9695e286e65b936ff0828ae989c0ec118cc03df35a09aff7b74b`。
- QD-gap-directed reciprocal micrograft v3：VEGFA/FGF2 各16 proposals，descriptor-exact preflight target-cell-hit=16/16；formal12=16/16、display=14/15，QD eligible=1/12，post-gate new-cell=1/7、replacement=0/0，未将预检命中冒充 coverage。HemoPI2 reviewed=16/16，no-conflict=6/4；exact-once materialization 各16 Candidate、192 formal Evaluation、272总 Evaluation；结构队列严格 VEGFA1/FGF2 12。scheduler manifest 合并至122条，sequence/authoritative UUID=122/122唯一，unresolved/drift=0，dispatch_allowed=false；CSV hash=`d8d0a3bb138be30006aa41e0b04d177384dc13b31163bff9ddfcb8b7beb565e8`。
- VEGFA dual-arm QD-gap v4：A=PepMLM、B=PepGLAD/PepFlow，各16 proposals，formal12=32/32、display A/B=13/14，support≥2 A/B=1/0；preflight target-cell-hit=32/32，正式 QD eligible/new-cell/replacement=1/1/0（未按预检命中冒充 coverage）。HemoPI2=32 reviewed、17 no-conflict；exact-once materialization=32 Candidate、384 formal Evaluation、544总 Evaluation；结构队列1。scheduler manifest=123条，sequence/authoritative UUID=123/123唯一，unresolved/drift=0，dispatch_allowed=false。远端只读核验（2026-09-03 10:30:34Z）确认 `.19` supervisor PID `3986807` 与分析 PID `3977733` 均存活；GPU0–7 compute PID 表已读到8个 OpenMM 进程。该轮未重启MD。远端日志/checkpoint最新mtime为 `angpt1/1064e16d.../production.log/.chk`（Unix mtime `1788431434.13`）；本地权威汇总仍为 launched=33、MD complete=20、interface/MMGBSA/PG evidence complete=18、failed=0，故状态为可观测运行中而非已停止。
- FGF2 QD-gap v5：严格复用 v3 高活动父本，A=1-aa descriptor-exact、B=2-aa 等长受控替换；PG exact 后 proposal A/B=13/4（共17），formal12=17/17、display=15/17、calibrated support≥2=17/17、excellent=15。正式 QD eligible/new-cell/replacement=15/12/0；HemoPI2 reviewed=17、no-conflict=8，APEX/PeptiVerse 为 runtime_unavailable shadow。exact-once materialization run=`1e99108f-73c1-5885-8a38-2ccc17de64af`，17 Candidate、204 formal Evaluation、289 total Evaluation；结构队列15。scheduler manifest=138条，UUID resolved/unresolved/drift=138/0/0，dispatch_allowed=false；未启动 GPU/Rosetta/MD。MD durable snapshot 仍为 launched=33、MD complete=20、interface/MMGBSA/PG evidence complete=18，本轮无新增增量。
- Cross-target QD-gap v6：沿用 FGF2 v5 的 1-aa descriptor-exact adjacent-empty-cell 算子，ANGPT1/AceA/PBP2a/GyrA 实际 proposal=10/8/7/7，共32；formal12=32/32，display=10/8/7/6，calibrated support≥2=4/1/7/5，QD eligible/new-cell/replacement=4/4/0、1/1/0、7/7/0、4/4/0。四个独立 PG run 分别为 `bc1cb537-543a-507a-b832-4378aeb46b9b`、`37a01b90-b23b-5aac-84bb-6e68ef693800`、`2b7498d4-112f-519e-b3a8-c48c12ecc3af`、`6af5ca2e-9929-5f8d-9532-f2324deb6db5`；共32 Candidate、544 Evaluation，结构队列4/1/7/4。scheduler manifest=154条，resolved/unresolved/drift=154/0/0，dispatch_allowed=false；未启动 GPU/Rosetta/MD。结束前 MD durable summary 仍为 launched=33、MD complete=20、interface/MMGBSA/PG evidence complete=18，无新增 delta。
- QD/new-family block-graft v7：PBP2a/GyrA 各上限16，严格使用 display+formal12+support≥2+QD elite 父本与 PepFlow/PepGLAD display-safe donor。PBP2a 预检产生5条 PG-new/新80/80 family/空 cell proposals，GyrA为0（24 donors可用但 3–4 aa 受控片段无同时满足新 family 与空 cell 的候选）；PBP2a score-all=5/5、display=5、校准support≥2=3、excellent=3、HemoPI2=5/5、QD eligible/new-cell/replacement=3/3/0。deterministic run=`fb6eb9ee-31c4-5bf8-83d7-404cd15648d6` 已由 PG 确认 `succeeded`，ToolCall/Candidate/Evaluation=1/5/85，未重复物化；结构队列严格3条。队列导出按 run+sequence_sha256 解析 authoritative Candidate UUID，proposal 仅保留 `source_proposal_id`。未启动 GPU/Rosetta/MD。
- v7 恢复与 scheduler：55432 监听 PID `34840` 为既有 SSH 隧道（`-p 32222 TargetServerDirect`），未恢复/重启；一次短暂 asyncpg 超时后 PG 查询成功，identity 状态为完整而非0/部分。合并 scheduler manifest=157条，global sequence/authoritative UUID=157/157唯一，unresolved/drift=0，active/completed=0/0，new_ready=157；active GPU=8、新调度上限=0，`dispatch_allowed=false`，未提交任务。manifest CSV hash=`ed40e0518cc239419ce171806ce778d6b866a347c557c928898e14d5c04314f5`；v7 队列 hash=`f76edca7bd3c7f5cf99a30dcb70d1d223fb63e04ecedddcacb956e52a23756a6`。
- MD 一次只读核验（2026-09-03 11:50:05Z）：.19 PID `3977733` 分析 supervisor 与 `3986807` idle supervisor 均存活，最新远端 production/checkpoint mtime 为 Unix `1788436204.2548380850`；状态为可观测运行中，未启动/恢复 MD。现有 durable 汇总仍为 launched=33、MD complete=20、interface/MMGBSA/PG evidence complete=18。
- 结构调度资源审计（2026-09-03 11:55:57Z）：157 条 manifest 的 PG exact identity=157/0/0，active/pending task-key 命中=0；但全局 `dispatch_allowed=false`。实时快照显示 .19 GPU0–7 均为 AMPgent OpenMM、.2 GPU0/1 已被占用，.32 GPU0/1 为外来 prima3d，.32 GPU2/3 永久禁用；未提交结构任务、未控制外来进程。一次远端只读吞吐诊断见 9 个 compute、30 个 production checkpoint/log、46 个分析产物，最新 mtime `1788436556.9398504620`，表明 MD 仍有 durable 进展；详见 `reports/ampgent_structure_dispatch_capacity_audit_20260903.json`。
- target-agnostic source-graft v8：固定 PepFlow16 + PepGLAD raw-fragment16，共32条；PepGLAD donor 明确 `donor_display_eligible=false`，但最终 child 门独立判定。正式 score-all/formal12=32/32，display=27，校准 support≥2=0；HemoPI2 challenger=32/32（no-conflict=6），APEX/PeptiVerse 为 `runtime_unavailable` shadow。固定2160-cell QD=eligible/new-cell/replacement 0/0/0。PG exact 按8个4-hash批次完成，historical_exact=0、PG-new=32；deterministic target-agnostic run=`5ff373b0-98c4-5f7e-a146-575a3b1369dc` 已 exact-once 物化32 Candidate、544 Evaluation（每条12 primary+3 HemoPI2+2 shadow），但 excellent/QD/Pool A=0；32条保留在 raw pending manifest，不进入结构队列。未启动 GPU/Rosetta/MD。
- target-agnostic v9：针对 v8 的 0/32 support 诊断，固定 PepFlow16 + PepGLAD16，使用24个 PepFlow display-safe/support≥2 父本，改为单残基低扰动 descriptor 变更；v8 父子三模型/四维差值匹配32/32，family novelty 仅报告。v9 score-all/formal12=32/32、display=30；target-agnostic archive empirical-CDF 校准 support≥2=0、excellent=0；HemoPI2/shadow=32/32，APEX/PeptiVerse=`runtime_unavailable`。固定QD eligible/new-cell/replacement=0/0/0。PG exact=8×4批次、historical_exact=0、PG-new=32；deterministic run=`e4554feb-bab4-5c39-974b-2927463a9709` exact-once 物化32 Candidate、544 Evaluation，Pool A/结构队列=0；未启动 GPU/Rosetta/MD。详见 `reports/target_agnostic_source_graft_v9_20260903/`。
- target-agnostic v10 前置诊断：v9 实际使用16个父本，全部来自 `acea` branch，原语义为 `exact_parent_empirical_cdf`；与 target-agnostic archive 的冻结语义逐项重算后 branch/calibration-domain/support mismatch=16/16/16，display+support≥2 同域父本=0/16。因此 v10 停止生成，不制造子代；需先修复/补齐同域校准 witness。详见 `reports/target_agnostic_v10_diagnostic_20260903/mismatch_matrix.json`。
- target-agnostic v10 witness 修复与配对轮：从 PostgreSQL/同域历史 cohort 冻结 17,087 条 witness，primary release=`ampgent_formal12_frozen`，AMP-READ/LLAMP=`minimize`、Macrel=`maximize`、p75 阈值；display+support≥2=31，冻结父本16。v10 PepFlow/PepGLAD 各16 child，score-all/formal12=32/32、display=27、support≥2=15；HemoPI2/shadow=32/32，APEX/PeptiVerse=`runtime_unavailable`。PG exact=32 PG-new；QD quality-eligible/new-cell/replacement=11/0/8。8条 replacement 已逐条核验 display+support≥2 且 child quality 高于 incumbent，按 archive-contribution 口径无靶点 Pool A 新增=8；其余3条仅保留 raw/QD 同格审计。deterministic materialization run=`4dbf4904-1995-527f-890b-9b5de1f277ce`，32 Candidate、544 Evaluation；controls 仅复用既有同域 PG identity，不重复 Candidate。
- target-agnostic v11：基于正确同域 witness 做固定 archive 空 cell 定向，预检命中32/32（PepFlow16、PepGLAD16；单残基25、双残基7）。正式 score-all/formal12=32/32、display=6、同域 support≥2=26；HemoPI2/shadow=32/32，APEX/PeptiVerse=`runtime_unavailable`。PG exact=历史重复1、PG-new31；正式 QD eligible/new-cell/replacement=3/0/2。2条 replacement 构成无靶点 Pool A archive-contribution 新增，另1条 eligible 仅保留同格 raw/QD 审计；历史重复不重写。materialization run=`1e4443f1-1c66-5760-867d-2645e4e88765`，31 Candidate、527 Evaluation；结构队列=0，未启动 GPU/Rosetta/MD。
- target-agnostic v12 safety/stability rescue：对 v10/v11 中27条 support≥2、display=false 父本按失败门定向编辑；失败组合为 Macrel hemolysis 22、hemolysis+Guruprasad>50 3、ToxinPred3 1，生成26条唯一 child。正式 full12=26/26、display=5、同域 support≥2=15；HemoPI2/shadow=26/26。PG exact=历史重复0、PG-new26；QD eligible/new-cell/replacement=1/0/0，Pool A 新增=0。deterministic run=`70fe41b6-015b-5cc4-b38a-b63947bba655`，26 Candidate、442 Evaluation；后续重试仅触发 append-only receipt 保护，未创建第二 run。未启动 GPU/Rosetta/MD。
- target-agnostic v13：先汇总 v10–v12，确认 1-aa micrograft 是唯一有历史有效贡献的算子（10），排除 2-aa 与 v12 定向 rescue 失败规则；基于31个同域 display+support≥2 父本生成 PepFlow/PepGLAD 各16条保守1-aa child。正式 score-all/full12=32/32、display=22、support≥2=19；HemoPI2=32/32，APEX/PeptiVerse=`runtime_unavailable` shadow。PG exact=历史重复0、PG-new32；固定QD quality-eligible=13，其中有效 archive contribution 为 replacement=10、new-cell=0，另3条同格非elite仅保留 raw/QD 审计；target-agnostic Pool A 累计按 archive contribution 为20（含v10+8、v11+2、v13+10）。deterministic materialization run=`8606dd93-5364-5ca2-ab95-b5de2f73bda9`，32 Candidate、544 Evaluation、ToolCall=1；scheduler 合并 manifest=167条，authoritative UUID resolved/unresolved/drift=167/0/0，active/completed=0/0，dispatch_allowed=false，CSV SHA=`93ccc6d5d7262a12426ee22b340211c35a1dacebf72983370ff62ba3c588fdd7`。未启动 GPU/Rosetta/MD。
- target-agnostic v14：基于 v10–v13 operator outcome 表，保留已产生 display/support 与 replacement 的保守1-aa规则，排除2-aa及v12手工救援；PepFlow/PepGLAD各16、PG-new32，正式 full12=32/32、display=22、support≥2=26。固定QD eligible=19，其中有效 replacement=13、new-cell=0，6条同格非elite仅审计；Pool A archive contribution 累计=33（v10+8、v11+2、v13+10、v14+13）。deterministic materialization run=`ea1b7e04-8d9d-54cf-91e7-e36f21dbdfd2`，32 Candidate、544 Evaluation、ToolCall=1；scheduler manifest=180条，UUID resolved/unresolved/drift=180/0/0，target_agnostic 23条均 `rosetta_required=false`、`exemption_reason=target_agnostic`，不得进入结构调度，dispatch_allowed=false，CSV SHA=`b79718b4051a7cde51544c8ac9e965638110cd5435bd5ba4253a5df4b31f7dae`。
- target-agnostic v15：v10–v14 outcome 学习表显示保守1-aa replacement 累计33，2-aa与v12 rescue均0；排除全部已试 parent/position/to 三元组后生成 PepFlow/PepGLAD各16。正式 full12=32/32、display=22、support≥2=22；HemoPI2=32/32，APEX/PeptiVerse=`runtime_unavailable` shadow。PG exact=历史重复0、PG-new32；QD 有效 replacement=9、new-cell=0，6条同格非elite仅审计。deterministic materialization run=`7519f976-6b0d-5a4d-a665-e4bab9a21e15`，32 Candidate、544 Evaluation、ToolCall=1；Pool A archive contribution 累计=42。scheduler manifest=189条，UUID resolved/unresolved/drift=189/0/0，target-agnostic 32条均 `rosetta_required=false`、`exemption_reason=target_agnostic`，仅不可调度审计行，dispatch_allowed=false。CSV SHA=`e4e6239dcd7a3f3954d557ff6b81fd8bb94411328609a3af0ec9ac932f894fd5`。
- target-agnostic v16：PepFlow/PepGLAD各16；full12=32、display=26、support≥2=22、QD replacement=13。PG run=`30778ba0-a7a2-5f4d-aaa5-fb4158e0ac70`，32 Candidate/544 Evaluation；无靶点 Pool A 累计55并停止扩增，`rosetta_required=false`。六个有靶点 Pool A 已完成5-decoy且 `primary_dg<-30`：AceA79、PBP2a53、VEGFA71、FGF2 81、GyrA100、ANGPT1 102。
- Pool A MD（2026-09-04 00:50 CST）：`.19` 主475与synth successor11按`run_id+candidate_id`并集486、重复0，均为单best-decoy、1 ns NPT+50 ns NVT、单重复并自动补位。合计 launched=44、全证据=28、running=16、未启动=455、失败=0、待全证据=458；`.19` PID `3986807/3977733`，synth supervisor `1280301` 及分析 `3311802/3360111` 均存活。synth 受控handoff保留GPU1 runner `58820`，新增GPU2/3 runners `1280316/1280381`；GPU5/7因CUDA声明未使用。全证据含interface RMSD、contacts、氢键/盐桥/水桥、离位、MM/GBSA均值+95% CI、残基分解与PG receipts；远端数据不删，不触碰`.32 GPU2/3`。明细：`reports/pool_a_md_50ns_expansion_20260903/md_live_queue_audit_20260903.json`。
- Pool A MD 紧凑证据同步（2026-09-04）：`.19`复制63项、synth复制10项，均限于JSON/CSV/receipt；未复制PDB/DCD/CHK/log，未删除远端文件。486汇总刷新为 launched=42、MD/interface/MMGBSA=28；本地PG receipt-confirmed=26，verifier明确2条successor的PG marker尚未进入本地compact汇总，故不将28冒充PG闭合；PG relay为2候选、30 evaluations、failures=0。明细：`reports/pool_a_md_50ns_expansion_20260903/synth-successor-11/postgresql_ingest_compact_receipt.json`。
- MD relay 自动化（2026-09-04）：relay 成功读取 synth ingester state 后，按 `subject_run_id+candidate_id+model_release_key+tool_call_id` 将本地 compact interface/mmgbsa receipt 原子写入；同内容幂等，路径/身份/内容漂移拒绝，不拉取结构、轨迹、checkpoint，不重复本轮数据库 relay。helper 与 fixture 定向测试已覆盖。
- PepFlow AceA 额外来源增量（2026-09-04）：既有1条 display/formal12 候选经 PG exact 确认为 PG-new，materializer 单次写入 run=`a47b2adf-a2b0-5208-b708-aa9c695fb4c7`、Candidate=1、Evaluation=17、ToolCall=`f998464e-bfee-4bc8-8a9a-58b57e250d9b`；authoritative candidate UUID=`3180e69f-701d-429d-94d6-1067f62e670f`。HemoPI2=no_conflict、APEX/PeptiVerse 为 runtime_unavailable；固定 QD 判定 support=0、quality_gate_failed，Pool A/Rosetta=0，未提交结构任务。收据：`reports/pepflow_acea_activity_rescue_charge_ladder_20260903/source_candidate_increment_receipt_20260904.json`。
- PepMLM AceA 活性导向增量（2026-09-04）：既有候选经 PG exact 绑定 run=`7a0f596a-4959-5756-b110-e7c489c5c183`、authoritative Candidate=`bc91b75a-0922-48aa-82f5-b7ff4ac3c024`、ToolCall=`ca9a3d86-0edf-4317-a7de-a6ee8a9a0757`；同一旧 root 下 CPU-only coarse5 已完成5/5，dG=`7.83664,13.10603,-45.94627,-41.84124,-37.85593` REU，median=`-37.85593 < -30`。PG receipt-ingest ToolCall=`28308f9f-18b9-4ba6-ae74-4550c2eab642` 写入2项 Rosetta 评价，候选幂等加入 targeted Pool A；现有 supervisor snapshot 不支持动态追加，已在 synth 建立 successor-12 prepared receipt，引用远端 best-decoy，未创建 claim、未提交/未启动 MD。详见 `reports/autoresearch_lineage_round102_acea_pepmlm_denovo_fullscore_20260901/rosetta_preflight_20260904/rosetta_final_admission_receipt_20260904.json`。
- AceA PepMLM Rosetta 入队审计（2026-09-04）：PG exact 确认上述 run/candidate 唯一且已有17条评估、结构证据0；本地无同序列完成/进行中收据，但 synth 远端已有同 target+sequence SHA 的旧 `nstruct=200` 队列（收据仍为 `running`，进度 `rosetta_succeeded=0/pending=12`，当前未见该候选进程）。.19 GPU0–7 均为 AMPgent OpenMM，synth GPU1–3 为 successor MD，GPU0/4 外来或未知、GPU5–7 有外部 CUDA 声明；资源门与 exact-once 门均不通过，未新提交、未重启、不进 Pool A。详见 `reports/autoresearch_lineage_round102_acea_pepmlm_denovo_fullscore_20260901/rosetta_preflight_20260904/rosetta_pending_receipt.json`。
- AceA PepMLM coarse5 受控续算（2026-09-04）：同一旧 root/candidate 的 CPU-only `nstruct=5/parallel=1` 单实例 parent PID `1488139`、当前 child PID `1534250`（owner=`synth`，`refine_decoy_0004`）仍运行；已验证 decoy `0001–0003` 共3/5，dG=`7.83664, 13.10603, -45.94627` REU，scorefile/result/completion/failure receipt 尚无，median 未计算，未进 targeted Pool A、未启动 MD。旧200-decoy数据保留，未重启/未并发；详见 `reports/autoresearch_lineage_round102_acea_pepmlm_denovo_fullscore_20260901/rosetta_preflight_20260904/rosetta_verified_wait_receipt_20260904.json`。
## 维护

只更新本文件；删除过时状态；不追加流水账。精确运行明细只写 PostgreSQL 与 JSON/CSV 收据。
