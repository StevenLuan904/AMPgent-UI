# AMPgent v37 单臂快速 champion 资源与流水合同

状态：`preregistered_not_authorized`

精确容量合同：`config/experiments/acea_v37_rapid_champion_capacity.yaml`

本合同只冻结未来获批 v37 的执行方式，不设置科学 proposal 数量、不生成候选、不提交 run，也不启动、
停止或探测远端进程。proposal 数量、seed、评价指标、过滤规则、最终 champion 判定和停止条件必须由另一份
先冻结的 v37 科学 benchmark 给出。

## 快速但不偷换预算

v37 是单臂流水，不再承担四臂公平比较，因此按 proposal ordinal 做确定性 FIFO：

`proposal(8) → cheap evaluation(16) → Boltz(3) → Rosetta(16)`

括号内是并发 activity slot。各级队列有界，分别最多积压 32、64、12、64 项；下游满载时暂停上游派发，
不丢弃、不重排。每一级必须先把输入/输出 manifest 提交 PostgreSQL 并由对象存储 SHA 定位，下游不能直接
读取尚未落库的本地临时输出。cheap evaluation 的拒绝也必须先落库，才能跳过后续结构计算。

允许导出中间进度供监控，但中间高分不是最终 champion。只有独立科学合同的固定预算完整结束，才能执行
最终选择；禁止根据早期好结果提前停或临时扩大预算。

## 可用资源

最多使用 `.19 GPU5`、synth GPU5、synth GPU6 三个单卡 Boltz worker；`.32` 全部 GPU 和 `.19 GPU4`
永久排除于本合同。每卡一个 worker、一个 activity slot，不做 GPU oversubscription。Rosetta 为 16 个
单线程 slot，`OMP_NUM_THREADS=1`、`MKL_NUM_THREADS=1`。

这些只是 eligible placement。启动前仍要核实物理主机、GPU、PID、role、task queue、环境、模型权重、
release/source revision 和外来进程。发现其他任务即排除该卡，不停止、不抢占；active workflow 中不改变
worker 拓扑。

## 重试与证据

每个逻辑 stage call 最多两次物理 attempt。先查 PostgreSQL 幂等记录和对象 SHA，已有成功结果就恢复，
不得重复计算。只有传输、心跳或存储瞬时故障可按原输入、seed、协议和 idempotency key 重试；身份/SHA/
release/环境/资源归属、非有限值或映射错误 fail closed。重试不算新科学 observation，不 reseed、不补位、
不替换。耗尽后标记该项失败，并阻止最终 completion gate。

容量合同、worker placement、流水 manifest、队列/状态转移、backpressure、每次 attempt 与重试分类都必须
进入 PostgreSQL typed evidence graph；大型字节进入内容寻址对象存储。静态实现
`pepagent.v37_capacity` 只构建流水依赖和 no-host-touch preflight，始终返回
`formal_run_authorized=false`、`formal_run_submitted=false`。

