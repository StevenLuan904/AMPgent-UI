# AMPgent 长期目标与问题账本

状态：`active_roadmap`

首次冻结：2026-08-11

维护者：当前 AMPgent/AceA Agent

事实源关系：本文件管理长期问题、假设与阶段目标；`docs/ampgent-acea-execution-protocol.md`
管理当前可执行状态与禁令；每个 `config/benchmarks/*.yaml` 仍是唯一精确科学合同。

## 1. 项目最终要做到什么程度

在没有湿实验的当前边界内，AMPgent 的合理能力上限不是“发现最好的药物”，而是成为一个
**可审计、可复原、会用对照实验改进自身的短肽假设工厂**：

1. 从多个生成器、文献知识、序列描述符、活性/风险软模型、结构与 Rosetta 相对证据中提出
   机制互补的候选组合，而不是制造一个不可解释的总分冠军。
2. 对每个候选说明它来自哪里、为什么被修改、依赖哪些证据、与哪些替代方案比较、在哪些
   端点上有利或冲突，以及哪些结论仍不能声称。
3. 在固定计算预算下，通过预注册的配对/消融实验，证明新的知识、工具或 Agent 策略提高了
   新颖非支配候选的发现效率、决策稳定性或错误拦截能力。
4. 不只适配 AceA：在预先资格审计的多靶点面板上区分可迁移规律、靶点特异规律和失败边界。
5. 把每次成功、失败、重试、排除和决策保存成 PostgreSQL 中的 typed evidence graph，并由
   数据库加内容寻址对象存储完整重放；CSV/Markdown 只作导出。
6. 让 harness 以受控的 champion/challenger 方式演化：任何新策略先离线重放，再 shadow，最后
   固定预算前瞻对照；不能在进行中的正式 run 内自改规则。

因此，项目的终局验收是“持续产生更好、更多样、证据边界更清楚的计算候选，并能证明改进来自
哪里”，不是计算分数等同于 AMP 活性、安全、AceA 结合、亲和力或临床价值。

### 1.1 能力成熟度：版本完成不等于能力晋级

| 等级 | 可声称的能力 | 必须具备的证据 |
|---|---|---|
| L0：来源可追踪 | 能找回输入、模型、工具和版本 | 精确身份、SHA、行数、顺序与来源记录 |
| L1：规则可复算 | 同输入可重算预注册变换和选择 | 冻结 config、确定性 verifier、负向测试 |
| L2：决策可复原 | 能从数据库与对象存储重放完整 Agent 决策 | typed evidence graph、原始 artifact、database-only replay |
| L3：干预有效 | 能证明一个组件在同输入、seed、预算下改善预注册端点 | 隔离对照、盲化、独立评价、完整成本与失败分母 |
| L4：作用域可迁移 | 能区分跨靶点通用、靶点特异和失败策略 | 资格审计后的多靶点面板、阴性对照和同协议复现 |
| L5：受控自我改进 | harness 能基于历史证据安全晋级或回滚 | 离线 replay、shadow、前瞻对照、策略谱系和追溯式回滚 |

截至 2026-08-11，项目整体处于 **L2（AceA 单靶点、冻结指标作用域）**。v32 已证明一个正式
portfolio 可以数据库单源复原；v34 provider shadow 证明外部 release 可被复原消费。它们尚未证明工具
干预有效、跨靶点迁移或 harness 自我改进，因此不能提前声称 L3–L5。当前无湿实验边界下的合理上限是
L5 的“可持续改进计算假设工厂”；即使达到 L5，也不能声称药效、安全、AceA 结合或临床有效。

### 1.2 第一性原则：从候选质量反推工作，而不是从工程仪式正推

当前最优先的长期能力不是“门禁最复杂”，而是能更快地产生生物学合理、抗菌/膜作用潜力更强、风险
更可控、结构证据更稳健且保持机制与序列多样性的短肽候选。工程、工具和治理只有在以下至少一项成立
时才值得占用主线时间：直接改善候选；揭示某种候选为什么改善或失败；保存复现该判断必需的证据。

因此长期执行采用以下分层：

1. 科学层严格：模型/seed/预算、序列身份、输入输出对应、端点语义、Pareto 比较和不得声称的结论不能
   为赶进度而漂移。
2. 证据层严格：影响正式结果的生成、重试、评价、选择、排除、失败和决策全部进入 PostgreSQL，原始
   大对象进入内容寻址对象存储，并能 database+object-store-only replay；不能用本地文件修补缺口。
3. 资源层严格：GPU 禁区和外来进程保护不可简化；共享资源必须先证明无冲突。
4. 工程形式从简：worker 通常只需保存 host、GPU/CPU、PID/role、source revision 和无外来冲突证明。
   不会改变执行字节、候选结果、证据完整性或资源安全的额外 receipt、dashboard、部署仪式和审计字段
   不得成为独立阻塞。
5. 行动默认继续：routine 缺陷先只读定位，再直接修复、做相称测试、记录并推进下一轮生成/评价；在
   有安全且范围内的下一步时，不因等待普通确认而停下。

“结果优先”不等于只追最高软分。最终仍追求可解释的非支配 portfolio，保留活性、膜作用、风险、结构
和多样性之间的真实冲突；也不把计算预测包装成实验活性、安全、结合或亲和力。

## 2. 当前基线（2026-08-11）

- v32 已锁定：300 条候选，109 条预注册双红旗排除，191 条可进入 Pareto，最终四个 lane 共
  24 条；数据库单源精确重放通过。
- 当前“Pareto 最优”只表示：在这 191 条、这些冻结指标和排除规则下，该组合由非支配层与
  lane 规则选出。它不表示序列空间全局最优，也不证明继续采样不会找到更优候选。
- v32 只使用 AMP-Designer 的三个 seed；未比较 search budget 曲线、archive 饱和、独立生成
  路线或跨靶点泛化。
- 文献知识卡任务 `019fad3e-76b8-7e32-8455-d2e9b31d33e5` 已建立可追溯公开文献流程；当前本地
  checkpoint 仅有 8 篇开放全文、9 张 verified cards，仍是早期证据层。Agent 平台已有 knowledge
  card 字段，但正式近期流程不是动态检索接入，缺卡时使用 placeholder；历史卡也含手工注入。
- PepShot 任务 `019fb910-f2dd-7be1-a7e6-bfe381512c25` 已形成确定性坐标审计、PyMOL 图像和
  受控 Agent review 能力；但当前 mutation brief 明确使用 `pepshot-placeholder-v1`，其解释没有
  指导 v32 采样。平台文档存在不等于已完成正式接入。
- AMPgent/AceA 设计验证仍以 AceA 为主；现有结构工具的多 complex 工程验证不能冒充 AMP
  生成流程的多靶点泛化实验。

## 3. 永久研究哲学

### 3.1 Pareto 是证据治理，不是一次排序

- 每个终点家族保持独立语义；不把膜作用、AMP/MIC、风险、结构和知识支持压成加权总分。
- “非支配”必须带作用域：候选集合、指标版本、缺失策略、风险规则与预算。
- 冲突、缺失和不确定性是组合的一部分，不用平均值消除。
- 组合质量同时看前沿质量、序列/机制覆盖、跨 seed 稳定性和单位计算成本的新发现率。

### 3.2 Agent 的改进必须由干预实验支持

接入一个工具不等于 Agent 变好。每个新组件必须有冻结的 baseline、同输入/seed/预算的
challenger、独立评价端点、预先停止条件和数据库可重放证据。禁止只展示 challenger 的成功案例。

### 3.3 Harness evolution 不能形成自证循环

用于生成/选择的软指标不能同时作为唯一验收指标。历史 run 要按时间和版本分层；提议策略不能
读取 holdout 的最终决策标签。新 harness 只有在离线重放、shadow 和前瞻对照依次通过后才能晋级。

### 3.4 生物学目标必须由外部证据定义，生成历史只诊断可达性

- 新的生物学优化目标、干预方向和风险边界必须来自可定位的外部证据：优先原始实验研究，必要时
  由系统综述帮助界定检索范围；不得从本项目自己生成的分数分布、分位数或当前 Pareto 前沿反推
  “应该优化到哪里”。
