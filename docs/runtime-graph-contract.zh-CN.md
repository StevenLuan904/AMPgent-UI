# 运行图数据契约（只读）

运行图不是阶段模板的渲染结果，而是对观察器接口返回事实的可视化。前端 `buildRuntimeGraph` 只接受本次运行的详情、节点明细和候选记录，任何未返回的关系都不补画。

## 当前已使用的事实入口

- `GET /v1/observer/runs/{run_id}`：运行状态、生命周期 `events`、候选预览 `candidates`，以及仅用于兼容读取节点明细的 `graph.nodes`。
- `GET /v1/observer/runs/{run_id}/nodes/{node_id}`：节点范围内的 `calls`、证据文件与工具调用参数；返回的每个 `ToolAttempt` 生成一个工具调用节点。
- 候选记录的 `generation` 生成代际分组节点；`parent_id` 与 `generator_call_id` 若存在，分别生成父子谱系和生成来源边。
- 事件 payload 或工具调用 `inputs`/`parameters` 中明确出现的调用、事件标识，才生成数据库显式关系边。

## 关系与状态语义

`provenance=database` 表示关系直接来自接口字段；`provenance=derived` 只表示候选按已持久化代际字段分组，不代表执行依赖。工具调用的 `attempt` 保留重试语义，未完成状态按接口状态映射为进行中、待观测或已停止。前端不会按时间顺序自动连接相邻节点，也不会把失败的读取当作成功结果。

## 已确认的接口缺口

- 当前运行详情没有直接返回完整工具调用集合；前端通过节点明细尽力读取，读取超时则保留事件/候选图并显示缺口。
- 节点明细缺少统一的 `ToolCallDependency` 返回入口，因此无法观察完整的并行、回退和显式调用依赖。
- 候选预览当前可能缺少 `parent_id`、`generator_call_id`，此时父子谱系与生成来源不会被推断。
- 事件列表和节点调用存在分页/截断迹象；前端会在达到已知上限时提示可能缺失历史，而不伪造完整图。

建议后续只读接口提供统一的 `tool_calls`、`tool_call_dependencies`、`candidate_occurrences` 分页集合，并为每条记录返回 `id`、`run_id`、`attempt`、`status`、时间戳、父子关系和证据引用。
