# AMPgent v35a 靶点资格合成数据库闭环验收授权说明

状态：`preregistered_not_authorized`

精确合同：`config/benchmarks/amp_target_qualification_synthetic_acceptance_v35a.yaml`

## 1. 这一步解决什么

v35 已在仓库实现靶点资格审计、面板选择 witness、选择成员三类 typed 实体，及其 migration、
retry-safe repository 和 database/object-store-only replay projection。尚未证明的是：这些实体能否在共享
PostgreSQL 的真实事务和对象存储环境中完整写入、拒绝非法操作并从数据库精确重放。

v35a 只允许用纯合成身份完成该工程验收。它不选择任何真实靶点，不查询外部靶点资料，不读取历史肽，
不生成候选，不运行结构或活性模型。

## 2. 冻结合成场景

- 8 个纯合成 shortlist 项，6 个通过、2 个拒绝，失败完整保留在分母；
- 6 个通过项中 A/B primary pocket 各 3 个，最终确定性选择 3 个；
- 8 个 target audit run 加 1 个 selection run；
- selection ToolCall 必须依赖全部 8 个 audit ToolCall；
- 每个审计和选择 AgentDecision 必须以 typed edge 连接对应 ToolCall；
- 0 Candidate、0 Evaluation；
- 所有原始对象、prompt/response、ToolCall input/output/error、anchor、witness、snapshot、replay 和
  acceptance receipt 均进入 PostgreSQL 与内容寻址对象存储。

## 3. 必须失败的七个探针

1. 面板冻结后追加 audit 行；
2. target、run、ToolCall 或 AgentDecision 跨身份连接；
3. AgentDecision 与 ToolCall 无 typed edge；
4. 相同幂等身份重试但 payload 漂移；
5. artifact metadata 或对象字节损坏；
6. AMP/MIC、风险、Boltz、Rosetta、PepShot 或生成肽结果进入 target selection；
7. shortlist 少于 8 项或静默丢弃失败分母。

每个失败探针自身的输入、失败状态、错误和 artifact 也必须落库；不能只在测试日志中出现。

## 4. 成功能说明什么

唯一允许的成功解释是：v35 typed qualification lineage 在共享 PostgreSQL/对象存储中可写、可拒绝、
可精确重放。

成功不能说明：

- 已选择或验证任何真实靶点；
- 已授权真实 target audit 或 panel execution；
- 已生成新的短肽；
- 已证明多靶点迁移、广谱性、结合、选择性或亲和力。

## 5. 授权语句

只有用户明确回复以下完整语句才授权执行：

`授权 v35a 靶点资格合成数据库闭环验收`

“继续路线图”“推进 v35”“可以规划”或一般性的“继续”均不构成授权。授权后仍须先核对 API、
PostgreSQL、MinIO、Temporal、active workflow、迁移基线、源码 revision 与禁止资源边界，并只提交唯一
合成验收 run。
