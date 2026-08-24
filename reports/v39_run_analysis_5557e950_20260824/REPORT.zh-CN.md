# v39 四轮序列探索：数据分析与可视化报告

生成时间：2026-08-24T03:05:40.722475+00:00
用户给定任务：`019fb225-0b2b-7b20-b258-24c1924f560e`（Codex 任务“MVP”，不是 PostgreSQL run ID）
权威控制 run：`5557e950-5bd9-551d-ae1d-948f0ca29d0b`
跨轮决策 SHA-256：`b1cbf8cbd2a0a688ae1ec18fe35cafcce709b84e992bf6e945617bb71e972fc5`

## 一句话结论

这次 run 的搜索广度和数据完整性是合格的：7,200 次生成得到 6,182 条唯一序列，12 个打分器全部覆盖；但“总体分数理想”只能说一半——硬安全门把候选压到 1,139 条（18.4%），最终 mature core 只有 39 条（0.63%），且两个 MIC 模型和 MACREL 活性模型并不一致，因此当前结果适合做结构确认和实验假设组合，不足以宣布单一冠军。

## 1. 分母与当前阶段

- 原始生成 occurrence：7,200（4 轮 × 1,800）。
- 有效唯一序列：6,182，整体去重/无效损耗 1,018（14.1%）。
- 每条完整评价：12/12；总 Evaluation 74,184。
- 两个硬安全标签都通过：1,139（18.4%）。
- 决策状态：rejected 5,043；promising_uncertain 1,100；mature_core 39。
- 进入后续双靶点结构预算：48（39 条核心 + 9 条安全探索候选）。
- 控制 run 和四个 child run 在数据库仍标记 `running`；结构/最终组合/replay 未完成，所以本文是 provisional 序列阶段分析。

![运行漏斗与每轮去重产率](01_run_overview.png)

## 2. 分数是否理想

| 指标 | 方向 | 全体均值/中位数 | 核心均值/中位数 | 全体 P10–P90 | 核心相对变化 |
|---|---|---:|---:|---:|---|
| AMP-READ log10 MIC | 越低越有利 | 1.501/1.311 | 1.541/1.501 | 0.765–2.580 | 接近（标准化位移 0.06） |
| LLAMP log10 MIC | 越低越有利 | 1.355/1.371 | 1.231/1.196 | 0.750–1.933 | 改善（标准化位移 -0.30） |
| MACREL AMP probability | 越高越有利 | 0.538/0.564 | 0.389/0.386 | 0.198–0.822 | 变差（标准化位移 -0.80） |
| ToxinPred3 hybrid risk | 越低越有利 | 0.335/0.255 | 0.051/0.000 | 0.000–0.780 | 改善（标准化位移 -1.28） |
| MACREL hemolysis risk | 越低越有利 | 0.678/0.802 | 0.250/0.208 | 0.139–0.980 | 改善（标准化位移 -1.72） |
| Net charge (pH 7.4) | 非单调描述 | 3.810/3.987 | 2.107/1.989 | -0.012–7.984 | 分布位移（标准化位移 -0.59） |
| Hydrophobic ratio | 非单调描述 | 0.423/0.435 | 0.366/0.364 | 0.211–0.615 | 分布位移（标准化位移 -0.39） |
| Hydrophobic moment | 越高越有利 | 0.338/0.302 | 0.303/0.325 | 0.108–0.624 | 变差（标准化位移 -0.20） |
| Maximum hydrophobic run | 越低越有利 | 3.496/3.000 | 3.231/3.000 | 2.000–6.000 | 接近（标准化位移 -0.13） |
| Guruprasad instability | 越低越有利 | 38.393/24.938 | 29.751/23.188 | -11.662–104.478 | 接近（标准化位移 -0.17） |

判断：mature core 的确定性改善主要来自 ToxinPred3 风险、MACREL 溶血风险和 LLAMP MIC；AMP-READ MIC 与 MACREL AMP probability 没有同步改善。这不是 bug，而是多模型目标确实不一致。净电荷和疏水比例不是单调目标；核心整体更克制，说明筛选没有简单追逐“越正、越疏水越好”。Guruprasad instability 有大量短肽域外标记，只能观察，不能据其数值淘汰候选。

![所有候选与成熟核心的分数分布](02_metric_distributions.png)

标签硬门的具体效果：

- ToxinPred3 Non-Toxin：3,704/6,182（59.9%）。
- MACREL low hemolysis：1,657/6,182（26.8%）。
- mature core：两类标签均 100% 通过；这证明规则执行有效，但不等于实验安全。

## 3. 目标冲突关系

相关热图已把每个有方向的指标转换为“数值越高越有利”的秩，因此负相关可直接读作冲突。

![目标效用相关热图](03_metric_correlations.png)

![四个具体目标关系](04_objective_conflicts.png)

最重要的量化结果：

