# AMPgent v33：文献驱动正电性反事实与 Pareto 搜索充分性预注册草案

状态：`draft_not_authorized_for_execution`

精确合同：`config/benchmarks/amp_charge_search_sufficiency_v33.yaml`

## 1. 目标从哪里来

v33 不再用 v32 自生成序列的电荷分布定义下一轮生物学目标。v32 的 300 条记录只能回答 AMP-Designer
目前覆盖了哪些电荷区域，不能回答哪些区域更抗菌、更安全。它在 v33 中只作 generator coverage
diagnostic 和冻结 baseline，禁止用于挑 parent、定生物学最佳区间或看到输出后调阈值。

原始实验研究也不支持跨 scaffold 移植一个统一净电荷/电荷密度最优点：

- V13K 的 26-mer 电荷系列中，降低到约 +4 会损失活性，+7 到 +8 附近活性较好，而 +8 到 +9
  出现超过 32 倍的溶血恶化且抗菌收益很小；这是清楚的非单调、scaffold-specific 证据。
- CL(14–25) 的 K/R→A 类似物中，减少某些正电位点反而增强活性，作者把变化归因于疏水性和两亲性；
  正电数量与膜破坏/活性并非简单对应。
- charge-clustered 与 amphipathic pattern 的配对研究表明，正电残基排布可同时改变抗菌、哺乳动物
  细胞毒性和蛋白酶稳定性。
- K 与 R 的优劣依赖序列背景：已有 W/K 与 W/R 系列得到不同甚至相反方向；R9 本身也可在高正电下
  缺少抗菌活性，说明“电荷高”不是充分条件。

因此 v33 的文献驱动目标不是某个绝对数，而是：在同一 scaffold、同一位置和同一编辑剂量下，测量
引入 1 或 2 个 K/R 后的膜作用、AMP/MIC 与风险响应，并比较 K 和 R。回答的是局部因果响应与
trade-off，不是把生成器分布当生物学真值。

## 2. 七臂匹配反事实

parent 在查看 AMP/MIC、毒性、溶血、结构或 Rosetta 分数前按 raw order 冻结。主干预只允许内部
Q/N/S/T→K 或 R；D/E 暂时只观察，因为 D/E→K/R 同时“去掉一个负电并增加一个正电”，会把阴离子
去除与阳离子引入混为一个约 +2 的操作。

每个可达 parent 形成七臂：

1. 原始序列；
2. 同一位置引入 1 个 K；
3. 同一位置引入 1 个 R；
4. 同一位置、同一编辑数的电荷保持对照；
5. 同一组位置引入 2 个 K；
6. 同一组位置引入 2 个 R；
7. 同一组位置、同一编辑数的电荷保持对照。

位置选择不得依赖 K/R 身份或任何活性/风险模型。候选位置穷举后，依次最小化 K/R 两种版本中的
最大疏水矩变化、最大平均 Eisenberg 疏水性变化、最大连续 K/R 和位置元组。对照使用
`Q↔N, S↔T`。这样 K/R 身份比较共享完全相同位置，剂量比较共享相同 parent；仍需报告全部描述符
残差，不能声称只改变了电荷而没有任何侧链化学变化。

净电荷不超过 +8、电荷密度不超过 0.50 是本版本的保守 operational guard，不是宣称 +8 或 0.50
是普适生物学阈值。超过 guard、位置不足或 K/R 任一版本不可达时记录原因并不补抽。

## 3. 怎样回答 Pareto 是否已经迭代充分

v32 只给出冻结集合内前沿。v33 使用 3 个开发 seed 和 2 个确认 seed；每 seed 固定 1000 条 raw
proposal，并对前 25/50/100/150/200 个可达 parent 保存 archive snapshot。即使中途看似饱和也跑完
预算，禁止 adaptive early stop。

膜作用、AMP/MIC 和风险家族分别记录非支配成员、明确支配 witness、archive turnover、累计首次
发现的 family-local ε-cell、跨 seed attainment、成本以及 leave-one-model-out 稳定性。电荷剂量是
干预标签和分层变量，不作为一个自动奖励“越高越好”的 Pareto 家族。

只有预注册末段在所有家族、开发 seed 和确认 seed 同时满足新 ε-cell 与 turnover 门槛，才能称
`saturated_within_protocol_and_budget`；否则只能称未饱和或因 shortfall 无法判断。禁止声称 global
optimum，也禁止用加权总分或单一 hypervolume 宣布完成。

## 4. 数据库原生执行形态

