# AMPgent AceA v35 多靶点资格框架

状态：`qualification_framework_frozen_not_authorized`

精确合同：`config/benchmarks/amp_multitarget_qualification_v35.yaml`。

本文解决的不是“马上挑几个靶点跑”，而是先规定怎样证明迁移、怎样保留失败、怎样避免只挑成功靶点。
当前没有选择具体新靶点、没有生成序列、没有提交 run。

## 1. 项目能力上限

计算流程最多能回答：在相同 harness、预算、结构协议和证据门禁下，某些预先资格化的细菌靶点是否
呈现可复现的候选产生、姿势稳定、native-vs-wrong-pocket 区分和相对 Rosetta 方向。即使多个靶点都
得到有利 REU，也不能称为结合、亲和力、选择性或广谱靶向；这些仍需实验。

v35 的可接受结论只能是：

- 某类靶点在本协议和预算内可迁移；
- 某类靶点需要专用策略；
- 某类靶点当前证据或结构条件不足；
- 该 harness 在预注册面板上没有显示可迁移性。

不能宣称“AMPgent 对任意靶点通用”。

## 2. 为什么先资格审计

若先生成短肽再挑靶点，会把容易取得漂亮结构分数的靶点留在面板中，把失败靶点删除，得到不可解释的
成功率。因此 v35 分成四个不可跳跃阶段：完整 shortlist 审计、面板预注册、shadow、独立确认。所有
shortlist 项目都必须留下通过或失败记录；失败项进入分母，不能用替补靶点刷新结果。

第一轮至少审计 8 个新靶点候选，最终选择 3–5 个新靶点，并始终保留 AceA reference 与 target-agnostic
AMP lane。具体名单在下一独立阶段确定，选择时禁止读取任何新肽、Boltz、pair-ipTM、Rosetta、AMP/MIC、
安全模型或 PepShot 结果。

## 3. 序列与结构证据

序列优先使用 UniProtKB reviewed 记录；UniProt 明确区分人工审阅的 Swiss-Prot 与自动注释的 TrEMBL，
并给每类 feature 提供 evidence attribution。因此正式审计要冻结 accession/version、原始序列字节、
sequence SHA、feature evidence、processing/isoform 状态，而不是只记蛋白名称。

结构优先级为：带验证报告的目标实验结构；带显式序列映射的近同源实验结构；只作假设的预测结构。
实验结构若以 ligand 定义口袋，必须检查局部密度、几何与 clash 等验证信息。RCSB/wwPDB 的 ligand
validation 会报告与实验密度拟合和局部几何质量，功能 ligand 也不能仅因“出现在 PDB”就自动视为可靠。

AlphaFold DB 提供 pLDDT 与 PAE：pLDDT 是局部坐标置信度，PAE 用于相对结构位置不确定性。其官方说明
也明确指出预测不包含 ligand、cofactor、metal 等非蛋白组分，不能控制多构象状态；低置信度区域不应
解释。因此预测结构只能支持 exploratory pocket，除非另有独立实验 pocket 证据，不能单独进入 primary
panel。

官方依据：

- [UniProtKB reviewed/unreviewed 与证据归属](https://www.uniprot.org/help/uniprotkb/)
- [UniProt sequence feature 与实验 evidence 查询](https://www.uniprot.org/help/sequence_annotation)
- [AlphaFold DB pLDDT、PAE 与使用限制](https://www.alphafold.ebi.ac.uk/faq)
- [RCSB ligand structure quality 与 wwPDB validation](https://www.rcsb.org/docs/general-help/ligand-structure-quality-in-pdb-structures)

## 4. Pocket 证据等级

- A：目标本身有带功能 ligand/substrate/inhibitor 的实验结构，且局部验证合格；可进入 primary。
- B：目标有实验 active-site/mutagenesis 证据并完成精确映射，但无直接功能 ligand；可进入 primary。
- C：近同源转移或高置信预测，明确保留不确定性；只进入 exploratory。
- D：仅 cavity predictor 或不可追溯注释；排除。

primary panel 每个靶点必须达到 A 或 B。分级是来源/映射强度，不是“这个靶点一定适合肽结合”的保证。

## 5. 面板不能只覆盖容易目标

面板要求覆盖三个预先声明的结构/机制层次：AceA 或近机制邻域、不同 fold/口袋几何的非同源可溶酶、
以及具有 multimer/cofactor/metal/membrane-association 上下文的困难靶点。所有靶点先过硬门，再按序列
同源性、fold、口袋开放度/体积、电性、机制和组装上下文做确定性 maximin；“预期会成功”不能作为
多样性特征。

困难靶点不是为了制造失败，而是为了测量 harness 的真实适用边界。若当前管线无法保留其上下文，结果
应记录为能力限制，不能悄悄排除后再宣布泛化。

## 6. 必需对照与结果

每个靶点至少有 native functional pocket、同一靶点的预先冻结 wrong/decoy pocket、target-agnostic
AMP lane。跨靶点共享 parent/seed block，并加入 target-label permutation 阴性对照。若有合适结构，可
附加 apo-vs-functional-state、同源靶点对或 catalytic-residue mask，但不得事后补挑。

报告每个靶点的执行/失败率、候选和姿势产率、多 seed 稳定性、native-vs-wrong-pocket 对比、同协议
Rosetta 分布、Pareto 前沿形状、跨靶点候选复用、成本和完整失败分类。不使用加权总分，也不强迫产生
一个“最通用靶点”。

## 7. 数据库复原

shortlist 中每个靶点的序列、结构、pocket、来源字节、SHA、硬门结果、拒绝理由和面板选择 witness 都
必须进入 PostgreSQL；结构/validation/PAE 等大对象进入内容寻址对象存储。未来候选、对照、ToolCall、
依赖、Evaluation、AgentDecision 和失败事件也必须进入同一证据图。

只有 database+object-store-only replay 能复原完整 shortlist、所有失败、最终面板、native/wrong pocket
定义、候选-target join 和最终结论范围时，阶段才算完成。CSV/Markdown 只是导出。

## 8. 下一步

下一步仅允许编写并审计不少于 8 个新靶点的 qualification ledger。该 ledger 必须先冻结选择规则和来源
字段，再开始逐靶点 web/database evidence audit；不能在本框架中直接写入具体 target 名称。靶点审计、
panel selection、shadow 和 formal confirmation 都需要各自独立授权与不可变合同。