- 两个 MIC 模型：效用 Spearman ρ=0.617；两者各自前 25% 的 Jaccard 仅 0.414，直接对立率 1.8%。二者不能互相替代。
- MACREL 活性 vs MACREL 溶血风险：效用 ρ=-0.876，直接对立率 38.6%。活性提高伴随溶血风险上升的趋势是当前最实际的 trade-off 之一。
- MACREL 活性 vs ToxinPred3 风险：效用 ρ=-0.255，直接对立率 20.2%。这是另一条活性—安全张力。
- 净电荷/疏水比例对安全风险呈明显非线性，单看相关系数不足；散点图显示极端区风险更集中，new run 不应继续无边界提高电荷或疏水性。

筛选失败原因（同一候选可有多项）：MACREL 溶血标签失败 4,525；ToxinPred3 标签失败 2,478；rank instability 5,638；超出结构预算 1,091。

## 4. 四轮与生成器表现

![生成器逐轮产率](05_generator_yield.png)

| generator | 唯一序列 | 硬安全通过 | 硬安全率 | mature core | 结构入选 |
|---|---:|---:|---:|---:|---:|
| amp_designer | 1,992 | 54 | 2.7% | 5 | 5 |
| ampgan_v2 | 1,843 | 180 | 9.8% | 8 | 9 |
| hydramp | 2,347 | 905 | 38.6% | 26 | 34 |


实用解释：比较 generator 时应同时看唯一产率、硬安全产率和核心/结构贡献，不能只看 raw 行数。四轮没有出现唯一产率崩塌，但总去重损耗 14.1%，说明继续增加完全相同策略的轮次会出现边际收益下降；new run 更值得扩展低覆盖序列家族和安全邻域，而不是简单重复 seed。

## 5. mature core 的具体情况

下图把 39 条核心在 7 个主要目标上换成相对于全体 6,182 条的百分位。它不是加权总分；每一行都保留自己的优势与短板。

![成熟核心多目标权衡图](06_mature_core_tradeoffs.png)

具体应优先人工复核的尾部：

- 核心中 `maximum_hydrophobic_run` 最大者：`SAGEALEKLKHVHPKIWLLLLWAW`，连续疏水段 9；虽然标签门通过，仍需结构/聚集与膜损伤风险复核。
- 核心中 MACREL 溶血概率最大者：`PIELLNLKIRRWWQKFMM`，概率 0.485；接近标签边界的候选不应仅凭 low 标签视为安全。
- 核心中 AMP-READ MIC 最差者：`MTRKNNDLNKNN`，log10 MIC 3.491（约 3099.1 µM）。它能进入核心是因为其他轴保持非支配优势，而不是 AMP-READ 预测优秀。
- 核心中 LLAMP MIC 最差者：`LVDRIGNKVGAA`，log10 MIC 2.509（约 323.0 µM）。

## 6. 证据不足与不能证明的内容

1. 没有实验 MIC、杀菌曲线、细胞毒性或人 RBC 溶血；所有活性/安全结论都是模型预测。
2. 当前 MIC 模型不是病原菌株、培养基、暴露时间条件化的实验端点，log10 MIC 只能同模型内排序。
3. 两个硬安全标签并非独立湿实验；MACREL 与已用数据族的独立性/校准限制仍然存在。
4. Guruprasad instability 对短肽大量 OOD；没有 serum/protease、溶解度、聚集或货架期证据。
5. 双靶点 native/wrong-pocket 结构、跨 seed 稳定性、Rosetta 相对能量和最终 replay 尚未完成；不能声称 GyrA/PBP2a 结合、亲和力或选择性。
6. 未提供 sequence-family key/聚类证据，无法严谨判断 6,182 条序列覆盖了多少独立家族，也无法证明搜索饱和。
7. 只有一个多轮 schedule；没有同合同独立重复、对照策略或 paired ablation，不能把分布位移归因于某个 Agent 改动。

## 附录 A：逐打分器完整统计

数值顺序固定为 `min / P10 / P25 / mean / median / P75 / P90 / max / SD`。MIC 为 log10(µM)：例如 1、2、3 分别约等于 10、100、1,000 µM；越低越有利。极值仅描述当前计算证据，不是新阈值。

