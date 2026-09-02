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
- Rosetta：每 complex `5 decoy`；以全部 5 个 `dG_separated` 中位数判定；已有 20/200-decoy 结果保留，未完成任务从现有 checkpoint 补到 5，不重算、不删除。
- Pool S：Pool A 后再经 MD；当前不启动新 MD。
- 计算预测不等于活性、安全、亲和力或药效。

## 闭环

1. 固定 QD behavior space：`[net_charge/L, hydrophobic_ratio_modlamp, alpha-helix hydrophobic_moment, L]`；v1 固定 2,160 cells，实验中边界不变。
2. 仅展示门通过、Macrel low/概率 `<=0.5`、校准活动模型支持 `>=2` 的候选参与 coverage；每 cell 仅留活动 percentile 均值最高者。
3. QD elites 与多前沿 archive 选亲；上一代已评分候选须作为输入并入父本，historical 仅排重；de-novo 保留全部质量合格 QD families，仅在同一 family 内优先三模型全支持代表，学习 family-balanced 残基/一阶转移先验；执行点突变、受控杂交、de-novo。
4. 分开报告 best/mean quality、valid-cell coverage、QD-score、最大 cell concentration、archive-relative novelty；新占格、格内替换、同格冲突与 operator `Δϕ` 独立记账，禁止 `q+λD`。
5. 全局序列去重；保留 occurrence；不改写历史 run。
6. 运行 12 项 score-all、活动模型校准、challenger shadow，写父子差值/QD archive/replay/PostgreSQL。
7. 缺失且未运行的有靶点候选进入 Rosetta 5-decoy 队列；按硬门、dG、Pareto、QD 填 Pool A 50。

Challenger 证据键为 `run_id + candidate_id + model_release_key`；字段为 `evidence_role`、`evidence_family`、`model_release_key`、`applicability_status`、`conflict_status`、value/unit/OOD/limitations/`tool_call_id`。三模型独立保存，不跨 run 按序列合并，不计加权总分。

## 资源

- `.19 GPU0-7`：获准；每次检查 PID/owner/显存/利用率/声明。
- synth `.2`：获准；仅实时空闲卡。
- `.32 GPU2/GPU3`：只读、零调度；GPU0/GPU1 仅实时证明可用后调度。
- Pool A 补齐为最高优先级；资源竞争时可暂停未完成的 Rosetta 后续计算，已完成结构、decoy、收据与 checkpoint 必须保留并续算。
- 不停止外来任务；只控制精确 AMPgent PID；任务 exact-once、可恢复。
- 密码仅外部凭据存储；不进入文档、Git、命令参数、日志。

## 证据

- 只保留影响科学结论、身份、重放或资源安全的门。
- SHA 仅用于对象寻址、批次身份、幂等；不重复人工复核、不作里程碑。
- PostgreSQL 普通协议/超时差异直接重试或放宽窗口；序列身份、去重、历史不可变、资源禁区不可放宽。

## 当前状态

