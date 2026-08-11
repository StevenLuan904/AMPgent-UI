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

后续工程闭环已实现但尚待冻结：parent/child、descriptor Evaluation、文献与指标 ToolCall 依赖、逐
checkpoint archive、dominance witness、saturation AgentDecision 和对象存储 artifact 可形成数据库原生
证据图，并可只用 PostgreSQL 与对象存储精确 replay。lost-response retry 恢复原身份而不追加新变体。
在 executable revision 提交、归档并获得另行授权前，这仍不表示 v33 已执行或产生结果。

### Q2：当前 Pareto 搜索是否已经接近可达最优？

当前判断：`in_progress`。v32 只证明冻结样本内的非支配组合，不能证明搜索收敛。v33 已冻结
25/50/100/150/200 的全预算 checkpoint、family-local ε-cell、archive turnover 和独立确认 seed
判据；尚未执行，因此没有新的饱和结论。

要回答的问题：

- 随预算增加，archive turnover、新增非支配候选率和新占据的 family-local ε-cell 是否趋稳？
- 独立 seed/生成器的 attainment 是否重合，还是持续发现不同区域？
- 去掉任一相关软模型、对有限数值作容差内扰动后，lane 与候选身份是否稳定？
- 每单位 GPU/CPU 时间、ToolCall 和候选预算能带来多少新的可复原非支配发现？

方法要求：使用预注册的逐批预算曲线和独立确认 seed。报告 archive turnover、ε-coverage、
cross-seed attainment、novel discovery yield、leave-one-model-out 稳定性；不以单一 hypervolume 或
加权总分宣布收敛。只有连续多个预注册批次的新发现率进入开发前定义的低区间，并在独立 seed
复现，才可称“在该协议和预算范围内经验饱和”，不能称全局最优。

### Q3：文献知识卡是否真的提高设计质量？

当前判断：尚未正式接入并验证。现有平台接口可容纳卡片，但动态检索、passage 绑定与正式 Agent
决策边尚未形成端到端对照。

建议实验：冻结 parent、seed、预算和所有模型，比较 `no_cards` 与 `verified_cards`；后续可增加
`retrieval_without_rerank` 和 `context-matched_cards`。知识组只能读取与任务上下文匹配、来源 passage
可定位且状态为 verified 的卡。主要评价不是 prompt 好看，而是：有效新颖非支配发现率、无效修改率、
重复失败避免率、机制多样性、决策可解释性和独立 shadow 端点。所有 query、候选 card、rerank、
passage SHA、prompt、response、采纳/拒绝边必须落库。

### Q4：PepShot 是否帮助 Agent 避免结构性错误？

当前判断：工具已成熟到可做 shadow evidence，但未进入 v32 决策。

建议实验：同一批冻结结构做 `PepShot off/on` 配对；与知识卡形成可解释的 2×2 设计：baseline、
cards-only、PepShot-only、cards+PepShot。PepShot 只影响结构审阅、追加视图和是否升级/修改，不得
偷偷改变生成预算。评价结构冲突拦截率、误报、审阅一致性、追加视图成本、后续 Rosetta/多 seed
方向稳定性和“高软分但明显几何异常”候选的减少。PepShot finding、图片、query、review、验证结果、
artifact SHA 和 Agent 决策边全部进入主证据图；只存在外部工具数据库不算接入完成。

### Q5：方法能否泛化到别的靶点？

当前判断：未回答。

进入多靶点前先冻结 target qualification：生物学假设、序列/结构来源、pocket 定义、结构完整性、
阴性/错口袋对照、可比较协议和不得声称的结论。第一阶段建议使用 3–5 个具有不同 pocket 形状、
同源性和作用机制的细菌靶点，并保留 target-agnostic AMP lane。比较同一 harness 的成功率、前沿形状、
结构稳定性、候选跨靶点复用和失败类型；不能把“对多个口袋都有有利 REU”称为广谱靶向或选择性。

### Q6：怎样利用历史尝试进行 harness evolving？

当前判断：v32 的 typed evidence graph 与 replay closure 提供了必要底座，但尚无正式的策略晋级器。

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

## 5. 分阶段路线与晋级门

| 阶段 | 核心交付 | 晋级前必须证明 |
|---|---|---|
| v33：正电性与搜索充分性 | 显式电荷设计、匹配反事实、顺序预算曲线 | 不靠更高风险换取全部收益；给出协议内经验饱和或明确未饱和结论 |
| v34：知识/视觉干预 | knowledge card × PepShot 2×2 同预算消融 | 至少一个预注册实用端点改善，且成本与失败也完整报告 |
| v35：多靶点迁移 | 资格审计后的 3–5 靶点面板与对照 | 区分通用、靶点特异和失败策略；不能只挑成功靶点 |
| v36+：持续 harness evolution | champion/challenger 晋级器与策略谱系 | 离线 replay、shadow、前瞻对照三关通过；可一键回滚和完整复原 |

v33 当前叙事预注册见 `docs/ampgent-v33-charge-search-preregistration.zh-CN.md`。其状态为
`draft_not_authorized_for_execution`；确定性 K/R dose block 与逐 checkpoint archive 已实现并有测试，
下一工程任务是 PostgreSQL persistence activity 和 database+object-store-only replay verifier，而不是
提交 formal run。

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