| 打分器 | n / 缺失 / 失败 / OOD | min / P10 / P25 / mean / median / P75 / P90 / max / SD | 最好序列 | 最差序列 |
|---|---:|---|---|---|
| AMP-READ log10 MIC | 6182 / 0 / 0 / 0 | 0.113 / 0.765 / 0.989 / 1.501 / 1.311 / 1.876 / 2.580 / 4.007 / 0.706 | `GWKKLRRFAAKFAGRAAHKLTAKKA` | `IFYVVCYGMMALMMAWWDDWWW` |
| LLAMP log10 MIC | 6182 / 0 / 0 / 0 | 0.108 / 0.750 / 1.016 / 1.355 / 1.371 / 1.712 / 1.933 / 2.831 / 0.447 | `FKRWWKRFKKFLEKLKRVKIFRKKR` | `FAAADLNKEDLKK` |
| MACREL AMP probability | 6182 / 0 / 0 / 0 | 0.000 / 0.198 / 0.376 / 0.538 / 0.564 / 0.723 / 0.822 / 1.000 / 0.228 | `LKKKLIKIVAKILK` | `GQEFDYRFVFM` |
| ToxinPred3 hybrid risk | 6182 / 0 / 0 / 0 | 0.000 / 0.000 / 0.040 / 0.335 / 0.255 / 0.605 / 0.780 / 1.000 / 0.300 | `INILKTITGSVLK` | `KKLLKWCKKR` |
| MACREL hemolysis risk | 6182 / 0 / 0 / 0 | 0.000 / 0.139 / 0.455 / 0.678 / 0.802 / 0.941 / 0.980 / 1.000 / 0.312 | `AMGDEADDLEESLESSQAA` | `FLKSLKKLAKHAL` |
| Net charge (pH 7.4) | 6182 / 0 / 0 / 0 | -11.120 / -0.012 / 1.988 / 3.810 / 3.987 / 5.986 / 7.984 / 20.833 / 3.175 | `不定义单调最好` | `不定义单调最差` |
| Hydrophobic ratio | 6182 / 0 / 0 / 0 | 0.000 / 0.211 / 0.318 / 0.423 / 0.435 / 0.529 / 0.615 / 0.929 / 0.156 | `不定义单调最好` | `不定义单调最差` |
| Hydrophobic moment | 6182 / 0 / 0 / 0 | 0.003 / 0.108 / 0.182 / 0.338 / 0.302 / 0.463 / 0.624 / 1.102 / 0.199 | `FKKLFRRWIR` | `FGMMDGMMCMSWWWWAAERRRRRRR` |
| Maximum hydrophobic run | 6182 / 0 / 0 / 0 | 0.000 / 2.000 / 2.000 / 3.496 / 3.000 / 4.000 / 6.000 / 25.000 / 2.267 | `KTGRPRRPPPPKPP` | `LLWWWWWWWWWWWWWWWWWWWWWWW` |
| Guruprasad instability | 6182 / 0 / 0 / 4147 | -56.745 / -11.662 / 2.826 / 38.393 / 24.938 / 58.695 / 104.478 / 448.188 / 53.750 | `FINFVYRYRTG` | `PCRRRRRRRRRRRRRFYRRRRRRRR` |

| 标签打分器 | 类别分布 | 缺失 / 失败 / OOD | 有利类别 |
|---|---|---:|---|
| ToxinPred3 label | Non-Toxin: 3,704 (59.9%); Toxin: 2,478 (40.1%) | 0 / 0 / 0 | Non-Toxin |
| MACREL hemolysis label | high: 4,525 (73.2%); low: 1,657 (26.8%) | 0 / 0 / 0 | low |

## 7. new run 建议（按实用优先级）

1. **先闭合当前 48 条结构证据，不重跑本轮。** 完成双靶点 × native/wrong-pocket × 3 seed × 16 Rosetta，报告每候选跨 seed 一致性与 native-control 差；否则再生成更多序列不会解决最关键的不确定性。
2. **下一序列 run 改为冲突定向探索。** 分层覆盖：低/中净电荷、低/中疏水比例、不同长度和 scaffold；对“MACREL 活性高但溶血/毒性风险高”“LLAMP 好但 AMP-READ 差”分别建立 challenger cells，保留 parent/control，不做单向增正电。
3. **补 family 与 novelty 证据。** 每条 Candidate 持久化 `sequence_family_key`，用预注册的 identity/coverage 规则计算 family yield、历史新颖率和每轮新增 family；连续两轮 family/Pareto extension 停滞才换策略。
4. **模型冲突用独立证据解决，不用加权平均掩盖。** 为双 MIC 模型增加菌株/条件化外部校准或独立模型；报告 rank agreement、top-k overlap 和校准误差。没有校准前保持两个 Pareto 轴。
5. **新增真实开发性端点后再设硬门。** 优先小规模 reference-pilot 锁定人 RBC 溶血、原代皮肤细胞毒性、serum/protease stability、solubility/aggregation；当前 39 条只作盲测，不参与阈值拟合。
6. **实验预算有限时采用分层组合，而非冠军。** 从 39 条核心中按“活性偏强 / 安全偏强 / 理化平衡 / 模型冲突代表”各保留若干条，并加入已知对照；最终数量由实验预算决定，不从当前预测强行产生唯一 winner。

## 8. 可复现产物

- `candidate_metrics.csv`：6,182 条候选、12 指标、决策状态与来源。
- `metric_summary.csv` / `label_summary.csv`：全体、硬安全池、mature core、结构池的完整统计。
- `conflict_summary.csv`：目标对的秩相关、前四分位重叠、直接对立率。
- `generator_round_summary.csv`：round × generator 产率。
- `mature_core_candidates.csv`：39 条核心明细。
- `data_quality.csv`：逐打分器完整性、失败与 OOD。
- `analysis_manifest.json`：输入 run、决策 SHA 和全部输出 SHA-256。

科学边界：本报告是同协议的计算探索诊断，不是实验活性、安全、靶点结合、亲和力或临床结论。
