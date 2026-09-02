# AMPgent

唯一项目文档；以用户最新指令、冻结配置、PostgreSQL、Temporal、对象存储、远端实况为准。

## 目标

AceA、GyrA、PBP2a、VEGFA、FGF2、ANGPT1 各获得 ≥50 条 Pool A 短肽；保留多端 Pareto、家族多样性、冲突证据；无湿实验结论。

## 架构

`AutoResearch -> QD选亲/生成/突变/杂交 -> 12项score-all -> challenger -> lineage/QD archive -> Boltz复合物 -> Rosetta粗筛 -> Pool A -> MD -> Pool S`

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
- Pool A：有靶点候选须完整 Rosetta 粗筛且 `dG_separated < -30 REU`；无靶点候选豁免。
- Rosetta：每 complex `20 decoy`；按 `reweighted_sc` 最优 10 个的 `dG_separated` 中位数；旧 200-decoy 结果兼容但不再补算。
- Pool S：Pool A 后再经 MD；当前不启动新 MD。
- 计算预测不等于活性、安全、亲和力或药效。

## 闭环

1. 固定 QD behavior space：`[net_charge/L, hydrophobic_ratio_modlamp, alpha-helix hydrophobic_moment, L]`；v1 固定 2,160 cells，实验中边界不变。
2. 仅展示门通过、Macrel low/概率 `<=0.5`、校准活动模型支持 `>=2` 的候选参与 coverage；每 cell 仅留活动 percentile 均值最高者。
3. QD elites 与多前沿 archive 选亲；de-novo v9 仅从质量合格 QD elites 学习 family-balanced 残基/一阶转移先验；执行点突变、受控杂交、de-novo。
4. 分开报告 best/mean quality、valid-cell coverage、QD-score、最大 cell concentration、archive-relative novelty；新占格、格内替换、同格冲突与 operator `Δϕ` 独立记账，禁止 `q+λD`。
5. 全局序列去重；保留 occurrence；不改写历史 run。
6. 运行 12 项 score-all、活动模型校准、challenger shadow，写父子差值/QD archive/replay/PostgreSQL。
7. 缺失且未运行的有靶点候选进入 Rosetta 20-decoy 队列；按硬门、dG、Pareto、QD 填 Pool A 50。

Challenger 证据键为 `run_id + candidate_id + model_release_key`；字段为 `evidence_role`、`evidence_family`、`model_release_key`、`applicability_status`、`conflict_status`、value/unit/OOD/limitations/`tool_call_id`。三模型独立保存，不跨 run 按序列合并，不计加权总分。

## 资源

- `.19 GPU0-7`：获准；每次检查 PID/owner/显存/利用率/声明。
- synth `.2`：获准；仅实时空闲卡。
- `.32 GPU2/GPU3`：只读、零调度；GPU0/GPU1 仅实时证明可用后调度。
- 不停止外来任务；只控制精确 AMPgent PID；任务 exact-once、可恢复。
- 密码仅外部凭据存储；不进入文档、Git、命令参数、日志。

## 证据

- 只保留影响科学结论、身份、重放或资源安全的门。
- SHA 仅用于对象寻址、批次身份、幂等；不重复人工复核、不作里程碑。
- PostgreSQL 普通协议/超时差异直接重试或放宽窗口；序列身份、去重、历史不可变、资源禁区不可放宽。

## 当前状态

- 七分支冻结交付：1900 条全局唯一，正式 run 不变。
- 三分支严格库：87,989 条；AceA 29,190、GyrA 30,579、PBP2a 28,220。
- VEGFA round128：run `f7cec85a-5a5d-5a55-a00f-d22de243457e`；1,024 条已物化、17,408 条结构化评价、65 条校准优秀、531 条新家族子代；HemoPI2 无冲突 698、分歧保留 326；1,024 个父子差值收据与 archive/replay 已关闭。
- QD v1 对 round128 只读回放：34/2,160 cells；本轮新占 10、替换 4；QD-score 28.1875；最大 cell concentration 0.1538。
- VEGFA round129 QD-v1：run `2ae024a7-575e-50ce-bc33-6da061440edc`；768 条、12项全覆盖、83 条校准优秀、386 条新家族；HemoPI2 无冲突 554、分歧保留 214；QD 43/2,160 cells，本轮新占 12、替换 9，QD-score 36.4928；lineage/replay 已关闭。
- AceA round122：run `a981d696-caa8-5f8d-af07-d5344e653aaf`；1,024 条已物化、17,408 条结构化评价、269 条校准优秀、583 条新家族子代；HemoPI2 无冲突 877、分歧保留 147；覆盖漂移 0。
- GyrA round130 QD-v1：run `05b186ab-5558-57e7-a0fe-39c9bedc7dda`；768 条、13,056 条评价、350 条校准优秀、409 条新家族；HemoPI2 无冲突 584、分歧保留 184；QD 80/2,160 cells，本轮新占 9，QD-score 74.4253；de-novo 展示/质量通过率由 round123 的 50.0%/42.6% 升至 65.6%/50.8%；lineage close run `a9f9943f-fcb3-534a-92c4-0a07bf5e4f63`。
- PBP2a round124：run `561a54a3-8a59-5f3d-af9d-f2ec71d3fca7`；1,024 条已物化、17,408 条结构化评价、447 条校准优秀、549 条新家族子代；HemoPI2 无冲突 758、分歧保留 266；覆盖漂移 0。
- ANGPT1 round127：run `3bdabede-3cf4-5e33-9705-0a8174c77d85`；1,024 条已物化、17,408 条结构化评价、511 条校准优秀、516 条新家族子代；HemoPI2 无冲突 812、分歧保留 212；覆盖漂移 0。
- FGF2 round126：run `92a30242-abd7-54d2-b242-b9aca1dcbbff`；1,024 条已物化、17,408 条结构化评价、520 条校准优秀；HemoPI2 无冲突 825、分歧保留 199；覆盖漂移 0。
- 历史 challenger 回填：147,161 个候选、735,805 条证据；HemoPI2/APEX/PeptiVerse 缺失均为 0；不重复回填。
- `.19`/synth 旧 Rosetta 200-decoy 链已停止且文件保留；13 条已有 ≥20 decoy，7 条已入库。
- Pool A Rosetta 20-decoy 批次：`.19` PID `1600869`、synth PID `502155`；最后收据失败 0；GPU 跑 Boltz，Rosetta 为两台各 6 个 CPU worker；本轮 SSH 不可达，未重启、未重复提交。
- PostgreSQL 历史+当前 Rosetta `完成/dG<-30`：AceA 392/265、GyrA 12/12、PBP2a 17/16、VEGFA 17/17、FGF2 30/29、ANGPT1 16/16；仍需结合展示/模型/QD门形成最终 Pool A。
- synth 仅流式回传 completion receipt 与分数 JSON 到 `.19` 做身份、哈希、聚合、冲突检查及 exact-once 入库；结构不传输。
- 本地悬浮进度每 30 秒读取两端 `progress.json`；状态为 `var/state/ampgent-rosetta-progress-float.json`。
- 瓶颈：六靶点 Pool A 完整 Rosetta 粗筛收据不足 50/靶点。

## 维护

只更新本文件；删除过时状态；不追加流水账。精确运行明细只写 PostgreSQL 与 JSON/CSV 收据。
