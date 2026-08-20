# AMPgent Workflow Observer 证据写入合同

本合同定义只读 Observer 可依赖的数据库证据。一个 formal science run 对应一个 Round；
Observer 只读取新 run 自创建后自然产生的记录，不回填、不重算、不修改历史 run。

## 固定决策树

新 run 在创建时冻结 `v38.formal-workflow-topology.1` JSON 工件，并在
`experiment_runs.spec_json.workflow_topology_artifact` 保存 SHA-256、media type、大小和
storage URI；紧凑 topology 同时保存在 `spec_json.workflow_topology`，因此任务尚未开始时
即可画出完整树：

`knowledge → generation → sequence_metrics → admission → refinement → structure_boltz →
structure_rosetta → final_portfolio → replay`

数据库事件 `run.workflow_topology_frozen` 记录 topology/schema 版本与工件 SHA。Observer
不得从大型 workflow request 工件猜测预算。

## 正式数据库证据

- `run_stage_checkpoints`：九个固定 stage 的 append-only 观测。字段直接使用现有
  `stage_name/stage_order/observation_no/durable_count/expected_durable_count/stage_status/
  controller_action/reasons_json/tasks_json/receipt_sha256/observed_at`。只在状态或 durable
  计数发生变化时追加。
- `lifecycle_events`：Activity 使用 `v38.activity-lifecycle.1` typed payload，事件类型为
  `activity.started|progress|succeeded|failed|cancelled`；包含 run、activity identity、可用时的
  ToolCall ID、logical stage、display category、attempt、completed/expected、worker role 和
  task queue。不得写自由文本日志、异常正文或凭据。
  AMPgent worker 由 interceptor 写入；独立 knowledge refinement provider 由 workflow 在
  control queue 上用同一 schema 桥接 started/succeeded/failed/cancelled，避免外部队列成为
  决策树盲区。
- `lifecycle_events`：知识读取使用 `knowledge_card.read` 与
  `v38.knowledge-card-read.1`。冻结 context pack 记录 pack SHA；refinement 对实际引用的
  passage 记录 card key、provider 版本、passage SHA、来源 URI、adopt/reject 和 read time。
  `content_kind=passage_evidence` 明确 passage SHA 不冒充整张卡片的 content SHA。
- `tool_calls`：保持原 schema；v38 新 ToolCall 的 `tool_call.succeeded` 事件补充
  `v38.tool-call-display.1` 的 `logical_stage/display_category`。父子图继续使用
  `tool_call_dependencies`；决策输入输出继续使用 `agent_decision_tool_call_edges`。
- `agent_decisions.structured_json`：admission 增加
  `v38.candidate-decision-observer.1` 投影，包含 considered、selected、rejected、deferred
  candidate IDs、reason codes、policy/version/policy SHA 和 input evidence SHA。该投影不改变
  admission 的科学选择。
- `artifacts + evidence_artifacts`：CIF/mmCIF/PDB 统一使用
  `role=structure_coordinate`；`Artifact` 提供 media type、SHA 和 storage URI，
  `multitarget_structure_evidence_records` 提供 target/candidate/control lane/seed/decoy 关联。
- final portfolio/replay：输出工件角色为 `v38_final_portfolio_and_replay`；
  `AgentDecision.response_artifact_id` 指向输出工件，所有结构 ToolCall 以 input edge 连接，
  portfolio ToolCall 以 output edge 连接。

## Derived progress 与本地瞬时状态

Observer 可从 topology、最新 checkpoint、ToolCallDependency 和 AgentDecision edges 派生 UI
布局及进度，但必须标成 derived。ETA、瞬时吞吐、高频 heartbeat、observer 自身写入错误等
只原子写入未跟踪的 `var/observer/<run_id>.json`，schema 为
`v38.observer-transient.1`，包含 `updated_at/ttl_seconds/source`。它不是 PostgreSQL 证据，
也不得包含 password、secret、token 或 credential 字段。

## 兼容边界

此实现复用现有表，不新增 migration。只有新创建且带 topology schema 的 formal run 写入
上述 checkpoint/event；旧 run 不回填。Observer 写入失败时科学 Activity fail-open，并在本地
瞬时文件留下 typed 状态，避免改变科学合同、exact-once 身份或历史证据。
