# AMPgent v37 worker/activity 桥接审计

状态：`blocked_missing_executable_activity_bridge`

本审计只读取仓库实现，不探测或启动远端 worker，不注册 Temporal poller，不提交 run。

## 可复用基础

- Temporal 已有 control、portfolio、metrics、Boltz2、Rosetta 五类 queue/worker 模式。
- 三个生成器现均与 v37 每 seed 1000 raw 的冻结预算一致；HydrAMP 与 AMPGAN v2 接受 request-driven
  budget，AMP-Designer 使用其已验证的固定 1000-row/batch 合同。Boltz2 和 Rosetta 已有独立执行与
  持久化 primitives。
- PostgreSQL schema/repository 已支持 CandidateOccurrence、ToolCall、Evaluation、dependency、
  AgentDecision/edge、Artifact 和 LifecycleEvent。
- v34 知识卡与 PepShot 已有严格 consumer/validator，但目前不是 Temporal activity。

## 当前不能安全启动的原因

1. `RapidChampionGenerationV37Workflow` 引用了尚未注册的 v37 activity 名；通用 temporal worker 也未注册
   该 workflow。仅让 poller 在线会制造“看似 ready、实际 activity not found”的假健康状态。
2. 当前 workflow 串行执行 9 个生成 batch 和 11 个 metric，并把结构确认合并为一个 GPU activity；这没有
   实现冻结的 proposal→evaluation→Boltz→Rosetta 有界流水，也没有把 Rosetta 放到 CPU queue。
3. 三个生成器的 budget/adapter 语义已经对齐，但其 executable、环境、模型路径及全部 SHA 尚未作为 v37
   runtime manifest 冻结。知识卡/PepShot 也尚缺正式 Temporal consumer activity。

## 最小安全桥接合同

`pepagent.v37_worker_bridge.V37_ACTIVITY_CONTRACT` 冻结 13 个 activity binding：生成、指标、Boltz、
Rosetta 的 compute activity 负责把每次物理 attempt 写 typed lifecycle；对应 control activity 负责将
ToolCall、CandidateOccurrence/Candidate、Evaluation、dependencies、AgentDecision/edge 和 artifacts
落库。知识卡、PepShot、shortlist、最终 portfolio 和 replay 也必须是数据库原生 activity。

Boltz compute 固定 `pepagent-gpu-boltz2`，Rosetta compute 固定 `pepagent-cpu-rosetta`；两者不能合并。
`build_v37_worker_role_config` 只有收到全部且仅有这 13 个 callable 才构建 role registration，缺一个或多一个
均 fail closed，因此不会注册 placeholder-success activity。

下一步是冻结三个生成器的 runtime manifest，再逐项实现上述 activity 和新的流水 workflow。完成前不得
启动 v37 poller 或把 activity availability gate 标为通过。
