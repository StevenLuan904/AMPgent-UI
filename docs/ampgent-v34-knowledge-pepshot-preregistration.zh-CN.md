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
replay verifier。真实接口的离线 adapter 合同现已实现并通过隔离测试：知识包必须匹配冻结 task、原始
schema/policy 字节 SHA、`generated_at`、verified card 与可定位 passage；PepShot 必须按实际 CLI 的
`bundle→verify→读取全部图片→validate-review` 路由，验证 `valid` receipt、bundle identity、artifact
计数、priority-first 阅读顺序和所有图片 SHA。adapter 会生成与 evidence plan 完全一致的 knowledge/
PepShot artifact roles，并把验证 receipt 一并封装供 PostgreSQL ToolCall 引用。它仍未注册为 worker
activity，也没有执行任何 episode；完成 shadow preflight、允许 worker 身份核验并获得单独 formal-run
授权后，才能申请唯一 formal run。

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

离线外部证据 adapter 实现冻结于 commit
`3f842967cab0c56e8c933b19afe5da98569de202`。当前 config SHA-256 为
`ece9e8d2853dd727d98fdc8951ad0e5dcca03a99f3ebd2df0c8df7f7f224c365`，外部合同 footprint
SHA-256 为 `912c8fd868d409b2ef6326007e5879cd4fbbc83b3c26c81ae986c0a0ae29b4be`，离线 preflight
SHA-256 为 `e53c8f894df5f8d32c6cb09661c71e51ca91723d365c5d10f01b8b8cae6ef903`；状态严格为
`ready_for_isolated_shadow_fixture_not_formal_execution`。全量验证为 ruff clean、pytest `306 passed`。
记录提交为 `90bf0bf`；内容归档 `var/archives/ampgent-v34-adapters-90bf0bf.zip` 的 SHA-256 为
`5fe4157e2901f982ee4b8822a8140f512a2c4fe5ced8d3c7d9aa24a3faec92ee`。这不是工具有效性结果，也不是
运行授权。

source-manifest 增量门禁实现冻结于 commit
`12cd18e9790fe67503709406c007d49cd5f677eb`。知识库 15 个 allowlisted 源码/合同/依赖输入的 manifest
SHA-256 为 `402a7be05785ce2fbbf9e8be4d714af1aa6952aee26f60de17f8ee1bf7e4cad4`；PepShot 32 个文件为
`b9ab9ecb88d6d82c3e93d28909702ddd2b56632c437df5bd60627892258519fa`。清单不编码机器绝对路径。
config 中 implementation revision 已更新为该 commit，当前 config SHA-256 为
`6ba458badbe8bb7e4446c9120b5cd5387f547f81c19812060868318c295e3388`，外部 footprint SHA-256 为
`8f792f0e780ae14a265821bea3a672881982cd3d397fe4627b79eb273a3394ec`，离线 preflight SHA-256 为
`c58b3af9f94c4e8cdf2d08167f647f5bba22121fac953ef2cfcde7447b711406`。该状态仍只是
`ready_for_isolated_shadow_fixture_not_formal_execution`：实际部署的 Python/PyMOL 环境尚未核验，
shadow fixture 尚未运行，且没有 Temporal activity、run、workflow 或新序列。
记录提交为 `696f696`；内容归档 `var/archives/ampgent-v34-source-manifest-696f696.zip` 的 SHA-256 为
`0f8f44fce80a43e489df62694520ae4e7b62b9134a832a149d2a72a27ceab91a`。

## 7. 外部工具必须自己满足合同

AMPgent 只维护冻结请求、消费 schema、证据 artifact roles 和 fail-closed validator，不为知识库或
PepShot 写 provider-specific 兼容实现。若 provider 源码、依赖、renderer、receipt 或 fixture 不满足
合同，修复必须发生在 provider 自己的任务与仓库，并发布新的不可变 revision/runtime manifest；AMPgent
随后只读验收。禁止在 AMPgent 环境中补装 provider 缺失依赖、monkey patch 输出或降低门禁。