- 自生成历史只能回答生成器覆盖、可达性、预算分配、匹配分层和异常检测问题。它可以说明“当前
  能生成什么”，不能说明“什么在生物学上更有效或更安全”。
- 文献若只支持 scaffold-specific、位置依赖或非单调效应，预注册就必须采用同 scaffold 配对干预、
  剂量梯度与匹配对照；禁止把单篇研究的绝对净电荷、MIC 或毒性拐点移植为跨 scaffold 通用阈值。
- 每个目标规则必须记录 `objective_claim`、来源 DOI/PMID、可定位 passage、证据等级、适用距离、
  支持与反对证据、检索时间和内容 SHA。检索、纳入/排除、规则抽取及 Agent 采用/拒绝边必须进入
  PostgreSQL evidence graph，原始页面或全文快照进入内容寻址对象存储。
- 生成结果只能更新“覆盖与可行性”判断。要改变生物学目标，必须经过新的外部证据审查和新版本
  预注册；不得在 active run 内用自身输出闭环改写目标。

## 4. 长期问题账本

### Q1：显式正电性怎样进入设计，而不是事后筛选？

当前判断：`in_progress`。v32 的 `net_charge_ph7_4` 是 `observe_only`；v33 已形成未授权预注册草案，
精确合同为 `config/benchmarks/amp_charge_search_sufficiency_v33.yaml`。2026-08-11 的审查已纠正一项
目标设定错误：v32 自生成电荷分布只能诊断生成器覆盖，不能定义下一轮生物学目标。v33 现改为由
原始电荷梯度、K/R 替换和 charge-pattern 实验支持的同 scaffold 相对干预：1/2 个 K 或 R、同位置
同编辑数 control；绝对净电荷和电荷密度只作描述与 operational guard。

要回答的问题：

- 在 pH 7.4 下，应控制绝对净电荷还是按长度归一化电荷密度？
- K、R、H 的位置与比例、D/E 抵消、两亲性相位和疏水协同分别造成什么收益与风险？
- 条件生成、受约束突变和生成后拒绝采样，哪一种在同预算下产生更多有效新颖候选？
- 怎样构造“只改变电荷轴”的匹配反事实对，避免把电荷效果与疏水性、长度或 scaffold 混淆？

建议版本：v33。先预注册干预剂量与风险护栏，再生成；正电不定义为越高越好。当前主干预只用
Q/N/S/T→K/R，避免把 D/E 去负电与加正电混淆。每个剂量共享 K/R 的编辑位置，并保留同位置
charge-neutral/hydropathy-near control；检查膜作用、AMP/MIC、软风险和结构是否发生方向性变化。

当前工程证据：文献驱动预注册、七臂变体器与 archive 充分性组件已冻结于 commit `140c71f`；归档
SHA-256 为 `c8224ac766c3b10ecefaeb443b42a5a570be0795e6b5b2a7418e3d188d65c1b3`。这只证明规则可确定性执行，
不代表 v33 已获运行授权或已经产生新的短肽结果。

后续工程闭环已冻结于 commit `fab5cac50b3d709e9435c732173bc22eba81a505`：parent/child、descriptor
Evaluation、文献与指标 ToolCall 依赖、逐
checkpoint archive、dominance witness、saturation AgentDecision 和对象存储 artifact 可形成数据库原生
证据图，并可只用 PostgreSQL 与对象存储精确 replay。lost-response retry 恢复原身份而不追加新变体。
归档 SHA-256 为 `1519d6b4e26546b5f28b2a5e7f0489f423232591dba25f9c5047eadfc2e3f55e`。在获得另行
formal-run 授权前，这仍不表示 v33 已执行或产生结果。

外部证据现进一步冻结为 `config/evidence/amp_charge_design_literature_v33.yaml`，SHA-256
`94096787d62233e9dca77f277bc24ec18ce512e9cb49db740255541f02b897e4`。它逐项保存九项原始研究记录
身份，其中八项为实验、一项仅为机制模拟，并冻结 source-record SHA、passage locator、证据等级和
适用距离。R9 的高正电无活性边界反例与 KR-12 机制证据现和 benchmark 身份精确闭合；三组冲突
witness 明确规定：K/R 没有跨 scaffold 的全局赢家，电荷数量不是活性/风险的单调目标，正电本身也
不足以推出抗菌活性。

v32 自生成分布仍只诊断 generator coverage 与预算可达性；生物学目标是同 scaffold 的 K/R 身份、
1/2-residue 剂量和位置效应，不是任何内部 quantile 或统一净电荷区间。正式 run 必须把 manifest 和
九份精确 source-record 字节、逐记录 SHA/passage、claim projection、冲突 witness 与 ToolCall 依赖写入
数据库—对象存储证据图。当前外部网页核验尚未落入 formal graph，来源漂移必须阻断并版本化，禁止
静默刷新。

source-record 审计实现为 commit `0bb8fb65c7bc42f427e9c06e55c2fab4cb8a7e26`，回填后的 benchmark
config SHA-256 为 `5bcf988937a0a51d39b4304c3e98ef454563abbe887fd1206d114d2e4aebfc54`。
它进一步回答了“目标从哪里来、冲突如何治理”，但仍未回答“哪种编辑有效”，因为 v33 未获执行授权。
revision 与账本回填 checkpoint 为 commit `cc999e6d3f45af9dddf656217540ec81bb560c53`；内容归档
`var/archives/ampgent-v33-source-evidence-cc999e6.zip` 的 SHA-256 为
`6cbde73186e00ecf558384cd286a2f6f9a5a1a18aae07186e8d7de585cb998cd`，全量测试 `360 passed`。

### Q2：当前 Pareto 搜索是否已经接近可达最优？

当前判断：`in_progress`。v32 只证明冻结样本内的非支配组合，不能证明搜索收敛。v33 已把“搜索
充分”升级为合取证据合同：25/50/100/150/200 全预算 checkpoint、active/cumulative family-local
ε-cell、cell turnover、开发/确认 strict-majority attainment 双向复现、成本观察和逐软模型剔除诊断。
实现与 database/object-store-only 重算 verifier 已完成，但尚未执行，因此没有新的饱和结论。

要回答的问题：

- 随预算增加，archive turnover、新增非支配候选率和新占据的 family-local ε-cell 是否趋稳？
- 独立 seed/生成器的 attainment 是否重合，还是持续发现不同区域？
- 去掉任一相关软模型、对有限数值作容差内扰动后，lane 与候选身份是否稳定？
- 每单位 GPU/CPU 时间、ToolCall 和候选预算能带来多少新的可复原非支配发现？

方法要求：使用预注册的逐批预算曲线和独立确认 seed。候选身份 turnover 只作审计，目标空间
ε-cell turnover 才进入稳定门；同 cell 的并列候选增长不算新进展。开发与确认 cohort 分别形成
strict-majority attainment surface，并要求双向被另一 cohort 的每个 seed attain。报告 novel discovery
yield、成本和 leave-one-soft-model-out Jaccard；模型剔除脆弱性与搜索饱和正交，不能把稳定搜索解释为
模型正确。不以 front size、候选 churn、单一 hypervolume 或加权总分宣布收敛。只有完整固定预算、
连续预注册末段 cell 稳定、独立 seed 目标区域复现且诊断齐全，才可称“在冻结生成器、指标、seed 与
预算内经验饱和”，不能称全局最优。

方法依据 manifest 为 `config/evidence/pareto_search_sufficiency_methods_v33.yaml`；其明确区分原始方法
文献支持的集合值/attainment 哲学和本项目自行预注册的 ε 宽度、`1 cell/50 candidates`、`0.10`
cell-turnover 实用门槛。后者不是文献给出的普适常数，也不得在看到 v33 输出后修改。

搜索充分性 v2 实现冻结为 commit `56710db7fbc5f02d79d1a46046d0c14d4e080f30`；方法 manifest 原始
SHA-256 为 `b5c3629cf19d90a6962d048cbe6bf8ff1d6ee7bef7ae449ffe03c649aa5470e6`，回填 revision 后的
benchmark config SHA-256 为 `486a8ce423d06ab05df3847f1ebe12d73de6bff6a3a0976809da3e8cf11a765b`。
全量测试为 `340 passed`。这些证据只证明预注册合同与数据库重算器可用，不能证明搜索已经饱和。
revision 回填 checkpoint 为 `87b96532ac3cac6cc0bac785ccae5ca34757fa21`，内容归档 SHA-256 为
`cb32279158e9f2f32827111677ff4aa201f7346909b38ba861eb401ec5339557`。

