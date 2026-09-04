# 运行图数据契约（只读）

运行图不是阶段模板的渲染结果，而是对观察器接口返回事实的可视化。前端 `buildRuntimeGraph` 只接受本次运行的详情、节点明细和候选记录，任何未返回的关系都不补画。

## 当前已使用的事实入口

- `GET /v1/observer/runs/{run_id}`：运行状态、生命周期 `events`、候选预览 `candidates`，以及仅用于兼容读取节点明细的 `graph.nodes`。
- `GET /v1/observer/runs/{run_id}/nodes/{node_id}`：节点范围内的 `calls`、证据文件与工具调用参数；返回的每个 `ToolAttempt` 生成一个工具调用节点。
- 候选记录的 `generation` 生成代际分组节点；`parent_id` 与 `generator_call_id` 若存在，分别生成父子谱系和生成来源边。
- 事件 payload 或工具调用 `inputs`/`parameters` 中明确出现的调用、事件标识，才生成数据库显式关系边。

## 关系与状态语义

`provenance=database` 表示关系直接来自接口字段；`provenance=derived` 只表示候选按已持久化代际字段分组或调用区间重叠观测，不代表执行依赖。未完成状态按接口状态映射为进行中、待观测或已停止。前端不会按时间顺序自动连接相邻节点，也不会把失败的读取当作成功结果。

`relation_kind=dependency` 仅来自显式依赖白名单；`retry` 仅来自 `retry_of_call_id`、`retried_call_id`、`recovery_of_call_id` 及数组形式；`fallback` 仅来自 `fallback_from_call_id` 及数组形式。它们不根据时间、attempt 数字、失败文本或相邻位置猜测，并且只有 dependency 进入循环检测。`relation_kind=parallel` 只有后端显式 `parallel_group_id`（`provenance=database`）或已返回的 queued/finished 区间重叠（`provenance=derived`）两类来源；后者只称“并行观测组”，不宣称调度依赖。

生命周期状态映射：`.started`、`.running`、`.progress` → 进行中；`.created`、`.succeeded`、`.completed`、`.persisted`、`.materialized`、`.recorded`、`.accepted`、`.rejected` → 已完成；`.failed`、`.cancelled` → 已停止；其余后缀 → 待观测。原始事件键仍保存在节点详情中。

## 已确认的接口缺口

- 当前运行详情没有直接返回完整工具调用集合；前端通过节点明细尽力读取，读取超时则保留事件/候选图并显示缺口。
- 节点明细缺少统一的 `ToolCallDependency` 返回入口，因此无法观察完整的并行、回退和显式调用依赖。前端只接受 `parent_call_id`、`depends_on_call_id`、`dependency_call_id`、`upstream_call_id`、`previous_call_id`、`input_from_call_id` 及其数组形式；普通 `source`、`call_id` 文本不会被当作依赖。
- 候选预览当前可能缺少 `parent_id`、`generator_call_id`，此时父子谱系与生成来源不会被推断。
- 事件列表和节点调用存在分页/截断迹象；前端会在达到已知上限时提示可能缺失历史，而不伪造完整图。

建议后续只读接口提供统一的 `tool_calls`、`tool_call_dependencies`、`candidate_occurrences` 分页集合，并为每条记录返回 `id`、`run_id`、`attempt`、`status`、时间戳、父子关系和证据引用。

## 可读聚合与关系分层

- 显式 `batch_id`、`iteration`、`generation`、`action_plan`/`action_plan_id` 或共同父调用字段提供批次身份时，前端先按该身份在整次运行内全局分桶，再按最早观测时间排序；同一批次可跨工具、跨节点和非相邻记录聚合。卡面只显示中文批次序号和操作构成，原始身份留在详情中。
- 缺少上述字段时，同名工具不会跨整次运行合并。仅当调用在观测序列中连续相邻、且相邻观测时间差不超过 5 分钟时，才生成一个可展开的“观测批次”节点；这只是阅读折叠，不是数据库批次或 iteration 事实。每条原始调用仍保存在 `calls` 中，展开后按“第 N 次尝试”显示。
- 位置按 `started_at`/`queued_at` 等观测时间排序并按事实类型分层；这不是执行依赖。依赖边只来自结构化依赖字段。事件 payload 的 `tool_call_id` 只画“关联”边，候选 `parent_id` 是“父子谱系”，`generation` 是“代际分组”，后两者不进入执行循环检测。
- `candidate` 节点只表示候选记录存在，默认中性；前端不从 `reasons` 文本猜淘汰或质量。只有后端显式 decision/status/quality gate 契约补齐后，才可显示相应结论。