该规则也适用于效果层面的“不满意”，而不只适用于接口报错。若 PepShot 的视图、finding、review
schema、可复原足迹或科学语义不能支持 v34 的冻结端点，AMPgent 必须把可复现缺陷、失败 fixture 和验收
标准发送至 PepShot 任务 `019fb910-f2dd-7be1-a7e6-bfe381512c25`，等待其在自身仓库修复、测试并发布新的
不可变 release。AMPgent 不为旧 release 写适配器来绕过问题。消费侧自身的合同错误仍由 AMPgent 修复；
例如 provider release 中的固定 fixture bundle 只证明该 release 可运行，不得被误当成以后每个候选
bundle 的身份。

2026-08-11 只读探针发现：PepShot controller runtime 满足其 Python 依赖，但既有 renderer 缺
`gemmi==0.7.5`；当前 AMPgent 平台环境缺知识库 requirements 中的 pypdf/jsonschema/paramiko，不能作为
知识库官方 runtime。问题已分别退回任务 `019fb910-f2dd-7be1-a7e6-bfe381512c25` 与
`019fad3e-76b8-7e32-8455-d2e9b31d33e5`。下一步等待两方交付源码 revision、路径无关 runtime
fingerprint、真实固定 fixture 与验证 receipt，再由 AMPgent 运行隔离 shadow。该等待不授权正式 run。
消费侧路径无关 Python/conda runtime probe 与 requirement/import verifier 已实现于 commit
`43fbe926cc8f3dcc0abc0231502b9079bc1a2368`，ruff clean、pytest `309 passed`；config 暂不更新到该
revision，须在 provider 交付后与其冻结 source/runtime manifest 一并重新预检。
provider gate 记录提交为 `6e38bdb`；内容归档 `var/archives/ampgent-v34-provider-gate-6e38bdb.zip`
的 SHA-256 为 `6e813011781cba3f6d9ea255381f870d8401fdd81226808c20b034fcd76fc8c9`。

2026-08-11 provider-owned 修复已分别发布并通过官方 verifier。知识卡 release revision 为
`amp-kb-acea-shadow-6d0eea37f2c145df`，release manifest SHA-256 为
`7fd21012bcbcbe519dd964b6c9c826f16532d257cbb721951cb3ab0c4023e518`；PepShot release ID 为
`pepshot-34487cf9667a64c3-fe1e5382de8cab09`，release manifest SHA-256 为
`b4f4b848f603f431e5db49bd66e018904c35c9eacf97ae83882d92e6710f2c5d`。PepShot 官方
`release-verify` 验证 30 个 artifact 和 9 张解码图片且无错误；知识卡官方 verifier 验证 33 个 passage、
冻结检索 policy 与 selection receipt。AMPgent 消费侧验收 receipt SHA-256 分别为
`b7149b780b44dfd3c0ff7fce00879af2e537bf33c81bf5531ebd272a53820c15` 与
`2985342dee11fdd4d3f112628e57dbec8529276124c3cd4c826db64feba43db7`。

这只解除 provider runtime/发布合同阻塞，证明可进入数据库原生隔离 shadow；不证明知识卡或 PepShot
提高了短肽质量，也不授权 v34 formal run。shadow 必须把完整 release、verifier 输出和消费侧 receipt
写入对象存储并由 PostgreSQL ToolCall/artifact/dependency/decision 引用，且仅靠数据库与对象存储重放。

## 8. Provider shadow 完成与所有权锁定（2026-08-11 append-only update）

数据库原生隔离 shadow 已完成并锁定，run ID 为
`941ea473-82d6-4b70-9ede-5162a14bf8ce`，parent 为
`de9f72ae-e490-408d-9432-c71a75a3d499`。该 run 没有生成候选或计算效果指标：0 Candidate、
0 Evaluation、4 ToolCall、5 ToolCallDependency、1 AgentDecision、3 条 decision edge、6 Artifact 和
8 LifecycleEvent。结论严格为
`provider_releases_replayable_for_v34_authorization_request`。

完整知识卡 release archive SHA-256 为
`cc04d5c67437f743c4b90595b15d0ba4e361c73b96319db07ea09eea8adce686`，PepShot release archive
SHA-256 为 `1cb5f3b642242a7c2d5bf0340137875d48438aa5a93e5e2db2ce82b5687556f0`；对应数据库验收 receipt
artifact SHA-256 分别为 `fff7cba95e73645a1241a586b60bb4fd958672f5e770cb42caa1c9992a114a23` 和
`e4e0ff844486ea755fcadeda517979832c3ef075a16d5d5f120df5608cd01cbd`。database+object-store-only
replay bundle SHA-256 为 `390d5757ee55d7a010b66701b4d6fe0338eb97f4d84b87c20b572f74cc9ae73c`，重放确认
`exact_replay=true`、`provider_effectiveness_evaluated=false`、`formal_v34_authorized=false`。