正式实现必须把 raw batch、重复/拒绝 proposal、全部 occurrence、parent eligibility、dose block ID、
编辑位置、K/R/control 替换、全部指标 ToolCall、依赖、支配 witness、archive 增删、运行成本和
AgentDecision 落 PostgreSQL。原始输出、冻结 manifest、环境和 checkpoint snapshot 作为内容寻址
artifact 进入对象存储并由证据边引用。

完成门槛是只用 PostgreSQL 与对象存储重建 raw order、七臂、每个指标 join、风险排除、逐 checkpoint
archive、配对分析和充分性结论。CSV/Markdown 只能导出，不能作为缺失数据库证据的回填来源。

## 5. 结构确认与解释边界

结构确认仍是预注册但未授权的 shadow child：七臂每臂最多 4 条，总计不超过 28；每条 3 个 Boltz
seed，每姿势 16 个 Rosetta ref2015 decoy且全部评分。它只检查编辑是否破坏同协议结构假设。
pair-ipTM、口袋覆盖、碰撞、姿势一致性和 Rosetta REU 不是 AceA 结合、亲和力或实验作用证据。

## 6. 当前实现状态与剩余门禁

- 已实现并测试文献驱动的确定性 1/2-residue K/R dose block、同位置 control 和逐 checkpoint archive；
- 已保存 archive 增删、累计新 ε-cell 和移除成员的 dominance witness；
- 已实现未注册为 worker activity 的 PostgreSQL persistence primitives：baseline 复用原 parent，六个
  变体保存为带 `parent_id` 的 child；描述符 Evaluation、文献/生成/指标依赖、archive artifact、
  saturation AgentDecision 与完整 tool edges 均有显式持久化路径；
- 已实现 database+object-store-only replay verifier：精确检查 parent/child、序列与顺序、七臂、
  描述符容差、artifact SHA/角色、文献依赖、archive dominance witness 和 decision edge；缺失或歧义
  fail-closed；lost-response retry 只恢复既有身份，不推进 raw stream 或复制 child；
- executable implementation 已冻结为 commit `fab5cac50b3d709e9435c732173bc22eba81a505`；归档
  `var/archives/ampgent-v33-evidence-fab5cac.zip` 的 SHA-256 为
  `1519d6b4e26546b5f28b2a5e7f0489f423232591dba25f9c5047eadfc2e3f55e`；
- 尚未冻结执行环境 SHA 或 worker identity，也没有 v33 run/workflow；
- 当前不得部署、生成、提交或运行，v32 三层 run 链保持只读锁定。下一门禁是用户另行授权 formal
  run，然后重新完成服务、worker 身份、唯一 run 和第 6 节全部门禁。

## 7. 主要原始证据

- Jiang et al., *Biopolymers* 2008, PMID 18098173, PMCID PMC2761230,
  DOI 10.1002/bip.20911：V13K 净电荷梯度、MIC 与人红细胞溶血。
- Taniguchi et al., *Biopolymers* 2014, PMID 23982951,
  DOI 10.1002/bip.22399：K/R 位点删减、膜模型与活性不呈简单电荷关系。
- Stone et al., *J Med Chem* 2019, PMID 31194548, DOI 10.1021/acs.jmedchem.9b00657：正电 pattern 对活性、毒性和
  稳定性的配对影响。
- Zhang et al., *Sci Rep* 2016, PMID 27271216, PMCID PMC4897634：正电残基的位置而非仅数量可显著
  改变疏水性、两亲性、螺旋、宿主膜作用与选择性。
- Schmidt et al., *J Biol Chem* 2016, PMID 27046192，以及 2026 W-rich K/R 系列 PMID 42276501：
  K/R 身份效应依赖 scaffold，不能预设单一赢家。
- Llenado et al., *Infect Immun* 2009, PMID 19737896, PMCID PMC2772546：同为 Arg→Lys 替换，在两种
  α-defensin scaffold 中得到相反的功能方向。
- Jeong et al., *Sci Rep* 2019, PMCID PMC6761801：R9 单独高正电不足以产生抗菌活性。

这些研究规定的是干预哲学和对照结构，不直接提供 v33 的实验活性、安全或普适阈值。
完整的 claim→source→禁止外推关系已冻结在
`config/evidence/amp_charge_design_literature_v33.yaml`，SHA-256 为
`309062137acc291ae58346fa9b80b5025a5438c7def097e67e235182bbb98e6a`。正式 run 必须把该 manifest 原始
字节、SHA、literature freezer ToolCall、逐 claim projection 和 charge-transform 依赖边全部写入
PostgreSQL/对象存储；仅在本文列出引用不算 Agent 证据已落库。
