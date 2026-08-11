# AMPgent AceA v37 单臂快速优选生成预注册

状态：`preregistered_not_authorized`

精确合同：`config/benchmarks/amp_rapid_champion_generation_v37.yaml`

## 1. 为什么是单臂

用户当前优先级是尽快得到一批证据较完整、机制互补的高质量计算候选，不做知识卡或 PepShot 的工具
消融。v37 因而只有一个 `rapid_champion` arm。已经通过只读验收的知识卡和 PepShot release 作为默认
辅助进入流程，但本阶段不回答它们是否有效，也不把“用了工具”当作候选质量证据。

这里的“champion”只表示在 v37 冻结生成器、指标、seed、预算与选择规则下进入最终组合的计算候选，
不是实验活性、安全、AceA 结合、亲和力、选择性或序列空间最优结论。

## 2. 固定预算与速度取舍

v37 把 HydrAMP、AMPGAN v2 和 AMP-Designer 作为同一条 champion arm 内的三个 proposal engine，
不把它们当作三个对照组。依据是锁定的 v31 结果中三者 non-dominated：AMPGAN v2 的代表 Rosetta
dG 中位数更有利，HydrAMP 的口袋覆盖更高且肽骨架位移更低，AMP-Designer 的 pair-ipTM 略高。
因此只用 AMP-Designer 会无依据地丢弃互补优势。HydrAMP 使用 v23 已验证的 raw-unfiltered de novo
adapter；v24 高温 analogue 需要父序列与 cell seed 等另一套合同，不能混入本版本。AMPGAN v2 不使用
v24 未晋级的安全优化 arm，AMP-Designer 使用冻结 adapter。

每个生成器固定三个新 seed，每个 generator×seed 生成 600 条 raw proposal，并只按原始顺序保留
前 60 条合法、全局唯一序列进入便宜序列指标阶段，正常情况下共 540 条。全局序列唯一性按冻结的
generator×seed 顺序执行；重复、非法和不足都进入失败分母且不补抽。昂贵结构阶段仍固定为 48 条：
四个机制 lane 各 12 条，并限制每个 generator 和 generator×seed 对每个 lane 的最大贡献。

每个结构候选固定运行两个 Boltz seed；两个姿势都做坐标审计并各跑 8 个 ref2015 Rosetta decoy。
最大计算量为 96 个结构姿势和 768 个 Rosetta decoy。全部 48 条都必须走 PepShot 的冻结
verify→读取全部请求图片→validate-review 路由。最终 portfolio 上限为 16 条，每个 lane 4 条。

固定预算必须完整执行，不允许看到好结果后提前停，也不允许因某个 seed、lane 或工具失败而补抽。
shortfall 会原样报告。这样既保持探索力度，又避免把快速阶段变成无限追加计算。

## 3. 多目标质量体系

候选质量保持五类独立证据，不做加权总分：

1. 膜作用描述符：疏水矩、疏水比例区间、最长连续疏水段；
2. AMP/MIC 软预测：Macrel AMP probability、LLAMP 与 AMP-READ MIC proxy；
3. 风险软预测：ToxinPred3 与 Macrel hemolysis；
4. AceA 结构/Rosetta：pair-ipTM、口袋覆盖与接触、碰撞、肽骨架位移以及同协议 Rosetta dG；
5. 多样性：全局序列唯一、lane 内 seed 上限和 normalized-Levenshtein maximin。

只有毒性与溶血两个体系同时给出红旗时才按冻结规则排除；单模型红旗仍可进入，但必须显著标注。
这只是软风险治理，不是安全证明。Rosetta REU 只在 v37 同协议内相对比较。

第一阶段在 membrane、activity/MIC、risk-control、balanced 四个 lane 内分别计算 Pareto 层，再用
确定性的序列 maximin 选出 48 条。第二阶段加入结构与 PepShot 证据后，形成 membrane-led、
activity-led、structure-led、balanced-risk 四个最终 lane。先使用 lane-local 非支配层，再做多样性选择；
任何候选不会跨 lane 重复。没有全局冠军，也没有隐藏的综合分。

## 4. 知识卡与 PepShot 的角色

知识卡必须来自冻结 release、verified card 和可定位 passage。它可以标注机制、适用范围和警告；只有
直接适用且有精确 passage 的 verified warning 才能形成排除理由。知识支持数量不能成为正向分数，以免
文献较多的机制天然占优。

PepShot 对每个结构 shortlist 候选做冻结路由审阅，可保留、标记证据不足或排除明确结构冲突。证据不足
和结构冲突都不能进入最终 16 条，但仍保留在完整失败分母。本阶段禁止根据 PepShot 临时追加 generation、
revision 或结构 seed。若 provider 不满足合同，AMPgent fail closed 并把缺陷退回其 owner task；不写兼容
层、不热换 release。

由于 v37 没有 off arm，最终只能说“候选在 knowledge/PepShot 辅助流程下产生”，不能说两项工具提升了
质量，也不能回填 v34 的未回答效果问题。

## 5. 正电性边界

净电荷只记录为 provenance，不用于 conditioning、mutation、阈值、Pareto 目标或 tie-break。v37 不从
项目自产分布推导生物学最优电荷，也不替代 v33 的显式正电性问题。

## 6. 数据库完成定义

run manifest、九个 generator×seed 的 raw 输出与顺序、合法性/去重/保留 witness、全部 Candidate、Evaluation、
ToolCall、dependency、knowledge query/card/passage、Boltz/Rosetta 输入输出、PepShot bundle/image/review、
两阶段 AgentDecision、Pareto/dominance/diversity witness、失败、重试、成本和停止理由必须进入
PostgreSQL；大对象进入内容寻址对象存储。

只有 database+object-store-only replay 能精确重建 540 条正常候选、48 条结构 shortlist、全部姿势与
decoy、所有排除、最终 lane 顺序、shortfall 和停止理由时，v37 才算完成。CSV、JSON 与 Markdown 只是
导出，不能回填证据。

## 7. 执行边界

用户关于快速获得高质量结果的最新指令已记录为 v37 formal direction authorized；这不是越过门禁的
执行授权，也没有提交 workflow。正式执行前必须完成可执行实现、内容归档、全量测试、数据库
schema 验收、服务与 duplicate-run 检查，以及所有 worker 的物理主机、GPU、PID、角色、task queue、
源码/release 映射。绝不使用 `192.168.99.32` 或 `.19` GPU4，也不停止、争抢或干扰他人任务。

不再要求用户提供额外固定短语。只有全部预执行门禁实际通过并由主任务记录执行授权后，才可注册正式
activity、生成序列或提交唯一 run。
