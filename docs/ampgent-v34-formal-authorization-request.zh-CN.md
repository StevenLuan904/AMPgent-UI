# AMPgent AceA v34 正式运行授权请求

状态：`authorization_requested_not_granted`

本文只定义用户将要授权的精确对象，不是授权本身，也不提交任何 run。精确科学合同仍是
`config/benchmarks/amp_knowledge_pepshot_ablation_v34.yaml`；本文与该 config 冲突时以冻结 config
为准。

## 1. 要回答的问题

在完全相同的 24 个冻结 parent、seed、proposal/结构/Rosetta 预算和独立评价下，比较：

1. baseline：无知识卡、无 PepShot；
2. cards-only：只加入 verified 文献知识卡；
3. PepShot-only：只加入 PepShot 结构审阅；
4. cards+PepShot：同时加入两项干预。

目标是判断知识卡、PepShot 及其交互是否改善 Agent 决策，不是证明新肽有实验活性、安全性、AceA
结合或亲和力。

## 2. 授权范围

若用户明确授权，只允许在全部门禁通过后提交一个正式 v34 run：

- 24 个 parent，每个 parent 四臂，共 96 个隔离 episode；
- 每个 parent×arm 固定提出 8 次，共 768 次 raw proposal occurrence；
- 每臂最多保留 4 条，每条 1 个结构姿势、8 个同协议 Rosetta decoy；在无 shortfall 时最多 384 个
  结构姿势、3072 个 Rosetta decoy；
- 每臂最多一次 revision，revision 替换原预算，不追加预算；
- 四臂不能共享记忆；评价者只见 opaque arm label，锁定 adjudication 后才揭盲；
- 不 adaptive early stop、不补抽、不改阈值、不使用加权总分；
- 不回写 v22–v33 或任何冻结 run。

授权不包括：修改 parent、增加预算、改变端点/margin、重新训练 provider、在 AMPgent 修 PepShot、使用
`192.168.99.32`、synth GPU4、他人任务或 Moba 资源、以及任何湿实验。

## 3. PepShot 与知识卡所有权

正式 run 只能消费 provider 自己发布、冻结且通过只读验收的 release。若运行前或运行中发现 PepShot
接口、renderer/runtime、输出 schema、证据足迹或科学审阅语义不满足合同：

1. AMPgent 以 typed failure/rejection ToolCall 和 artifact 保存可复现输入、失败输出、违反的合同与 SHA；
2. 将缺陷、回归 fixture、期望验收标准和证据落库要求发送至 PepShot 任务
   `019fb910-f2dd-7be1-a7e6-bfe381512c25`；
3. 停止受影响 episode，不写兼容层、不 monkey patch、不补包、不修输出、不降低门禁；
4. 等 PepShot 在自身仓库发布新不可变 release；
5. 对新 release 另做只读验收。是否允许替换正式 run 中冻结的 provider release，必须另行预注册，
   不能静默热换。

知识卡 provider 采用相同规则，对应任务为 `019fad3e-76b8-7e32-8455-d2e9b31d33e5`。

## 4. 执行前门禁

授权后仍必须逐项通过，任何一项失败都保持未提交：

- API、PostgreSQL、MinIO、Temporal 健康且 active workflow 为 0；
- 数据库中不存在同协议 formal run，Temporal 中不存在同 workflow；
- 24 个 parent 的 ID、sequence SHA、顺序和 member manifest 与冻结 config 精确一致；
- knowledge/PepShot release、runtime、schema、policy、fixture receipt 和 source SHA 精确一致；
- proposal、结构、Rosetta、独立评价及 replay activity 的物理主机、角色、PID、task queue、源码 revision
  全部可映射，且不位于禁止资源；
- 正式 implementation 已 commit/push，全量 `ruff`/`pytest` 通过并有内容归档 SHA；
- 盲化分配、失败策略、成本记录、provider rejection/change-request 路径和唯一 run 防重复门禁通过。

当前 provider shadow run `941ea473-82d6-4b70-9ede-5162a14bf8ce` 只证明两项 provider release 可从
PostgreSQL 与对象存储复原；它不是 formal run，也没有证明工具有效。

## 5. 数据库完成定义

所有 input、query、card/passage、PepShot request/bundle/image/review、prompt/response、proposal
occurrence、parent/child、ToolCall/dependency、Evaluation、AgentDecision、盲化/揭盲、失败、成本、
portfolio 变化和停止理由必须落 PostgreSQL；大对象进入内容寻址对象存储并由数据库引用。

只有 database+object-store-only replay 能重建 96 个 episode 的顺序、工具可用性、所有候选与 revision、
holdout join、配对效应和最终 verdict 时，v34 才算完成。CSV/Markdown 仅是导出，不能回填证据。

## 6. 用户需要确认的唯一问题

是否授权：在上述范围和冻结 config 不变、且所有执行门禁通过后，提交唯一一个 v34 正式 2×2 消融 run？

可接受的明确答复：

- `授权 v34 正式 2×2 run`；或
- `暂不授权`。

任何含糊答复、仅同意继续规划、仅同意 shadow，均不视为正式运行授权。
