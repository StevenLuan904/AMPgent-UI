# AMPgent 企业核心管线审计：从穷举 workflow 到 Autoresearch

## 结论

v38 已经具备可追溯生成、score-all、历史失败分母、知识卡 refinement、多靶点隔离和 exact-once 等重要基础，
但还不是“极致探索”的企业核心管线。当前主要瓶颈不是 Pareto 算法本身，而是高维非约束 Pareto 把弱活性但
单轴极端的候选送入固定穷举结构阶段；再叠加固定 2 靶点 × 2 pocket lane × 3 seed × 16 decoy，成本被放大。

recovery-013 的 35 条结构准入肽会产生 420 个 Boltz pose 和 6,720 个 Rosetta decoy。旧调度实测约
5.3 个完整结构任务/小时；若不改变策略，单次结构确认可占用约 79 小时。Pareto 排序只需很短时间，真正拖慢
系统的是它的准入语义和之后的固定预算。

## P0：必须先修的科学身份问题

`se_pbp2a_allosteric` 被注册为 *Staphylococcus epidermidis* `WP_308061015.1`，但坐标 3ZFZ 的官方
实验来源是 MRSA *Staphylococcus aureus* subsp. *aureus* Mu50，聚合物 accession 为 A0A0H3JPA5。
当前运行时 633 aa target sequence 又恰好是 3ZFZ 链去掉 N 端 12 aa 后的完整序列。这不是普通“相似靶点”，
而是物种/accession 元数据与坐标链身份不一致。即使 docking 数值看起来合理，也不能被解释为表皮葡萄球菌
PBP2a 证据。[RCSB 3ZFZ](https://www.rcsb.org/structure/3ZFZ)

企业管线必须在提交前自动验证：注册物种/菌株、蛋白 accession、坐标链 accession、序列覆盖率、序列 identity、
pocket residue 映射。跨物种同源结构只能进入显式 `homology_mode`，并单独报告不确定性；不能伪装成 direct
experimental structure。E. coli GyrA 的 8QQI 身份与来源描述一致，可作为正确 witness 的模板。
[RCSB 8QQI](https://www.rcsb.org/structure/8QQI)

## 现有打分器是否足够

不够。v38 正式合同目前只使用双通用 MIC、MACREL AMP/溶血、ToxinPred3 和少量理化描述符：

| 证据域 | 当前状态 | 企业管线要求 |
|---|---|---|
| 病原体/菌株 MIC | LLAMP + AMP-READ，但主要是通用预测 | 至少双模型，按病原体/菌株/培养条件标定 |
| 溶血 | MACREL 单模型 | 独立模型共识 + OOD；已有 HemoPI2 插件应纳入验证 |
| 毒性 | ToxinPred3 单模型 | 至少第二个独立模型；单模型只可作软证据 |
| AMP-likeness | AMPlify 已按用户决定停用，当前缺失 | 选择并独立验证非 AMPlify 替代模型；不替代 MIC |
| 哺乳动物细胞毒性 | 缺失 | 至少皮肤场景 HaCaT/成纤维细胞风险模型或实验 |
| 新颖性/OOD/IP | MMseqs + ESM2 插件存在但未进正式 v38 | 强制报告近邻、训练集泄漏和 OOD |
| 稳定性 | Peptiverse 仅 shadow | 血清/蛋白酶/盐条件分开，随后用实验校准 |
| 聚集/溶解性 | AggrescanAI 仅 shadow，溶解性缺失 | 两者独立评估，不能用 GRAVY 冒充 |
| 合成可行性 | 仅合法氨基酸/长度 | 难合成 motif、纯化、修饰和成本 |
| 耐药倾向 | 缺失 | serial-passage/机制 proxy 和实验 |
| 共生菌选择性 | 缺失 | 与病原菌并行的皮肤共生菌 counter-screen |

ToxinPred3 不能单独承担安全门：在困难的细菌小蛋白/毒素任务上，已有独立评估报告其高召回但较低精度，
说明单模型标签需要交叉验证和域外检测。[VISH-Pred benchmark](https://academic.oup.com/bib/article/25/4/bbae270/7688816)
物种信息也不是可选元数据；species-aware LLAMP 的消融显示去掉基因组/物种分支会明显降低 MIC 预测性能。
[species-aware LLAMP](https://academic.oup.com/bib/article/26/4/bbaf343/8205772)

## “全靶点”应该怎样定义

不能把“全靶点”等价成对所有蛋白做 docking。AMP 常见首要机制是膜作用；对胞内蛋白 docking，若没有摄取、
胞内暴露和机制证据，会制造大量伪精确结构分数。企业级覆盖采用四层体系：

1. **病原表型面板**：优先病原体、耐药菌株、临床条件下 MIC/MBC；这是核心覆盖。
2. **选择性反筛**：皮肤共生菌、红细胞、角质形成细胞/成纤维细胞。
3. **机制面板**：膜选择性、biofilm、persister、胞内进入、盐/血清/蛋白酶稳定性。
4. **蛋白靶点结构**：只有满足可达性和机制 plausibility 的靶点才进入，且必须通过身份和 pocket witness。

高通量突变扫描表明，活性与哺乳动物膜选择性需要联合学习，而不是只追求单一活性轴。
[Nature Biomedical Engineering 2024](https://www.nature.com/articles/s41551-024-01243-1)

## Pareto 的正确位置

Pareto 不是罪魁祸首，但无约束高维 Pareto 会出现“维度灾难”：目标越多，互相不支配的点越多。当前做法允许
一个几乎无 AMP 活性但 hemolysis 很低的候选靠单轴极端保留。企业方案为：

1. 有效输入、关键证据完备、安全红旗仍是硬门；
2. 用公开基准、阳性/阴性对照和历史 calibration 冻结**参考区间**，禁止用当前批次分位数自我设门；
3. 先要求最低质量/可信度，再执行 constrained epsilon/reference-point Pareto；
4. dominance 同时考虑预测不确定性和 OOD，模型冲突进入主动学习池；
5. 只在质量准入后做序列多样性组合，并冻结 12–24 条结构候选上限；
6. 一个都不满足时触发有证据 refinement 或区分性实验，不降低安全门、不强制补位。

这不会重回“一个外部阈值把候选全清空”的问题：参考区间来自对照/校准并带灰区，灰区进入主动学习，只有明确
无效或不安全才淘汰。

## Autoresearch 运行循环

真正的 autoresearch 不是固定流水线跑到底，而是每一轮都缩小不确定性：

```text
观察 durable 证据
  -> 诊断当前最大知识缺口/成本瓶颈
  -> 选择最有区分力的下一实验
  -> 执行并验证
  -> 内容寻址持久化
  -> 更新模型校准、策略和下一轮 proposal
```

具体控制：

- 低成本序列模型对全部合法唯一序列 score-all；
- 模型一致的强候选进入核心，模型冲突且安全的候选进入主动学习，不直接耗尽结构预算；
- refinement 以失败模式分桶（活性不足、溶血冲突、稳定性差、OOD），每桶使用不同知识卡和 mutation operator；
- 结构先跑每候选/靶点/lane 1 seed，只有排序不稳定、native-vs-wrong 对照可区分且有决策价值时扩到 2–3 seed；
- Rosetta decoy 同样采用预注册置信区间/排序稳定性停止，而非所有 pose 固定 16 个；
- 每个湿实验结果回写 model registry 的 calibration/OOD，而不是只形成一次性报告；
- 每 5 分钟检查 durable 增量，每 15 分钟无进展必须改变策略，120 分钟只用于面向用户 review。

已有成功工作证明更像这种漏斗：HydrAMP 实验测试的 24 个类似物中，18 个 HC50 ≥512 µg/mL，并在多菌株
活性和溶血间联合筛选；物种感知筛选则从约 550 万序列逐层收缩到少数实验候选。企业目标不是“最大前沿”，而是
单位 GPU 小时、单位合成成本带来的校准后命中率。
[HydrAMP](https://www.nature.com/articles/s41467-023-36994-z)

## 实施优先级

### P0：现在

- 对 3ZFZ 分支 fail closed，修正物种/accession 或显式降级为同源模型；
- 把 target identity witness 加入 formal preflight；
- 停止把当前高维 Pareto 直接等同于结构准入；
- 保持 AMPlify 停用；选择非 AMPlify 替代项，并对 HemoPI2、MMseqs/ESM2 做独立 benchmark 后再升格；
- 将当前 structure 调度改为 target interleave + Boltz/Rosetta 流水线（未来版本代码已完成）。

### P1：下一可运行版本

- 病原体/菌株条件化 MIC 与共生菌反筛；
- 第二毒性模型、细胞毒性、稳定性、聚集/溶解性证据域；
- constrained epsilon Pareto、模型不确定性和 OOD；
- 12–24 条结构组合与预注册 sequential stopping；
- model/target/assay registry，记录版本、训练域、license、calibration、单位和条件。

### P2：形成企业飞轮

- 小批量 MIC/MBC、HC50、HaCaT/成纤维细胞、盐/血清/蛋白酶和共生菌实验；
- 以实验信息增益选择下一批，而非固定 900→固定结构预算；
- 按病原体、作用机制和制剂场景维护独立 portfolio；
- 用历史 run 学执行可靠性和模型校准，但不复制旧候选作为新 run 结果。

机器可读审计合同位于 `config/enterprise/ampgent_core_pipeline_v39_audit.yaml`；它明确标记
`formal_science_run_authorized=false`，因此不会被误当成已冻结科学版本。
