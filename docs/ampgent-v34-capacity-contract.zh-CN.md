# AMPgent v34 资源、并行与重试合同

状态：`preregistered_not_authorized`

精确合同：`config/experiments/acea_v34_execution_capacity.yaml`

本合同只冻结“获批后如何调度”，不授权 v34、不中止或启动任何远端进程，也不改变
`config/benchmarks/amp_knowledge_pepshot_ablation_v34.yaml` 的科学预算。

## 1. 资源拓扑

当前最多预留三个单卡 Boltz worker 槽位：`.19 GPU5`、synth GPU5、synth GPU6。`.32` 全部 GPU 与
`.19 GPU4` 明确禁止。每张物理卡最多一个 Boltz worker、一个 activity slot，不允许用单卡多进程制造
表面吞吐。正式启动前仍须逐卡核对物理主机、GPU、PID、worker role、task queue、环境、release/source
revision 和外来进程；任何卡有未归属负载就排除，不停止、不抢占。

Rosetta 冻结为 16 个单线程 activity slot，`OMP_NUM_THREADS=1`、`MKL_NUM_THREADS=1`。授权前必须确认
至少 16 个空闲逻辑核；不允许在 active workflow 中动态更换 worker 拓扑。

## 2. 96 个 episode 的公平调度

调度器只看到 `parent_order`、`arm_order` 和 opaque label，不看到真实 arm。队列固定为先 arm-order round，
再 parent-order：先调度 24 个 parent 的第一个密封 episode，再依次调度第二、第三、第四轮。每个 parent
最多同时运行一个 episode，避免同 parent 的四臂并发造成记忆污染或某一 parent 抢占资源；任何 arm 或
provider 都没有优先权。失败或 shortfall 不补位、不换 parent、不追加 proposal。

固定上限仍为 96 episode、384 个 Boltz pose、3072 个 Rosetta decoy。并发只改变墙钟时间，不改变
seed、预算、顺序、候选身份或科学端点。

## 3. 重试不是新科学样本

每个逻辑 ToolCall 最多两次物理 attempt（原始一次，加一次受控重试）。重试前先查 PostgreSQL 的
idempotency 记录，再查内容寻址对象；已经提交成功结果时只能恢复，不能重新计算。只有传输超时、worker
心跳丢失、数据库/对象存储瞬时不可用可重试；身份、SHA、release、环境、资源归属、非有限值或映射错误
一律 fail closed。重试必须复用完全相同的输入、seed、协议与 idempotency key，不得 reseed、补抽、替换
或产生额外科学 observation。重试耗尽后正式完成失败，不创建替代 run。

## 4. 证据落库

容量合同、启动前 worker placement snapshot、公平队列 manifest 和全 attempt ledger 都必须作为内容寻址
artifact 由 PostgreSQL typed evidence graph 引用。每个 attempt 还要写 typed lifecycle event，保存队列
位置、派发/开始/结束时间、worker 的 host/GPU/PID/role/release/environment、失败分类、idempotency 恢复
动作和最终 outcome。只有数据库与对象存储能完整重建资源拓扑、队列顺序和每次重试时，v34 才能完成；
本地日志、CSV 和 Markdown 仅是导出。

静态实现 `pepagent.v34_capacity` 能验证合同、从冻结 evidence plan 构造 96 个 arm-blind episode 的固定
队列，并生成明确标记为 `formal_run_authorized=false`、`formal_run_submitted=false` 的静态 preflight。
正式授权前仍须把该合同 SHA 接入 v34 benchmark/数据库证据计划，并完成动态 worker 映射门禁。