前一节记录的 PepShot 基础消费 receipt SHA-256
`2985342dee11fdd4d3f112628e57dbec8529276124c3cd4c826db64feba43db7` 使用了旧的 fixture 字段解释；
在消费侧把 `bundle_id` 正确区分为 `fixture_bundle_id` 后，基础 receipt 的追加更正值为
`d7ae26187004eb0949251753bea7389f6be6d4dea47713d00bde1ac9c1e7d487`。这是一项 AMPgent 消费合同
错误修正，不是对 PepShot 输出的适配，也没有改变 provider release。

用户再次明确：如果后续 2×2 消融显示 PepShot 的视图、finding、review schema、证据足迹或科学审阅
语义不满意，AMPgent 不修改、包装或兼容 PepShot。必须把可复现输入、违反的冻结合同、期望验收标准、
回归测试和证据落库要求直接发送到 PepShot 任务 `019fb910-f2dd-7be1-a7e6-bfe381512c25`，由 PepShot
自己修复并发布新不可变 release；AMPgent 只拒绝旧 release、记录 change request，并只读验收新 release。
本文历史所称“adapter”只表示严格消费验证器，不授权任何 provider-specific compatibility adaptation。

shadow 的完成只允许提出 v34 正式 2×2 消融授权申请。正式 v34 仍未提交、未授权，不能运行知识卡/
PepShot 效果对照，也不能生成新序列。

完成锁定提交为 `834ef57`，完成态 config SHA-256 为
`a8e4e4e3fafcb638c292bbb042eaa88fc4900c163d32e654778417e880893547`；内容归档
`var/archives/ampgent-v34-provider-shadow-834ef57.zip` 的 SHA-256 为
`652f1801c09f9babb6cd7295e3f1df7960b023b766913ef1deb45af20508274c`。

## 9. Provider change request 数据库闭环（2026-08-11 append-only update）

此前规则说明了“对 PepShot 不满意就退回 provider”，但正式 evidence plan 只冻结 release receipt 和
episode 内的采纳/拒绝结果，尚不能完整重放“拒绝旧 release → 向哪个任务发送什么验收要求 → 收到
哪个替代 release → 如何只读复验”。v34.1 现新增固定 `v34-provider-governance-freeze` ToolCall；正式
完成图即使没有发生退回，也必须保存 provider ownership、冻结 release 和显式空 change-request ledger。

若发生退回，ledger 必须保存 provider task、拒绝 run、独立 change-request child run、被拒绝 release、
触发类别、可复现输入、违反合同、验收标准、外部任务发送 receipt、生命周期状态，以及替代 release
manifest 和只读复验 receipt。AMPgent 兼容适配必须为 `false`。正式 run 内禁止热替换 provider release；
替代 release 只能在当前 run 停止并经过新的冻结/授权边界后使用，防止同一 2×2 对照混入两个工具版本。

evidence plan 现在固定 771 个 ToolCall、1345 条 dependency；database+object-store replay 会读取并校验
change-request ledger 的原始 artifact，而不只核对其 SHA。实现 revision 为
`9e879fec1285d2a1071fde7cd2d874765409aa24`；回填后的 config SHA-256 为
`b6adc410f99185f1f25c6205c57dc89223c0d685f5c2b80084a8cb39106318e6`，plan SHA-256 为
`67020e0241cf2eb0dae954e9dd8767a5321207ea3b1b656aacd69d62f35f4939`。全量验证为 ruff clean、pytest
`368 passed`。这些是未执行的消费治理合同，不表示发生了新的 PepShot 缺陷，也不授权 v34 formal run。

revision/文档回填 checkpoint 为 commit `f76a8101588f2c34ecfce21ea941aaf30d3db96b`；内容归档
`var/archives/ampgent-v34-provider-governance-f76a810.zip` 的 SHA-256 为
`64b2db2692b2b640e5d3d3b91e03de4ee621a8c1a7f53c873c7689e7b642da54`。该归档是未执行合同的仓库
checkpoint，不是 provider change request 或效果实验结果。