### Q3：文献知识卡是否真的提高设计质量？

当前判断：`deferred_by_user`。用户于 2026-08-12 明确要求当前不做消融，先追求候选结果质量并快速
看到结果；Q3 保留为以后研究问题，不再阻塞当前主线。v34 已形成未授权的 2×2 预注册草案，精确合同为
`config/benchmarks/amp_knowledge_pepshot_ablation_v34.yaml`。离线 adapter 已能校验冻结 task、原始
schema/policy 字节 SHA、verified card、可定位 passage 与完整 evidence artifact roles；动态检索仍未
注册为可执行 activity，也未进入正式 episode。

工程状态更新：ToolCall、artifact、proposal occurrence、AgentDecision、盲化门禁与
database+object-store-only replay 的持久化 primitives 已实现，但动态检索与 passage 绑定尚未注册为
可执行 activity，也没有获授权的端到端 workflow。

建议实验：冻结 parent、seed、预算和所有模型，比较 `no_cards` 与 `verified_cards`；后续可增加
`retrieval_without_rerank` 和 `context-matched_cards`。知识组只能读取与任务上下文匹配、来源 passage
可定位且状态为 verified 的卡。主要评价不是 prompt 好看，而是：有效新颖非支配发现率、无效修改率、
重复失败避免率、机制多样性、决策可解释性和独立 shadow 端点。所有 query、候选 card、rerank、
passage SHA、prompt、response、采纳/拒绝边必须落库。

### Q4：PepShot 是否帮助 Agent 避免结构性错误？

当前判断：`deferred_by_user`。PepShot 可作为当前单臂 champion 流程的已验收辅助工具使用，但当前不
比较其 off/on 效果，也不据单臂结果声称 PepShot 有效。工具增益问题留待以后。v34 草案已固定
PepShot contract/request/review schema SHA、verify→读取全部请求图片→validate-review 路由和允许影响的
决策类型。离线 adapter 已按实际 CLI 的 `valid` receipt、bundle identity、artifact 数、priority-first
顺序、全部图片 SHA 和 review validation 实现 fail-closed 校验，并生成与 evidence plan 一致的五类
artifact；尚未注册 activity 或运行。

工程状态更新：正式 evidence graph、重放 primitives 与离线 PepShot adapter 合同已实现；实际
PepShot activity、worker 注册和运行仍未发生。

建议实验：同一批冻结结构做 `PepShot off/on` 配对；与知识卡形成可解释的 2×2 设计：baseline、
cards-only、PepShot-only、cards+PepShot。PepShot 只影响结构审阅、追加视图和是否升级/修改，不得
偷偷改变生成预算。评价结构冲突拦截率、误报、审阅一致性、追加视图成本、后续 Rosetta/多 seed
方向稳定性和“高软分但明显几何异常”候选的减少。PepShot finding、图片、query、review、验证结果、
artifact SHA 和 Agent 决策边全部进入主证据图；只存在外部工具数据库不算接入完成。

### Q5：方法能否泛化到别的靶点？

当前判断：`in_progress`。尚未对任何新靶点执行候选生成或结构评测；v35 已先冻结不含具体靶点名单的
qualification framework，精确合同为 `config/benchmarks/amp_multitarget_qualification_v35.yaml`，叙事
说明为 `docs/ampgent-v35-multitarget-qualification.zh-CN.md`。该框架不授权 target selection、候选生成
或 formal run。

进入多靶点前先冻结 target qualification：生物学假设、序列/结构来源、pocket 定义、结构完整性、
阴性/错口袋对照、可比较协议和不得声称的结论。第一阶段建议使用 3–5 个具有不同 pocket 形状、
同源性和作用机制的细菌靶点，并保留 target-agnostic AMP lane。比较同一 harness 的成功率、前沿形状、
结构稳定性、候选跨靶点复用和失败类型；不能把“对多个口袋都有有利 REU”称为广谱靶向或选择性。

v35 现进一步冻结：先审计至少 8 个新靶点候选，所有通过和失败都留在数据库分母；最终 3–5 个新靶点
只使用生物学、序列、结构、pocket 证据和预注册多样性描述符做 hard-gate+maximin，选择阶段禁止读取
任何新肽、Boltz、pair-ipTM、Rosetta、AMP/MIC、安全或 PepShot 结果。primary pocket 必须达到 A/B
证据级；每靶点预先冻结 native pocket、same-target wrong/decoy pocket 和 target-agnostic lane。预测结构
只能作带 pLDDT/PAE 限制的 hypothesis，不能单独定义 primary pocket。

该 qualification framework 冻结 revision 为 `6608c2c690a76d0dcf1e4b974613676204c00b9e`，config
SHA-256 为 `a722f0f74d486237a128327a3158ae71ee143577f3f8b7e4acb46505e38778da`，内容归档 SHA-256 为
`b33c54adeec43c736ba7d0ba340ce7d0cf813426e42b40358662463c591f17eb`。这不授权具体靶点审计或运行。

Q5 工程层现新增 `v35.target-qualification-replay.1` typed ledger/offline verifier：它要求不少于 8 个
shortlist 项完整保留通过/失败分母，并从 immutable sequence/structure/pocket artifact、A/B hard gate 与
AceA-anchor-aware deterministic maximin 重算 3–5 靶点面板；会拒绝任何 AMP/MIC、风险、Boltz、Rosetta
或 PepShot 结果进入 target selection。typed qualification-audit、panel-witness、ordered-member 三类
PostgreSQL 实体、migration `0011_target_qualification_lineage`、retry-safe repository primitive 与
database-row + object-store-only projection verifier 现已在仓库实现；会拒绝跨 target/run 证据、
AgentDecision/ToolCall 脱链、冻结后追加 ledger 行和重试漂移。共享 PostgreSQL 尚未部署 migration，
隔离合成数据库 acceptance 尚未执行，所以没有授权真实 target audit，也没有产生多靶点结果。
该离线实现 revision 为 `e47e0d3cf94d6b9d0b63c5a799694c13aeb819ca`，回填后的 config SHA-256 为
`c9641143982940a0a05127e8b2e0081837a499b13770fc4c0ac6ecbad63a0c81`，全量测试为 `352 passed`。
它只关闭 Q5 的确定性离线重算缺口；本次 typed persistence 实现进一步关闭仓库 schema/repository
缺口，但 migration 部署、合成验收和真实靶点授权仍是三个独立后续问题。
typed persistence revision 为 `6767f603be82ff3370bd655eed67cc29e7b81080`，migration SHA-256 为
`08e486d8d4d267ba57b763a27aefed8db5c139e31e5c212e1eb46fe11c00d472`，回填后的 v35 config
SHA-256 为 `2a7b54a1ac1c7ace73cb3c39b3f6ab3eed6676dda033e73c866fe4883f9ec027`，全量测试为
`355 passed`；这仍不是共享数据库部署或真实多靶点结果。
revision 回填 checkpoint 为 `09ec7cf025636cf1b67f83b5d6243c7aa497bf3f`；内容归档 SHA-256 为
`3d8f923264e46c1c7f02c37fe9ddc1faa4f9694590a61978acf16ea18551d520`。
Q5 的下一工程门已预注册为 v35a 合成数据库闭环验收，精确合同为
`config/benchmarks/amp_target_qualification_synthetic_acceptance_v35a.yaml`。它只检验 typed lineage 在
真实 PostgreSQL/对象存储事务中的可写、可拒绝和可重放：8 个匿名合成 shortlist 项、7 类负向探针、
0 Candidate、0 Evaluation。当前为 `preregistered_not_authorized`；即使未来通过，也只关闭数据库工程
缺口，不能回答跨靶点泛化、不能选择真实靶点，也不能生成肽。预注册实现 revision 为
`41aba8ba08405cde65479bfd802fd2c6b2891598`；回填后的 v35a config SHA-256 为
`b85d8542d1ab2f7f18b6c803fe8f6fea042dfcc0b1967ff3797b76af067befcb`；全量测试为 `359 passed`。
revision 回填 checkpoint 为 `a9516bdb8d6521c505c5428ac24b6cd3af513f08`；内容归档 SHA-256 为
`8d4e3bfd1b4280725253fe4e7c1c543e541f33daffce216ca284ff72ec30f384`。
revision 回填 checkpoint 为 `d79858dc3aa42399e439abaabc7d2e0fbe42bc70`；内容归档 SHA-256 为
`31b549ee748bd07edd083351732c8c4f76f1fbb4c8f8326d20716d05b12ad10b`。

