# AMPgent

唯一项目文档；以用户最新指令、冻结配置、PostgreSQL、Temporal、对象存储、远端实况为准。

## 目标

AceA、GyrA、PBP2a、VEGFA、FGF2、ANGPT1 各获得 ≥50 条 Pool A 短肽；保留多端 Pareto、家族多样性、冲突证据；无湿实验结论。

## 架构

`AutoResearch -> 生成/突变/杂交 -> 12项score-all -> challenger -> lineage/archive -> Boltz复合物 -> Rosetta粗筛 -> Pool A -> MD -> Pool S`

- PostgreSQL：候选、评价、谱系、决策、运行状态的权威源。
- Temporal：调度；不改变科学状态。
- 对象存储/远端目录：大对象；本机仅代码、配置、紧凑收据。
- 结构、decoy、Rosetta/MD 输出仅留获准远端；不下载、不删除。
- Observer/UI：只读 PostgreSQL 聚合。

## 硬门

- 展示：ToxinPred3 `Non-Toxin`；Macrel hemolysis `low`；Guruprasad instability `<=50`。
- 12 项评价必须成功、有限；challenger 冲突保留独立前沿，不冒充主门。
- 新轮次在 lineage close 前必须逐候选覆盖声明的 HemoPI2/APEX/PeptiVerse；缺 runtime 记 `runtime_unavailable`，不记通过。
- Pool A：有靶点候选须完整 Rosetta 粗筛且 `dG_separated < -30 REU`；无靶点候选豁免。
- Rosetta：每 complex `20 decoy`；按 `reweighted_sc` 最优 10 个的 `dG_separated` 中位数；旧 200-decoy 结果兼容但不再补算。
- Pool S：Pool A 后再经 MD；当前不启动新 MD。
- 计算预测不等于活性、安全、亲和力或药效。

## 闭环

1. 多前沿 archive 选亲，执行点突变、受控杂交、de-novo。
2. 全局序列去重；保留 occurrence；不改写历史 run。
3. 运行 12 项 score-all、活动模型校准、challenger shadow。
4. 写入父子逐指标差值、archive、replay、PostgreSQL。
5. 缺失且未运行的有靶点候选进入 Rosetta 20-decoy 队列。
6. 按硬门、dG、Pareto、家族多样性填充各靶点 Pool A 50。

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
- VEGFA round121：run `ca18524d-5588-5d23-b03c-53b71cbfce6f`；1,024 条已物化、17,408 条结构化评价、322 条校准优秀、512 条新家族子代；HemoPI2 无冲突 826、分歧保留 198；覆盖漂移 0。
- AceA round122：run `a981d696-caa8-5f8d-af07-d5344e653aaf`；1,024 条已物化、17,408 条结构化评价、269 条校准优秀、583 条新家族子代；HemoPI2 无冲突 877、分歧保留 147；覆盖漂移 0。
- GyrA round123：run `1bf92615-ef5d-5411-90cc-ef9d362c187c`；1,024 条已物化、17,408 条结构化评价、557 条校准优秀、518 条新家族子代；HemoPI2 无冲突 723、分歧保留 301；覆盖漂移 0。
- PBP2a round118：run `5a0d046a-5d66-584c-b774-1dca3e248051`；14,655 条已物化、249,135 条结构化评价、12,884 条校准优秀；HemoPI2 无冲突 10,301、分歧保留 4,354；覆盖漂移 0。
- ANGPT1 round119：run `3ad746e4-8e2a-51f6-a9d5-840cc5b01a33`；7,119 条已物化、121,023 条结构化评价、5,763 条校准优秀；HemoPI2 无冲突 4,811、分歧保留 2,308；覆盖漂移 0。
- FGF2 round120：run `3be3bde7-9231-533c-a3f8-36f0d135dee1`；5,344 条已物化、90,848 条结构化评价、5,064 条校准优秀；HemoPI2 无冲突 4,308、分歧保留 1,036；覆盖漂移 0。
- 历史 challenger 回填：147,161 个候选、735,805 条证据；HemoPI2/APEX/PeptiVerse 缺失均为 0；不重复回填。
- `.19`/synth 旧 Rosetta 200-decoy 链已停止且文件保留；13 条已有 ≥20 decoy，7 条已入库。
- 20-decoy 去重队列 86,776 条；已按 9 个安全空闲槽均分，依最新指令暂不启动 Rosetta。
- 瓶颈：六靶点 Pool A 完整 Rosetta 粗筛收据不足 50/靶点。

## 维护

只更新本文件；删除过时状态；不追加流水账。精确运行明细只写 PostgreSQL 与 JSON/CSV 收据。
