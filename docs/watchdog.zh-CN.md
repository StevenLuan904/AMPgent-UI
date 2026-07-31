# Auto Research Watchdog

## 为什么必须有

长程研究任务大部分时间都在等 GPU、Rosetta 或外部服务。让研究 Agent 持续轮询既浪费推理预算，也容易把“进程还在”误判成“实验在推进”。Watchdog 的职责是判断系统是否健康推进，并只在关键节点或异常时唤醒研究 Agent。

## 定位

- Watchdog 是独立监督组件，不是候选生成器，也不是 metric。
- 它只读实验状态和证据，不修改序列、分数、父代或科学结论。
- 所有恢复操作都生成新的、可追溯的操作节点；不得覆盖失败记录。
- 主研究流程即使失去 Agent 会话，也继续由 Temporal 持久执行。

## 观察什么

- Temporal workflow/activity 状态、attempt、heartbeat 和 task queue poller；
- PostgreSQL 中最后一个生命周期事件、generation、候选、工具调用和决策边；
- GPU/CPU worker 进程、实际子进程、资源占用和产物更新时间；
- MinIO artifact 是否出现且哈希可解析；
- 进度是否超过该节点基于历史运行时间设定的允许窗口。

不能只检查 `status=running`。有心跳但长期没有新 evidence，同样属于停滞。

## 如何唤醒

事件优先，低频轮询兜底。以下事件唤醒研究 Agent：

- 一轮完成，需要审阅结果并决定是否继续；
- workflow/activity 最终失败或反复重试；
- worker、隧道或存储不可用；
- 指标或结构出现严重异常；
- 全部轮次完成，需要证据审计和发布。

正常的单个 seed 完成只记录 checkpoint，不唤醒 Agent。

## 恢复边界

Watchdog 可以重启同版本 worker、恢复隧道和触发确定性的健康检查。凡是会改变实验 spec、模型版本、阈值、MSA 策略或随机种子的操作，都必须唤醒研究 Agent，由研究 Agent 创建新版本 run，并把旧 run 标记为失败分支或上游证据。

## MVP-v2 实施

当前使用两层监督：Temporal 负责节点级重试与恢复；绑定当前 Codex 任务的 heartbeat 定期核对 Temporal、PostgreSQL 和真实 worker 进度。产品化时增加 `watchdog_checkpoint` 与 `watchdog_alert` 节点，并通过 `observes / detects / wakes / recovers` 边连接被观察的 workflow、activity 和 Agent 决策。

Watchdog 的终点不是“一个 workflow 完成”。每次多轮研究循环结束后，它还要唤醒研究 Agent 比较各 generation 的主指标、结构一致性、Rosetta 相对复排、序列多样性和淘汰原因。若没有真实改善或没有可信候选，研究 Agent 必须定位生成、选择、口袋条件、结构 gate、采样或精修中的最小问题，版本化修改流程并启动新 run。旧 run 保留为负结果和新实验的上游证据。

因此长期状态机是 `运行 → 评估 → 诊断 → 改进流程 → 新 run`，而不是 `运行 → completed → 停止`。只有出现相对前代明确改善、跨 seed 结构证据一致、Rosetta 相对复排支持并保有多样性的候选，同时工程证据链完整，Watchdog 才允许结束并进入发布。

验收标准是：停止 Codex 会话后实验仍继续；杀掉一个 worker 后任务不丢；制造假心跳或无产物停滞时能报警；恢复后旧失败证据仍可查询；结果不佳时能够形成可验证的流程假设、启动下一版循环，而不是重复相同 run。