### Q6：怎样利用历史尝试进行 harness evolving？

当前判断：`in_progress`。v32 的 typed evidence graph 与 replay closure 提供了 run 级底座；v36 已冻结
证据治理式演化框架，精确合同为 `config/benchmarks/amp_harness_evolution_v36.yaml`，叙事说明为
`docs/ampgent-v36-harness-evolution.zh-CN.md`。当前状态是
`typed_schema_and_offline_verifier_implemented_not_deployed_not_authorized`，没有 replay、shadow、
champion/challenger trial 或候选生成。

每个 harness 版本必须保存：`harness_id`、`parent_harness_id`、变更假设、允许读取的历史切片、
禁止泄漏的 holdout、代码/config/model SHA、预算、失败分类和晋级结论。演化循环固定为：

```text
历史证据图归纳失败模式
→ 提出单一或最小策略变更
→ 在冻结历史 run 上做 counterfactual replay
→ shadow challenger（不改变正式决策）
→ 同预算前瞻 champion/challenger
→ 晋级、保留为专用策略或回滚
```

允许学习：重复失败的变异、工具调用顺序、证据缺口、预算浪费、lane 塌缩、策略在何种上下文有效。
禁止学习：利用 holdout 最终答案调阈值、回写旧 run、在 active run 中改 policy、把软模型自洽性当进步。

v36 将 harness 定义为版本化的“证据到决策”策略，而不是一段 Prompt；每个 challenger 只允许一个主要
可归因变化，并按时间隔离 proposal history、counterfactual replay、shadow 和 prospective holdout。
晋级必须通过失败归纳、历史时点一致 replay、不影响正式动作的 shadow、同预算盲化前瞻对照和作用域
晋级/回滚五关；replay 或 shadow 单独不能晋级，也不设加权总分或默认全局冠军。

schema 审计提出的六类 typed 实体已在仓库实现：`HarnessRelease`、`HarnessLineageEdge`、
`HarnessTrial`、`HarnessAssignment`、`HarnessOutcome`、`HarnessPromotionDecision`；同时新增独立
`adjudication_run_id`、repository primitive 和 database/object-store-only verifier。纯合成 fixture 已覆盖
泄漏、谱系环、shadow 越权、manifest/ToolCall 脱链、揭盲顺序、decision artifact、裁决 run 脱链及
非有限值。共享 PostgreSQL 尚未迁移，合成数据库 acceptance 尚未执行，所以所有演化 gate 继续未授权。

治理框架冻结 revision 为 `f7b58f9`，config SHA-256 为
`286bc3888f675ef5dc794e40aad8903ad173674dcaf554d1936e185962f2043e`，内容归档 SHA-256 为
`20b43305c7cc6586c977262638f3273da776eb3db4898926edb8e88bb2182c66`。这些足迹只证明治理框架已冻结；
后续实现足迹另行追加。即使 typed schema 已在仓库实现，也不证明任何 harness 已改善。

下一工程门已进一步预注册为 v36a 合成数据库闭环验收，精确合同为
`config/benchmarks/amp_harness_synthetic_acceptance_v36a.yaml`，授权说明为
`docs/ampgent-v36a-synthetic-acceptance-authorization.zh-CN.md`。它只允许两个隔离 synthetic scope
分别覆盖作用域晋级和祖先回滚，走完 replay→shadow→prospective 三阶段与五类端点，共 0 Candidate、
0 Evaluation。通过也只能回答“typed lineage 在真实 PostgreSQL/对象存储事务中可写、可拒绝、可重放”，
不能回答 harness 是否改善。typed preflight 实现 revision 为
`1905974f0a8f8818e7591cf3b38d70df5344c975`，v36a config SHA-256 为
`62a18e0f13f3bd248176ab91cf1300fd82c4da9770e40d8d4b5d07366a4a5387`，全量测试 `346 passed`。当前仍为
`preregistered_not_authorized`，路线图维护不能推定执行授权。
revision 回填 checkpoint 为 `6299b233eef751004eec946f4ee2eab1edacdc1b`，内容归档 SHA-256 为
`b1a7e1f4c4a2ee40a4f2838461ac4ebaf61cabd0f2a7df92aeabe04f535a5e41`。

## 5. 分阶段路线与晋级门

### 5.1 长期问题的完成证据矩阵

| 问题 | 当前精确合同与状态 | 当前已证明 | 标记 `answered_within_scope` 前仍必须证明 | 下一独立门 |
|---|---|---|---|---|
| Q1 正电性设计 | `amp_charge_search_sufficiency_v33.yaml`；`preregistered_draft_not_authorized` | 文献来源、K/R 配对变换与数据库 replay 合同可校验 | 固定预算结果显示哪类同 scaffold 编辑改善活性/膜作用且没有仅以风险换收益 | v33 formal run 独立授权 |
| Q2 Pareto 搜索充分性 | 同一 v33 合同；`preregistered_draft_not_authorized` | checkpoint、ε-cell、attainment 与饱和判据可重算 | 跑完冻结预算并由独立 seed 复现末段目标区域，或明确判定未饱和 | 与 Q1 共用但不混淆结论的 v33 formal run |
| Q3 文献知识卡增益 | `amp_knowledge_pepshot_ablation_v34.yaml`；`deferred_by_user` | provider release、receipt 和 shadow 可数据库复原 | 2×2 同预算盲化对照在预注册实用端点上给出增益/退化、成本和完整失败分母 | 当前不执行；以后由用户重新启用 |
| Q4 PepShot 增益 | 同一 v34 合同；`deferred_by_user` | PepShot release 只读验收和 provider 退回路径已闭合 | off/on 配对证明结构错误拦截、误报、追加视图成本及后续方向稳定性的净效应 | 当前不执行；不满意仍直接退回 PepShot |
| Q5 多靶点泛化 | `amp_multitarget_qualification_v35.yaml`；`typed_persistence_implemented_not_deployed_not_authorized` | 资格审计 ledger、面板选择与离线 replay 可验证 | 先完成 v35a 数据库验收，再审计不少于 8 个靶点并在冻结 3–5 靶点面板报告成功与失败 | v35a 合成闭环需独立授权；真实 target audit 再另行授权 |
| Q6 Harness evolving | `amp_harness_evolution_v36.yaml`；`typed_schema_and_offline_verifier_implemented_not_deployed_not_authorized` | typed 谱系、晋级/回滚规则和离线 verifier 可验证 | 先完成 v36a 数据库验收，再以真实历史进行无泄漏 replay、shadow 和前瞻配对，并产生可追溯晋级/拒绝/回滚 | v36a 合成闭环需独立授权；真实演化再另行授权 |

矩阵中的“已证明”只陈述当前证据实际覆盖的范围。“下一独立门”不得由路线图维护、测试通过、无 active
workflow 或用户一般性“继续”推定授权。每次状态变化时必须同时更新对应 Q、精确 config、执行协议和本表；
若三者冲突，以用户最新明确指令、冻结 config、PostgreSQL/Temporal 实况和执行协议为准。

| 阶段 | 核心交付 | 晋级前必须证明 |
|---|---|---|
| v33：正电性与搜索充分性 | 显式电荷设计、匹配反事实、顺序预算曲线 | 不靠更高风险换取全部收益；给出协议内经验饱和或明确未饱和结论 |
| v34：知识/视觉干预 | knowledge card × PepShot 2×2 同预算消融 | 至少一个预注册实用端点改善，且成本与失败也完整报告 |
| v35：多靶点迁移 | 资格审计后的 3–5 靶点面板与对照 | 区分通用、靶点特异和失败策略；不能只挑成功靶点 |
| v36+：持续 harness evolution | champion/challenger 晋级器与策略谱系 | 离线 replay、shadow、前瞻对照三关通过；可一键回滚和完整复原 |
| v37：结果优先单臂 champion | 当前最佳 Agent 流程直接生成、评价、结构确认并输出 portfolio | 快速得到可查看的高质量候选组合；不声称工具增益或实验效力 |

