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

- 冻结交付1900；三分支严格库87,989；历史 challenger 147,161候选/735,805证据，不改写。
- Pool A archive 486个靶点内80/80家族elite：AceA79、GyrA100、PBP2a53、VEGFA71、FGF2 81、ANGPT1 102；严格过门候选498，同家族非elite不属于archive；全部结构与decoy远端保留。
- `.19` GPU0–7运行首批475条；synth `.2` GPU1运行新增11条；两队列身份无交集，统一总数486。已启动29条、生产MD完成15条、MM/GBSA完成入库13条、全证据canonical完成12条、其余未启动457条；GyrA `0b71cf1c` 已完成MM/GBSA与899残基分解并入库、界面分析待完成，另一条GyrA继续后处理；VEGFA完成后GPU自动补位ANGPT1；失败0、跨报告一致性错误0。AceA现有8条中Rosetta dG与MD RMSD/MMGBSA秩相关仅0.262/0.238，MD保持独立必经门。非正式smoke不计数。5条靶点内暂定Pool S前沿在MM/GBSA均值与95%CI上界定义下成员一致。两端均为单best-decoy、1 ns NPT+50 ns NVT、checkpoint续算；`.2` 紧凑证据转交`.19`，PDB/DCD/checkpoint只留远端。
- `.32 GPU0`有外来声明不抢占；GPU1虽空闲但当前MD已占满资源上限，不重复派发；GPU2/3禁止。
- 生成来源：PepGLAD三靶点严格库87,989条/8,657家族，61,914条(70.4%)获至少双活动模型支持；target-conditioned PepMLM六靶点24,576/24,576完成12项、12,151条(49.4%)过展示门、890条优选子集完成challenger；PepFlow AceA model2真机8/8完成12项与challenger并落库至run `de80b78e-ae0d-5190-93cc-11ee5dee62a2`，5条(62.5%)过展示门、0条过活动/QD门，下一批优先改善活动条件化而不降低硬门；首批A池475/475按PostgreSQL精确审计均无唯一显式来源，不按序列归因。

## 维护

只更新本文件；删除过时状态；不追加流水账。精确运行明细只写 PostgreSQL 与 JSON/CSV 收据。
