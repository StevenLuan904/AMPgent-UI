# AMPgent v34：文献知识卡 × PepShot 的 Agent 干预消融预注册草案

状态：`draft_not_authorized_for_execution`

精确合同：`config/benchmarks/amp_knowledge_pepshot_ablation_v34.yaml`

## 1. 要回答的不是“工具能不能调用”

系统知识库已经能返回带 `policy_version`、`retrieval_trace_id`、来源和 evidence refs 的 Design
Context Pack；PepShot 已能生成可校验 bundle、坐标审计、受控图片和 schema 合法的结构 review。但
AMPgent 当前 mutation brief 仍把 PepShot 写成 `pepshot-placeholder-v1`，知识卡也只是 advisory
字段。工具存在、输出看起来合理或偶尔拦下一条坏结构，都不能证明 Agent 变好了。

v34 要回答：在完全相同的 parent、seed、proposal 数、结构预算和评价协议下，经过验证的文献知识
与 PepShot 结构审阅分别带来什么增益，二者是否互补，以及增益是否值得额外成本。

## 2. 每个 parent 都跑四个隔离 episode

使用 v32 数据库重放中冻结的 24 个 portfolio parent，按重放顺序全量纳入，不再根据 v34 输出挑
parent。每个 parent 都运行四个独立 episode：

1. `baseline`：无知识包、无 PepShot；
2. `cards_only`：只增加 verified Design Context Pack；
3. `pepshot_only`：只增加验证通过的 PepShot review；
4. `cards_and_pepshot`：两者都增加。

四个 episode 不共享对话记忆。执行顺序由 parent ID 与冻结 salt 的 SHA-256 排列决定；评价阶段只见
opaque arm label，必须先锁定判定，之后才揭示四臂身份。知识关闭不是让模型凭内部知识补齐；PepShot
关闭也不是伪造一个“无异常”结论，而是写入 typed tool-absent marker。

## 3. 同预算并不意味着假装工具免费

每个 parent×arm 固定 8 个 raw proposal，最多保留 4 个，每个结构姿势使用同协议的 8 个 Rosetta
decoy，最多一次 revision；revision 必须替换原预算中的 proposal，不能增加名额。所有 arm 的生成、
结构和评价预算相同，知识检索与 PepShot 本身的 ToolCall、墙钟时间和失败率单独报告。即使中途看似
有优势也完成全预算，不准提前停止或补抽 shortfall。

知识 on 时只接纳与任务匹配、来源 passage 可定位且满足距离/证据门槛的卡片；数据库、活动 policy、
schema 或 evidence reference 损坏时 fail closed。PepShot on 时必须依次通过 bundle verify、读取
`agent_request` 要求的全部图片、输出 schema 合法 review、再通过 review validation。未验证输出不能
进入决策。

## 4. 工具不能自己证明自己有效

主要端点分属三个独立家族：

- 每 parent 的确认后新颖非支配发现率；
- 对独立多 seed/坐标/Rosetta 结构冲突的拦截召回率；
- 无效或证据不足编辑率。

次要端点包括历史失败避免、冲突拦截精确率、跨 seed 决策一致性、机制 lane 覆盖和单位保留候选
成本。知识卡自己的“支持”标签不能成为唯一验收；PepShot 自己的 finding 也不能成为唯一结构真值。
评价使用冻结的独立结构复核、膜作用/AMP/MIC/风险家族和 v34 前已存在的失败 taxonomy。

报告知识主效应、PepShot 主效应、交互效应以及三条相对 baseline 的配对差异。所有差异统一换向为
正值代表改善，但原始端点仍分别展示。禁止加权总分、只挑成功案例或用一个相关软模型宣布晋升。

## 5. 何时才能运行和晋升

24 个 parent 的候选 ID、sequence SHA、lane/rank 和数据库重放顺序已经冻结，member manifest SHA-256
为 `f1955476cb761d9ca300a8fed00d9bb847e775ee5f4c1ef51d1346376a4f943e`。三个主要端点的 practical
margin 也已在输出前冻结：确认后新颖非支配发现率至少增加 0.25 条/parent（24 个 parent 即至少 6 条），
结构冲突拦截召回率至少提高 10 个百分点，或无效/无支持编辑率至少降低 10 个百分点；其余主要端点
分别不得恶化超过 0.10 条/parent、5 个百分点、5 个百分点。这些是 Agent 工程晋级幅度，不是生物学
活性阈值或显著性替代品，并须同时报告 parent-cluster bootstrap 区间。数据库持久化实现已冻结为
commit `4f152bc31498e0fcf53fa47469dfd2d2791b163d`：包括 typed proposal occurrence、精确重试
ToolCall/artifact/Evaluation/AgentDecision、盲化后揭盲门禁、冻结依赖图和 database+object-store-only
replay verifier。当前仍需实现并测试真实 knowledge/PepShot adapter activity、注册允许 worker、核验身份，
并获得单独 formal-run 授权，才能申请唯一 formal run。

只有至少一个主要实用端点达到冻结改善幅度，并且其他主要端点没有超过冻结退化幅度，工具或组合
才可晋升。否则结论必须是 context-specific、无已证明收益、有害/不可靠或无法判断。所有结论仍只是
计算流程改进证据，不是实验 AMP 活性、安全性、AceA 结合或亲和力证据。

## 6. 数据库原生证据链

正式 run 必须把 parent 顺序、opaque assignment、知识 query/pack/trace/card/passage/policy、PepShot
request/bundle/audit/image/review/validation、prompt/response、proposal occurrence、采纳/拒绝/revision、
全部 ToolCall dependency、Evaluation、AgentDecision、成本、失败和揭盲事件写入 PostgreSQL；大型原始
输出进入内容寻址对象存储并由数据库 artifact 引用。

正式完成的定义是只依赖 PostgreSQL 与对象存储即可重建四臂顺序、工具可用性、全部决策、holdout
join、配对效应和晋升结论。CSV 与 Markdown 只允许作为导出，不能用于回填缺失证据。

当前 evidence plan SHA-256 为
`94f008863a57ff306b3134e1e81f7b6ed4dac81ca45b03b5d8c9cbc0e32084b5`：96 个 episode、770 个逻辑
ToolCall、每 episode 固定 8 次 raw proposal，共 768 次 proposal occurrence。相同序列可去重成同一
Candidate，但每一次提出行为仍单独落 `candidate_occurrences`；因此重复生成、拒绝项和 lost-response
重试都不会被候选去重吞掉。

实现记录提交为 `bba75e95b358eca205be0736f3d7b8600765355f`，全量验证为 ruff clean、pytest
`294 passed`；内容归档 `var/archives/ampgent-v34-persistence-bba75e9.zip` 的 SHA-256 为
`07ec7cbe1e5649e50df7b899c3cdb8ed04cb9bfa38eea43be573e07018e525af`。