v37 执行闭环已冻结到 `fd263e8afc984960067fad94821d12a5b3effd73`，授权状态机冻结到
`c4ef99ff3743408910a61cd4c0f0f5b6ef845fa2`，并通过独立对抗复审。用户已授权
唯一正式执行；当前状态为 `execution_authorized_not_submitted`。下一动作是内容寻址 preflight、数据库
migration、允许资源的物理映射与一次 exact-once submit。v34 消融继续 deferred；v37 不产生工具增益结论。

2026-08-12 追加执行事实：v37.0.0、v37.0.1 与 v37.0.2 的工程失败均作为不可变证据保留，均未产生可
解释为最终短肽结果的完整 cohort。v37.0.2 揭示了 Temporal 心跳超时后“已开始但无终态”的 attempt
证据语义缺口；该问题已在独立的 `v37.0.3-interrupted-attempt-recovery` 中以数据库原生、带并发围栏的
typed interruption ledger 修复。它不改变本路线的科学问题、预算或成功标准，也不复用旧 run 数据。
当前近期目标仍是完成一次可数据库回放的 v37 单臂 champion portfolio，再据此更新 Q1/Q2/Q6；工程恢复
本身不构成肽质量、工具增益或实验效力结论。资源边界同时明确为：`192.168.99.32` 整机禁用（明确包括
GPU3/GPU4），`.19` GPU4 禁用，其他资源仅在归属与 release 映射清楚且不干扰他人任务时使用。

v33 当前叙事预注册见 `docs/ampgent-v33-charge-search-preregistration.zh-CN.md`。其状态为
`implementation_frozen_not_authorized`；确定性 K/R dose block、逐 checkpoint archive、PostgreSQL
persistence primitives 与 database+object-store-only replay verifier 已实现并有测试。persistence
primitives 尚未注册到 Temporal worker；未经单独 formal-run 授权，不得注册或提交。

v34 当前叙事预注册见 `docs/ampgent-v34-knowledge-pepshot-preregistration.zh-CN.md`，精确合同为
`config/benchmarks/amp_knowledge_pepshot_ablation_v34.yaml`，状态为
`draft_not_authorized_for_execution`。每个冻结 parent 都运行 baseline、cards-only、PepShot-only 与
cards+PepShot 四个隔离 episode；proposal/结构/评价预算相同，工具成本单列，评价先盲化后揭盲。当前
parent identity manifest 已由 v32 database-only replay 冻结，SHA-256 为
`f1955476cb761d9ca300a8fed00d9bb847e775ee5f4c1ef51d1346376a4f943e`；三个主要端点的 practical
improvement/degradation margins 也已在输出前冻结。实现 commit
`4f152bc31498e0fcf53fa47469dfd2d2791b163d` 新增 typed `candidate_occurrences`、精确重试 ToolCall/
artifact/Evaluation/AgentDecision primitives、770 节点依赖物化、96 个盲化锁门禁和仅依赖数据库/对象
存储的 replay verifier；8×96=768 次 raw proposal occurrence 可逐次复原。它们没有注册到 Temporal，
实际知识检索与 PepShot adapter 也尚未执行，因而不能执行或声称工具有效。
该实现记录提交为 `bba75e95b358eca205be0736f3d7b8600765355f`，全量测试 `294 passed`；内容归档
SHA-256 为 `07ec7cbe1e5649e50df7b899c3cdb8ed04cb9bfa38eea43be573e07018e525af`。
预注册与预执行验证器 checkpoint 为 commit `29a352abb858e07086ffac943e2b5c939c97d940`，内容归档
SHA-256 为 `cf5afb9ee7a4c01d1628323523abd15ff9589e52208def845c4b00d0b8ef6eba`。
离线 knowledge/PepShot adapter 与 preflight 实现冻结于 commit
`3f842967cab0c56e8c933b19afe5da98569de202`；外部合同 footprint SHA-256 为
`912c8fd868d409b2ef6326007e5879cd4fbbc83b3c26c81ae986c0a0ae29b4be`，preflight SHA-256 为
`e53c8f894df5f8d32c6cb09661c71e51ca91723d365c5d10f01b8b8cae6ef903`。当前只到
`ready_for_isolated_shadow_fixture_not_formal_execution`，全量测试 `306 passed`，没有实际工具干预结果。
记录提交为 `90bf0bf`，内容归档 SHA-256 为
`5fe4157e2901f982ee4b8822a8140f512a2c4fe5ced8d3c7d9aa24a3faec92ee`。

source-manifest 增量门禁冻结于 commit `12cd18e9790fe67503709406c007d49cd5f677eb`：知识库 15 个
allowlisted 文件的 manifest SHA-256 为
`402a7be05785ce2fbbf9e8be4d714af1aa6952aee26f60de17f8ee1bf7e4cad4`，PepShot 32 个文件为
`b9ab9ecb88d6d82c3e93d28909702ddd2b56632c437df5bd60627892258519fa`。当前 config SHA-256 为
`6ba458badbe8bb7e4446c9120b5cd5387f547f81c19812060868318c295e3388`，外部 footprint 为
`8f792f0e780ae14a265821bea3a672881982cd3d397fe4627b79eb273a3394ec`，preflight 为
`c58b3af9f94c4e8cdf2d08167f647f5bba22121fac953ef2cfcde7447b711406`。这只推进 Q3/Q4 的可复原工程
底座；尚未运行知识检索、PepShot、shadow episode 或正式消融，也没有工具效果结果。实际 Python/PyMOL
运行时仍须独立冻结并与 source manifest 对齐。
该 checkpoint 的记录提交为 `696f696`，内容归档 SHA-256 为
`0f8f44fce80a43e489df62694520ae4e7b62b9134a832a149d2a72a27ceab91a`。

2026-08-11 新增工具所有权原则：Q3/Q4 的 challenger 必须消费知识库/PepShot 自己发布并冻结的
revision、runtime fingerprint 和 fixture receipt；AMPgent 不维护 provider-specific 兼容补丁或替工具
修环境。消费侧可以拒绝漂移或缺证据的交付，但不能通过本地 monkey patch 把失败包装成可用。当前
PepShot renderer 缺声明的 gemmi，知识库也尚无满足完整 requirements 的官方隔离 runtime；修复已退回
对应任务。在两项 provider-owned 交付完成前，v34 shadow 保持未运行，不能据此评价工具增益。
消费侧 runtime verifier 已实现于 commit `43fbe926cc8f3dcc0abc0231502b9079bc1a2368`，全量测试
`309 passed`；它只负责拒绝不合格交付，不替 provider 修复。
provider gate 记录提交为 `6e38bdb`，内容归档 SHA-256 为
`6e813011781cba3f6d9ea255381f870d8401fdd81226808c20b034fcd76fc8c9`。

用户进一步明确：对 PepShot 的不满意必须直接退回 PepShot 任务
`019fb910-f2dd-7be1-a7e6-bfe381512c25`，不得由 AMPgent 自行适配。退回内容至少包含可复现输入、违反的
冻结合同、期望验收标准和证据落库要求；PepShot 必须在自身任务/仓库完成修复并发布新 release，AMPgent
只做只读验收。该边界同时覆盖接口兼容性、renderer/runtime、输出 schema、证据完整性和科学审阅语义。

两项 provider 已在自身仓库修复并发布不可变 release，官方 verifier 与 AMPgent 只读消费门禁均通过。
这把 Q3/Q4 从“provider runtime blocked”推进到“ready for database-native isolated shadow”，但仍无工具
效果结论。下一验收点是：完整 release 与 receipt 进入 PostgreSQL/对象存储证据图，数据库-only replay
通过后，才允许申请 v34 正式 2×2 消融授权。