- 七分支冻结交付：1900 条全局唯一，正式 run 不变。
- 三分支严格库：87,989 条；AceA 29,190、GyrA 30,579、PBP2a 28,220。
- VEGFA round139/141/142/144/146 均保留；round146 run `d3ab0200-5818-517e-aa17-2b7036200c7e`：768 条、13,056 评价、198 条校准优秀、HemoPI2 619/149，QD gain 2，close `7c24eb5e-1b81-5d40-93ab-351d6897b246`；累计严格独立家族 ≥100，精确重放 0。
- AceA round140/143/145 均保留；round145 run `4288f009-d188-5d32-bf2f-1bf62c1a2855`：768 条、13,056 评价、156 条校准优秀、HemoPI2 623/145，QD gain 3，close `d9be25dd-8aaa-58bd-b32e-73559edd66d5`；累计严格独立家族 ≥100，精确重放 0。
- GyrA round137：run `2998677d-7061-5f76-bb6e-1a6d4a009700`；768 条、13,056 条评价、474 条校准优秀、419 条新家族；HemoPI2 无冲突 561、分歧保留 207；QD 87/2,160 cells，本轮新占 8，QD-score 80.9835；全支持子集提高活动支持但降低展示率，促成 family 内优先 v10；close run `9fb34332-85b3-5990-ac23-d063ce9dbd3c`。
- PBP2a round138：run `26cb1ff7-144e-5e33-892d-0ac012954716`；768 条、13,056 条评价、349 条校准优秀、398 条新家族，历史精确重放 0；HemoPI2 无冲突 657、分歧保留 111；QD 82/2,160 cells，本轮新占 5、格内替换 25，QD-score 73.3105；family 内全支持优先 v10 保持覆盖但 PBP2a de-novo 展示/活动支持/质量率 65.1%/60.7%/31.8%，相对 round131 基本中性；close run `62e3e289-a6ed-50c7-ba6a-697f7332e0bc`。
- ANGPT1 round135：run `9bcbda95-56a0-52b0-80ba-296fc22a0e4e`；768 条、13,056 条评价、373 条校准优秀、397 条新家族；HemoPI2 无冲突 596、分歧保留 172；QD 79/2,160 cells，本轮新占 16，QD-score 69.9886；活动支持提高但展示率下降；close run `501edd83-0521-5b2d-8b10-2af95d98ee5b`。
- FGF2 round134：run `577f08b8-b582-5142-bd8d-1990bad63f06`；768 条、13,056 条评价、440 条校准优秀、403 条新家族；HemoPI2 无冲突 552、分歧保留 216；QD 76/2,160 cells，本轮新占 8，QD-score 63.8617；de-novo 活动支持/质量率由 64.6%/38.7% 升至 70.8%/40.9%；close run `ca7f8611-64f1-5317-9f37-cdf897e37ad1`。
- 历史 challenger 回填：147,161 个候选、735,805 条证据；HemoPI2/APEX/PeptiVerse 缺失均为 0；不重复回填。
- `.19`/synth 旧 Rosetta 200-decoy 链已停止且文件保留；13 条已有 ≥20 decoy，7 条已入库。
- 旧display-only Rosetta coarse5已在283/900暂停（`.19` 165、synth 118），零删除；结果/checkpoint保留，每 complex 仅补到5。
- 严格 Pool A priority276：与旧900重叠0；AceA/GyrA/PBP2a/FGF2/ANGPT1各50、VEGFA 26，按 exact run+Candidate UUID、独立80/80家族绑定；`.19` GPU2-7 PID `1933418`，24个CPU Rosetta worker；根 `/data1/huangyueshan/pepagent/data/run-cache/rosetta-poola-priority276-coarse5-host019-gpu2-7-20260902-v1/`；旧结果和checkpoint不变。
- v2 append22：21完成并入库、1次Boltz OOM；父PID `2335217`已结束，失败与批次收据已冻结；VEGFA已满50，暂不抢占未满靶点资源重试。
- priority v3：300条、六靶点各50、每80/80家族一个；结果/checkpoint保留，成员优先级已由 v6 取代。
- v4 reserve100：515个独立家族；差集206在synth GPU2/3 PID `2537984`，根 `/sdd_data/pepagent/ampgent/structure/rosetta-poola-v4-reserve100-diff206-synth-gpu2-3-20260902-v1/`。
- v5 reserve100：582个独立家族；差集75在synth GPU1/6 PID `2748958`，根 `/sdd_data/pepagent/ampgent/structure/rosetta-poola-v5-reserve100-diff75-synth-gpu1-6-20260902-v1/`。
- v6 reserve100：六靶点各100家族；扣除v1-v5共595个完成/在途/排队身份后新增53（AceA 33、VEGFA 20），在`.19` GPU0/1 PID `2157401`、12个CPU worker，根 `/data1/huangyueshan/pepagent/data/run-cache/rosetta-poola-v6-reserve100-diff53-host019-gpu0-1-20260902-v1/`；当前唯一最高优先级结构清单。
- PBP2a v7：第101–159名59个独立家族，经87,670条远端队列身份扫描重叠0；`.19` GPU0/1 PID `2426846`、ingester `2428077`、12个CPU worker；根 `/data1/huangyueshan/pepagent/data/run-cache/rosetta-poola-v7-pbp2a-extension59-host019-gpu0-1-20260902-v1/`；v6未重启、未覆盖。
- PostgreSQL 严格 Pool A（2026-09-02 21:13）：AceA45、GyrA15、PBP2a12、VEGFA50、FGF2 24、ANGPT1 24；其余 reserve 正在补齐 5-decoy Rosetta 与收据入库。
- synth 仅流式回传 completion receipt 与分数 JSON 到 `.19` 做身份、哈希、聚合、冲突检查及 exact-once 入库；结构不传输。
- 本地悬浮进度每 30 秒读取两端 `coarse5_progress.json`；状态为 `var/state/ampgent-rosetta-progress-float.json`。
- 瓶颈：六靶点 Pool A 完整 Rosetta 粗筛收据不足 50/靶点。

## 维护

只更新本文件；删除过时状态；不追加流水账。精确运行明细只写 PostgreSQL 与 JSON/CSV 收据。