该边界与消费门禁冻结于 commit `96b4e02732939544a8b7a939de312e7d22ff0ad2`，config revision 记录于
`d24a55c1d2ab0f8276d09a904118f5d9e8224a3d`；全量验证为 ruff clean、pytest `313 passed`。内容归档
SHA-256 为 `4196f9ffb4017bcf2ed44e89d8b69c8e5aae611145f41abb6a72a807dad9ab05`。

数据库原生 provider shadow 随后已完成并锁定。唯一 run 为
`941ea473-82d6-4b70-9ede-5162a14bf8ce`，0 Candidate、0 Evaluation；两项完整 release、验收 receipt、
依赖图、AgentDecision 和 replay bundle 均已进入 PostgreSQL/内容寻址对象存储。replay bundle SHA-256
为 `390d5757ee55d7a010b66701b4d6fe0338eb97f4d84b87c20b572f74cc9ae73c`，结论仅为
`provider_releases_replayable_for_v34_authorization_request`。因此 Q3/Q4 的工程可复原阻塞已解除，但
效果问题仍为 `in_progress`：没有 knowledge/PepShot 增益结果，也没有正式 v34 授权。

正式 v34 的授权对象、计算上限、provider 退回路径、执行门禁与数据库完成定义已独立整理为
`docs/ampgent-v34-formal-authorization-request.zh-CN.md`。该文件只消除授权歧义，不改变预注册协议，
也不构成运行授权。

PepShot 治理规则再次锁定：若后续实际对照对 PepShot 不满意，AMPgent 不做兼容、修补、后处理或语义
猜测，而是把可复现失败与冻结验收标准直接退回任务 `019fb910-f2dd-7be1-a7e6-bfe381512c25`。PepShot
必须在自身仓库发布新不可变 release，AMPgent 才重新只读验收；change request、拒绝、新 release 和
receipt 都必须进入相应 Agent evidence graph。历史“adapter”一词仅指严格 consumer validator，不代表
允许适配 provider。

该治理路径现从文档规则升级为 v34.1 可重放合同：固定 governance ToolCall 保存 ownership、冻结 release
和显式空/非空 change-request ledger；非空 ledger 必须连接拒绝 run 与独立 child run，并保存复现输入、
合同违例、验收标准、外部任务 receipt、替代 release 和只读复验 receipt。正式 run 内禁止 release 热换，
避免同一消融混入两个 provider 版本。实现 revision 为
`9e879fec1285d2a1071fde7cd2d874765409aa24`，config SHA-256 为
`b6adc410f99185f1f25c6205c57dc89223c0d685f5c2b80084a8cb39106318e6`，evidence plan SHA-256 为
`67020e0241cf2eb0dae954e9dd8767a5321207ea3b1b656aacd69d62f35f4939`；全量测试 `368 passed`。
这仍只证明 Q3/Q4 的退回治理可预注册和复算，不证明知识卡或 PepShot 有效，也不授权正式 v34。
revision/文档回填 checkpoint 为 commit `f76a8101588f2c34ecfce21ea941aaf30d3db96b`；内容归档
`var/archives/ampgent-v34-provider-governance-f76a810.zip` 的 SHA-256 为
`64b2db2692b2b640e5d3d3b91e03de4ee621a8c1a7f53c873c7689e7b642da54`。

v36 当前框架见 `docs/ampgent-v36-harness-evolution.zh-CN.md`，精确合同为
`config/benchmarks/amp_harness_evolution_v36.yaml`。typed lineage schema、迁移、repository primitive 与
离线 replay verifier 已在仓库实现，但尚未部署到共享 PostgreSQL，也未做合成数据库 acceptance；因此
不授权真实历史 replay、shadow、前瞻 trial 或候选生成。下一独立阶段仅部署迁移和做合成闭环验收，
真实演化仍需再授权。

实现 checkpoint 为 commit `c185476a0db34bb2cf802aba89299a8593520abc`，config SHA-256 为
`8ce6fe07689851c354ecb01cc620f081d80c9ede03ee6a81e7e6a3964a0f2528`，内容归档 SHA-256 为
`a0e6f32464e37d44193a4fe2efd1cdc16a7dc6b19d5f70d643d12d1ce87d5c3c`；全量测试为
`334 passed`。repository 现在会在写入时拒绝跨 scope parent/rollback、非祖先回滚、未通过上一关的
trial、缺失独立裁决 run、非配对或未完成 outcome，以及完成后追加 assignment/outcome。该结果仍只是
可验收的工程底座，不是 harness 增益证据。

版本号是当前规划，不是正式 run 授权。任何生成、阈值、候选选择或执行必须先有独立冻结 config、
提交/push、服务与 worker 门禁、唯一 run 检查。长期路线允许被新证据修订，但修订必须追加理由，不能
静默改写历史问题或成功标准。

## 6. 数据库原生要求

后续每个 Agent episode 至少持久化：

- 输入问题、目标、预算、harness/policy/prompt 版本；
- 检索 query、source/card/passage、结构输入、工具调用及完整依赖；
- 原始候选、parent/child 关系、逐步变异理由和所有评价；
- Pareto archive 的每次增删、支配证据、lane 决策、排除与停止理由；
- Agent 的 observation、decision、tool edges、重试、失败与人工输入；
- 所有原始输出、图像、manifest、环境和 replay bundle 的内容寻址 artifact；
- champion/challenger 分配、盲化信息、评价结果和晋级/回滚决策。

正式阶段只有在 database+object-store-only replay 能重建候选顺序、每次决策、archive 演化、最终组合
和停止条件时才完成。外部知识库或 PepShot 自己的数据库必须以 immutable source/artifact 引用和
ToolCall 边进入 AMPgent run；“能在另一目录找到”不满足可复原要求。

## 7. 维护规则

每次新事实出现时更新对应问题的 `当前判断`、证据 ID/SHA、未解决项和下一项预注册实验。问题只能
标记为 `open`、`in_progress`、`answered_within_scope` 或 `blocked`；不能因一次最好分数标记“解决”。
每个大版本结束时追加：回答了什么、没有回答什么、哪个假设被推翻、harness 学到了什么。

定时 Agent 必须作为持续研究循环运行，而不是周期性仪表盘读取器。每次唤醒都要从当前证据选择并执行下一项
最高价值动作，形成实验、修复、验证、持久化证据或候选报告中的至少一种前向增量。5 分钟进展检查用于发现
activity/count 停滞，15 分钟计划复核用于改变策略，120 分钟只规定面向用户的 review 节奏，不构成休息或等待
许可。连续两个应有进展的窗口没有证据增量时，必须把停滞解析到具体执行边界并采取最小安全行动。

失败 workflow 只冻结该次身份，不冻结长期目标。Agent 必须立即保留并解释部分科学产出、标注缺失证据、用
真实部署边界复现故障并开始版本化修复；在精确 smoke 通过前不得用完整 science run 代替基础设施验证。只有
用户明确暂停、安全/资源非干扰边界、科学合同需要改变且必须由用户决定，或外部依赖已穷尽安全替代方案时，
才允许停止主动推进。即使进入 `blocked`，仍须推进所有不依赖该 blocker 的工作。

L0–L5 成熟度与 Q1–Q6 完成证据矩阵 checkpoint 为 commit
`db252e2b0713c4966fd414ab802f72eac9797b94`。内容归档
`var/archives/ampgent-long-horizon-maturity-db252e2.zip` 的 SHA-256 为
`334ad637931a4a3c31a5f0ee3b1d34ed2905257a2d7b49422d0d61e85abb13de`；全量验证为 ruff clean、pytest
`362 passed`。该 checkpoint 只强化路线图维护，不改变任何执行授权。

2026-08-12 后续工程恢复事实：唯一 v37.0.3 run 在生成任何候选前因父 worker 的 Python 3.11
`PYTHONPATH` 污染独立知识 provider Python 3.12 而失败；Candidate、Evaluation 与 AgentDecision 均为 0。
该 run 作为不可变工程失败保留，不构成短肽质量、知识卡效果或科学结论。独立
`v37.0.4-subprocess-environment-recovery` 仅隔离 provider、metric 与 generator 子进程环境，科学模型、
seed、预算、结构协议、Pareto 和停止条件不变。其完成仍只用于追求一次数据库可回放的高质量 champion
portfolio，不改变 Q1/Q2/Q6 的回答标准，也不提前恢复已延期的工具消融。

2026-08-13 资源边界勘误：`.19 GPU4` 可供 AMPgent 使用；`.32 GPU2/GPU3` 是绝对禁区。
`.32 GPU0/GPU1` 及其他共享卡必须先与任务 `019fcd9b-a14e-7741-a3ff-2fd0e1d3d4c7` 协调精确归属，
不得抢占。当前协调结果将 `.19 GPU4/GPU5` 明确留给 AMPgent，其他卡仍有外部任务。该勘误只提高
v37 的安全执行容量，不改变科学问题、固定预算、评价端点、Pareto 规则或长期成熟度判据。

2026-08-13 v37.0.4 追加事实：唯一正式 run
`57a30fb0-e373-40ab-a629-1b22756bc70f` 在九个冻结生成 activity 返回后、候选持久化前，因 attempt-ledger
投影错误地用 `v37.attempt-event.1` schema 校验合法 launch-receipt 事件而失败。数据库中为 0 Candidate、
0 proposal occurrence、0 Evaluation、0 AgentDecision，因此没有新的可解释短肽结果，也不能更新 Q1/Q2
或声称搜索进展。该结果只暴露 persistence-verifier 的事件分类顺序缺陷；修复必须版本化、带对抗 fixture，
且不得从 Temporal 返回值或本地文件回填失败 run。v37.0.4 保持不可变 failed，新的正式恢复不由本条自动授权。

2026-08-13 最新 GPU 追加勘误：`.19` 可在每次定时巡检中只读检查，并仅使用当次核验为空闲、无外来进程
且归属清楚的 GPU；`.32 GPU0/GPU1` 可供 AMPgent 使用，但检查必须只显式定向 `0,1`。`.32 GPU2/GPU3`
继续为绝对禁区，不访问、不探测、不使用，也不得用整机枚举间接获知其状态。空闲集合变化由 30 分钟 heartbeat
调用只读脚本并唤醒本任务复核；它不自动部署 worker 或提交 run。该规则不改变科学预算、Pareto 规则、
v37.0.4 不可变失败状态或 formal-run 授权边界。

2026-08-14 第一性原则追加：当前下一恢复版本不是为了证明门禁或部署工具先进，而是为了尽快获得第一批
可解释短肽并完成单臂 champion portfolio。v37.0.4 以前的失败证据继续不可变，下一版本使用独立身份，
不得回填或复用失败输出；同时保持 900 条候选、五类序列评价、48 条结构短名单、每条 3 个 Boltz seed、
每 pose 16 个 Rosetta decoy、非加权 Pareto 与数据库/对象存储 replay 的科学预算不变。worker 记录按
host、GPU/CPU、PID/role、source revision、无外来冲突的最小集合执行；其余不影响短肽结果、证据闭环
或资源安全的仪式性检查不得延迟 Candidate/Evaluation 落库。routine 修复默认直接推进，直到出现真正
会改变科学意义、造成证据丢失、重复提交、资源冲突或需要扩大用户授权的边界。

2026-08-14 v37.0.5 执行事实：结果优先恢复版本已作为唯一正式 run
`1655ba61-f380-4669-8b03-ccda4ae33c7d` exact-once 启动，workflow 为
`pepagent-rapid-champion-v37-ab6d1d6b70b82262dc2d4408f6644cfcd0fabfbf03942af6bfc6ac830611844e`。
该版本只修复 v37.0.4 的持久化事件分类顺序并换用独立身份；900 条候选、五类序列评价、48 条结构短名单、
每条 3 个 Boltz seed、每 pose 16 个 Rosetta decoy、非加权 Pareto 与数据库/对象存储 replay 均不变。
当前问题仍为 `in_progress`：只有候选、评价、结构和最终 portfolio 实际持久化后，才更新 Q1/Q2/Q6 的科学判断；
运行启动、worker 在线和 GPU 可用本身不构成短肽质量结论。

2026-08-14 v37.0.5 失败与恢复追加：唯一 run `1655ba61-f380-4669-8b03-ccda4ae33c7d` 已在 900 条候选和
9000 条 proposal occurrence 落库后失败；Evaluation 与 AgentDecision 均为 0，因此没有可解释排序或
Pareto 结果，Q1/Q2/Q6 不更新。根因是消费者错误要求 ToxinPred3 的全部输出与冻结选择严格相等；provider
除合同所需 hybrid score 和 label 外还合法返回 ml score。修复只将冻结声明的指标投影为 Evaluation，额外
输出仍进入原始 replay，不参与选择。独立 `v37.0.6-metric-observation-projection-recovery` 保持全部科学预算
和 11 个 required metrics 不变，目标仍是尽快获得 900×11 条序列 Evaluation、48 条结构短名单和最终非加权
Pareto portfolio；v37.0.5 保持不可变且不得回填或重跑。

2026-08-15 v37.0.11 追加事实：唯一正式 run 已在完整持久化 900 条候选、9,000 条 occurrence、
9,900 条序列评价和 48 条结构短名单后，因 `.19` 的冻结 Boltz worker 环境缺少实际 `boltz`
可执行文件而失败；Boltz 推理未开始，结构证据为 0，也没有最终 Pareto portfolio。该失败只说明
worker runtime 验证不足，不更新 Q1/Q2/Q6 的短肽科学判断。v37.0.11 保持不可变且不得回填或复用；
下一次独立恢复必须先以真实 provider smoke 证明 Boltz 可执行、包、CLI、权重与目标 GPU 环境共同可用，
同时保持 900/11/48/3/16、非加权 Pareto、数据库/对象存储 replay 和不做消融的科学合同不变。

2026-08-15 Boltz runtime 修复追加：`.19` 已具备可执行的 Boltz 2.2.1，但首次真实 GPU4
AceA-pocket smoke 在推理前暴露第二个部署缺口——`.19` 没有公网路由，Boltz 本地缓存又未包含
CCD 和结构模型。该 smoke 未产生结构 artifact，不是短肽质量、Boltz 模型或 GPU 失败证据。
权威大文件位于 synth `/sdd_data/pepagent/models/boltz2/cache`；当前以不落本地大文件的流式
SSH 输送到 `.19`，只有完整 SHA-256 一致才提升为可用缓存。下一小阶段是完成输送并通过
同一真实 provider smoke；在此之前不启动新正式 run，冻结科学合同不变。

2026-08-15 Boltz runtime 修复完成：两个大缓存对象已经过流式 SSH 输送到 `.19` 并通过
SHA-256 校验，全程未在工作区落地大文件。第二次真实 GPU4 AceA-pocket smoke 已完成一次
Boltz-2 结构预测，紧凑输出 SHA-256 为
`892373b095e8a4b5fa777df98fdd0f76ed61f82e85f24918502e9b06853abf73`，且 CIF、PAE/PDE、pLDDT、置信度与约束产物都存在。
这只证明 Boltz 可执行、包、缓存、GPU 和 AceA 口袋输入能共同工作；哨兵肽的分数不是 v37
候选证据，不更新 Q1/Q2/Q6，也不授权新正式 run。

2026-08-17 v38 framework-only 追加：用户终止 v37.0.15 并要求在再次生成前重构 Agent。旧 run 已闭合
为 `cancelled`，900 Candidate、9000 occurrence、11468 Evaluation、282 succeeded ToolCall 与 2 个
Decision 保持不可变。新框架把“继承历史”定义为对全部 succeeded/failed/cancelled run 的按时间截断、
内容寻址、只读证据快照；失败和取消必须留在分母，旧候选与输出不得复制、回填或跨 run 复用。该能力复用
v36 typed harness lineage，用来改进证据到决策过程，不构成新的肽科学结果。

同一追加将序列阶段改为知识卡前置、全量有效 proposal 评价、多轮父子改写、双 MIC/毒性/溶血/理化/
域外/稳定性成熟度准入，只有成熟序列才进入结构；模型冲突保留为探索证据但默认不消耗结构预算。多靶点
能力复用 v35 qualification witness，要求在肽结果前冻结至少两个靶点，让同一成熟序列 cohort 进入相互
隔离、可并行、等预算的 target branches，AceA 不再是唯一靶点。当前只完成框架合同和验证，不执行目标
选择、候选生成或结构 run；因此 Q1–Q6 的科学状态不因该工程升级而改变。

2026-08-17 v38.1 追加：外部 MIC/活性阈值不再作为一刀切硬门，避免模型标定或实验条件差异导致整批清空。
有效性、缺失/域外、毒性和溶血维持严格；MIC、AMP 活性及理化可开发性使用非加权 Pareto。安全但模型冲突
或排序不稳的候选可进入不超过结构预算 20% 的固定探索 lane，成熟核心不足则触发最多三轮知识卡可追溯
refinement，不降低安全门、不强制补满。

第一真实多靶点面板已冻结为 GyrA/LEI-800 与 PBP2a/allosteric，均有独立坐标、native/wrong-pocket 和证据
namespace。控制 run `b931b9df-c618-4d89-a1d1-ec52acc6e74e` 已继承 54 个终态历史 run 并冻结用户指定知识卡
provider 的 context-pack SHA；当前只有控制/预检身份，未提交正式科学 workflow，候选、评价、结构和决策
计数均为 0。下一科学进展必须来自兼容 score-all、迭代 refinement 和隔离多靶点分支的新执行器，不能退回
旧 v37 单靶点 first-100 流程；因此 Q1–Q6 尚不更新。

2026-08-17 v38.2 追加：序列准入不再使用固定 MIC 差值阈值。两个 MIC 模型必须都成功并作为独立非加权
Pareto 轴，避免“一个都进不来”，同时保留模型分歧。安全硬门只采用 provider 合同标签；只有第一非支配
前沿进入成熟核心，被支配但安全的序列进入有界探索或知识卡 refinement。成熟核心不足 12 条时不上结构，
而是最多三轮带父本 control、知识 passage 哈希和完整 11 指标重评的改写；当前仍未提交正式科学 workflow。

2026-08-20 v39 靶点身份门追加：`target qualification` 不再只依赖面板声明和文件 SHA。正式 preflight 必须从
数据库注册序列与独立坐标链序列重新计算 coverage/identity，并核对注册物种/accession 与坐标来源
物种/polymer accession。direct experimental 与 homology 两类证据分开；跨物种同源结构不得冒充直接实验
结构。verified witness 的 SHA 进入 exact-once submission identity，使“多靶点”既能扩展，也不会把错误靶点
元数据规模化复制进企业管线。
### 企业模型/实验注册门

下一次正式科学运行不得仅凭“已有两个打分器”启动。机器必须先用
`config/enterprise/ampgent_model_assay_registry_v39.yaml` 对照企业证据域合同完成资格审计：
模型的终点语义、训练域、部署 runtime 哈希、商业使用许可、独立验证、校准与 OOD 证据均需
版本化绑定；同一 `independence_group` 的多个模型只算一个独立证据源。shadow、未验证、blocked
或 retired 模型不得满足最低数量。缺任一必需证据域时保持
`formal_science_run_authorized=false`，优先补验证/校准或安排区分性实验，而不是扩大候选库或提前
消耗结构 GPU。

模型资格门关闭时，定时 Agent 必须从
`config/enterprise/ampgent_evidence_acquisition_backlog_v39.yaml` 领取可验收任务；默认每轮最多
3 项、240 CPU 分钟、0 GPU 分钟。排序按优先级、可同时消除的证据域数量、GPU/CPU 成本和稳定
task id，不能用模糊总分。只有声明的哈希化验收产物全部形成才算完成；许可证覆盖不明、数据泄漏、
终点语义不清或 OOD 不可复现时必须停该任务并换策略，不能把 smoke 或“模型能运行”冒充晋级。

2026-08-21 v39 溶血证据追加：Macrel 1.6.1 不能作为 HemoPI2 的第二独立来源。固定训练代码证明
`Hemo.onnx` 直接使用 HemoPI-1 main 数据；精确序列审计显示其训练集与 HemoPI2 cross-validation
重叠 167 条、与 independent 重叠 32 条，且后者有 6 个标签冲突。该负结果触发预注册 stop
condition，未执行会被训练泄漏污染的校准，也未降低安全门。下一实验必须从 Hemolytik/DBAASP
等不同数据谱系中筛选可商业执行、可复算且有独立校准/OOD 的模型；在此之前溶血域仍不满足企业门。

2026-08-21 v39 第二溶血证据族资格筛选追加：已审计 BERT-HemoPep60、HemoPI、MLpeptide、
HemoNet、PeptideBERT、HAPPENN、tAMPer 与 Hemolytik2 八个高优先级公开候选。没有候选同时
通过独立数据谱系、商业内部使用权、不可变权重、可复算概率和 sequence-first 兼容性；因此不运行
会重复计算同源证据的 benchmark，不放宽安全门，也不提交正式 science run。下一关键路径改为
前瞻性独立 RBC 溶血实验与模型合同：冻结物种/供体/浓度/时间/读出、原始测量与删失 schema、
数据所有权、既有数据库序列排除以及预注册 train/calibration/OOD 拆分。当前候选只能用于最终
盲测，不能参与安全阈值拟合。

2026-08-21 v39 前瞻性溶血合同追加：主端点不再是脱离条件的“溶血/不溶血”标签，而是至少 3 位
独立供体、至少 2 个实验日的去纤维蛋白人 RBC 浓度响应。EDTA 与非人 RBC 只能保留为独立 bridge
视图；原始吸光度、干扰空白、失败/复测、供体曲线和 HC10/HC50 删失必须可重放。当前 773 条候选
完全排除于训练、校准、模型选择和阈值拟合，只能在实验与模型锁定后盲测，因此不会因预设一个外部
阈值而把候选池提前清空。该 checkpoint 只冻结采集/建模合同，尚无新湿实验安全证据。外部 provider
pilot 作为后续任务保留；等待期间优先补血清/蛋白酶稳定性证据域，避免研究循环停滞。

2026-08-21 v39 稳定性合同追加：血清稳定性主端点为至少 3 位独立人供体、至少 2 个实验日的
50% serum intact-parent LC–MS 时间曲线；供体不可混池。trypsin、chymotrypsin 和 human neutrophil
elastase 仅作独立机制诊断，不与 serum endpoint 合并。降解 fragment 单独持久化，不能冒充 intact parent；
低于 LLOQ 和未达到半衰期保留删失。当前 773 条候选只允许在 assay/model lock 后盲测，不参与模型、
split、校准或 operating point，因此该合同不会提前清空候选池。当前仍没有新稳定性湿实验或正式候选门；
下一独立任务转为溶解度/聚集条件矩阵与原始测量合同，同时保留稳定性 provider/pilot 的外部执行路径。

2026-08-21 v39 溶解度/聚集合同追加：pH、盐度、浓度、温度和时间被保留为显式条件轴；未过滤浊度、
DLS 原始粒径分布、固定离心后的可溶 intact parent 与可见沉淀是分离端点。高浓度预孵育后的十倍稀释
复测用于区分可逆自聚集与持久颗粒，禁止用过滤后样本掩盖大聚集体，也禁止把 DLS 无信号解释为
“无聚集”。当前 773 条候选只作 assay/model lock 后盲测，不能定义 split、模型或 operating point。
该 checkpoint 没有新增湿实验开发性证据；provider/reference pilot 留作后续，下一独立合同转向
serial-passage resistance propensity 与 cross-resistance，继续补齐企业核心管线而不消耗结构 GPU。

2026-08-21 v39 耐药倾向合同追加：E. coli 与 S. epidermidis 使用相互隔离的 30 代 serial-passage
lineages、固定祖先 MIC 相对暴露梯度、无药 controls、每 5 代归档/MIC 复测和末端 5 代无药稳定性挑战。
population extinction 与 MIC 漂移是分离的 competing outcomes；灭绝 lineage 不得复活后进入主分析。
cross-resistance 同时覆盖宿主防御肽、colistin 及靶点相关抗生素，WGS 覆盖祖先、群体和 clones，但基因变异
只有在独立重建/互补后才可称因果。当前 773 条候选只作 protocol/model lock 后盲测，因此本 checkpoint
没有新增耐药实验结论或正式门。下一独立任务转为 phenotype-grounded AMP-likeness reference/model 合同，
专门避免用数据库 membership 和随机负序列制造虚假的模型性能。
