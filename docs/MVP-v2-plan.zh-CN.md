# 结论与 MVP-v2

## 结论

MVP 已经证明一件重要的事：这不是“聊天生成几条肽”的玩具流程，而是一条能中断恢复、能追溯模型与权重、能保存候选生命周期和原始证据的实验流水线。

首轮 AceA 试跑中，PepMLM 生成的最优候选是 `KSSVGVVVGNPA`，条件 PPL 为 `5.9315`。但 Boltz-2 给出的蛋白—肽 pair-ipTM 只有 `0.1402`，界面证据很弱。因此正确结论是：

> 系统跑通了，候选没有通过。它不能被称为 AceA 抑制剂，也不能声称有 Kd。

这恰好说明证据闸门有效。PepPAP 已冻结；现阶段不接入任何不可复现或未经本项目校准的亲和力模型。

## MVP-v1 到底完成了什么

下表只写实际执行结果，不把“代码里预留了字段”或“文档里讨论过”算成已实现。

| 工具 / metric | MVP-v1 状态 | 实际用途与结果 |
| --- | --- | --- |
| PepMLM-650M | **已接入并跑通** | 目标序列条件生成；记录 conditional NLL 和 pseudo-PPL。AceA 共生成 4 条 12 aa 肽，最优候选 PPL `5.9315`。PPL 只表示符合模型学到的 target–peptide 条件分布，不是亲和力。 |
| Boltz-2 structure model | **已接入并跑通** | 把靶蛋白和肽都表示为 protein chain，做蛋白—肽复合物共结构预测；记录 confidence、ipTM、protein–peptide pair-ipTM、complex ipLDDT 和 CIF。首轮仅 1 个 diffusion sample，pair-ipTM `0.1402`，未通过。 |
| Boltz-2 affinity head | **明确禁用** | 官方把 affinity prediction 定义为 protein–small-molecule；不能拿来给 peptide chain 报 Kd。 |
| PepPAP | **只复现官方样例，随后冻结** | 官方 1G6R 示例数值复现成功，证明软件可回放；因实现异常和跨靶点有效性无校准，不进入生成、排序或新实验。 |
| PPI-Affinity | **未接入** | 公共服务验证时不可用，也未找到可下载、版本化的权重与推理包。 |
| Rosetta FlexPepDock / Rosetta dG | **没有运行** | 当前只有 P1 设计、metric 枚举和数据契约；没有 Rosetta 安装证据、tool call、refined pose 或 `dG_separated` 结果。不能说 MVP-v1 做过 Rosetta dG。 |
| 口袋条件、界面坐标审计、snapshot critic | **未运行** | 首轮是无 pocket constraint、无 MSA server 的盲测 smoke test；这些属于 v2。 |

MVP-v1 的核心产物不是一个好候选，而是一个可恢复、可重放、不会把低可信结构包装成“命中”的最小系统。完整运行事实与哈希见 [validation-report.md](validation-report.md)。

## Boltz-2 的边界：结构可以，肽 Kd 不可以

这里需要纠正一个容易混淆的说法：Boltz-2 **不只会做蛋白—小分子 complex**。它的结构模型支持多链生物分子复合物；本项目可把短肽作为第二条 protein chain，做 peptide–protein **复合物共结构预测**。Boltz-2 也已被用于大规模 protein–protein complex prediction。官方发布说明同时明确把新增 affinity prediction 写成 **protein–small-molecule**。

因此 v2 节点必须命名为“Boltz-2 peptide–protein complex structure sampling”，不要笼统写成 folding，也不要把两条能力混在一起：

- 允许：复合物坐标、confidence、ipTM、pair-ipTM、ipLDDT、接触条件和结构模板；
- 禁止：用 affinity head 给 peptide chain 预测 Kd；
- 禁止：把 pair-ipTM 或任一结构置信度改名为亲和力；
- 必须：先用已知 protein–peptide 复合物做本项目域内校验，再决定晋级阈值。

依据：[Boltz-2 官方 release](https://github.com/jwohlwend/boltz/releases)把 affinity 限定为 protein–small-molecule，并列出 multimeric template/contact conditioning；[Boltz-2 human interactome 工作](https://pmc.ncbi.nlm.nih.gov/articles/PMC12236519.1/)则直接展示了 protein–protein complex prediction 的用途。

## MVP-v2 只增加五件事

1. **口袋证据卡**：Agent 对每个靶点检索结构、配体、催化残基和文献，保存来源、日期、结构版本与残基编号；没有可靠口袋时明确标记为盲预测。
2. **口袋条件生成**：把已确认的口袋/催化位点上下文加入生成与 Boltz-2 结构约束，不再只用整条蛋白序列盲生成。
3. **小批量、多样性迭代**：按固定随机种子生成多个长度和多批候选，去重并保留亲子谱系；先用 PepMLM PPL 粗排，只让前列且有差异的候选进入 GPU 结构计算。
4. **结构重复与晋级阈值**：对入围候选做多个独立结构采样，以 pair-ipTM、界面接触和重复一致性决定是否晋级。单次漂亮结构不算命中。
5. **高精度相对复排**：只对最前面的少量候选加入 Rosetta FlexPepDock，报告相对界面分数，不把 Rosetta 能量换算成 Kd。亲和力预测继续作为后续验证课题，不阻塞 v2。

MVP-v2 的成功标准不是“预测出一个神奇数字”，而是相对盲跑显著提高口袋接触、结构重复一致性，并能一键重放整个选择过程。

## Pocket 调研后的实际分流

口袋证据必须同时回答两个问题：**证据真不真**，以及**这个位置适不适合短肽条件生成**。两者不能混成一个分数。

- **AceA：主口袋。** 采用催化 Mg²⁺/异柠檬酸位点。E. coli 精确靶点有 [1IGW](https://www.rcsb.org/structure/1IGW) 结构和 [UniProt P0A9G6](https://www.uniprot.org/uniprotkb/P0A9G6/entry) 功能/突变证据；但结构含 A219C，催化环处于开放或无序状态，因此必须做构象集合。
- **GyrA：主口袋。** 优先采用 [8QQI](https://www.rcsb.org/structure/8QQI) 的 LEI-800 别构口袋。它位于 DNA 结合表面，并部分依赖 GyrA–DNA 复合物；不能把孤立单体预测当成完整证据。
- **GyrA 氟喹诺酮位点：暂不启用。** [9GGQ](https://www.rcsb.org/structure/9GGQ) 证据很强，但口袋由 GyrA 二聚体、断裂 DNA、Mg²⁺共同形成。当前输入还不能专业地表示这个复合环境。
- **PBP2a：别构位点优先，催化位点次选。** S. epidermidis 序列与 [3ZFZ](https://www.rcsb.org/structure/3ZFZ) 的 S. aureus 可溶构建体对应区间完全一致，可明确映射。别构区更暴露，适合先尝试短肽；催化位点狭窄且 β-内酰胺机制是共价酰化，不能按普通亲和力理解。
- **VEGFA、FGF2、ANGPT1：不是抗菌肽靶点。** 它们是促愈合载荷，保存受体界面证据用于以后检查功能是否被融合/修饰破坏，但不进入抗菌肽 pocket-conditioned generation。

评级采用双轴：A–U 表示证据等级；`primary / secondary / excluded` 表示条件生成优先级。全部标准口袋、残基映射、来源版本、检索日期和限制已进入版本化 catalog，并由 PostgreSQL 保存多条独立证据。

## MVP-v2 是 Auto Research 闭环

每轮不是把一个总分越刷越高，而是执行：

1. 从当前 Pareto 前沿选择父候选，同时保留一定探索配额。
2. PepMLM 生成定向突变与新候选，固定并记录随机种子、父子关系和生成轮次。
3. 先做 PPL、重复序列、长度和多样性筛选；通过者进入多种子 Boltz-2 peptide–protein 复合物共结构采样，肽作为第二条 protein chain，affinity head 保持硬禁用。
4. 用“结构集合一致性 + 口袋界面几何”晋级，不接受单次偶然的漂亮结构。
5. 只让少量可信姿势进入 FlexPepDock 局部精修和相对能量复排。
6. 新候选只有在硬约束全部通过，并改善 Pareto 前沿或不确定性时才成为下一轮父代。

停止条件是预算耗尽、连续多轮无 Pareto 改善、候选多样性坍缩，或结构不确定性始终无法下降。所有失败候选仍保留，避免下一轮重复探索。

## 独立视觉辅助模块（不属于主 Auto Research）

**有意义，但现在没有资格成为 metric 或自动晋级闸门。** 它最合适的角色是 shadow-mode 的 Codex structure critic：发现固定 scalar 没覆盖的失败模式、解释异常、提出下一轮应补算什么。它不能预测 Kd，不能替代坐标计算，也不能凭“看起来不错”改变候选分数。

原因很实际：三维渲染能让 Codex 同时看到全局落位、表面互补、长肽尾部、域碰撞和置信度分布，适合发现“各项数字勉强过线但整体明显不合理”的组合异常；但截图丢失精确距离、遮挡背面原子，并强烈依赖相机、颜色和表面表示。现有多模态模型在化学视觉任务上有能力，但公开基准也显示视觉—语言融合仍会出现稳定而自信的错误，不能未经本项目校准就充当科学评分器。

视觉模块与主 Auto Research 分开开发、分开部署、分开失败。主流程不调用视觉模块，也不等待其结果；视觉模块只能订阅已经完成的结构 artifact，异步产生辅助审阅。

主 Auto Research 的结构晋级只采用坐标和能量证据：

- **坐标审计是主证据和晋级门。** 直接从 CIF/PDB 计算：

- 多种子姿势聚类，以及口袋接触在重复采样中的出现频率；
- 目标口袋覆盖率、非目标表面接触率和锚点残基埋藏情况；
- 原子碰撞、氢键、盐桥、疏水接触、界面埋藏面积和形状互补；
- 肽是否异常自折叠、穿模、贴在低置信无序区，或只靠一个偶然接触挂住；

- **Codex snapshot critic 是独立辅助证据。** 输入不是一张随意截图，而是确定性生成的 evidence bundle：全局复合物、口袋近景、正交视角、分子表面、接触/残基标注、置信度着色和 contact map，并同时提供 pocket evidence card 与坐标审计表。Codex 输出固定 schema 的 `flags / evidence / uncertainty / suggested_next_action`，例如 off-pocket、gross clash、仅单点悬挂、低置信区吸附、跨域穿插或视角不足。

Snapshot critic 先以 shadow mode 运行，不参与 Pareto 分数。是否晋升为规则，必须先做一个小型验证集：已知 protein–peptide complex、人工制造的 off-pocket/clash/遮挡负例，以及坐标规则难判的边界例。比较“坐标审计”“snapshot only”“两者合并”的错误发现率、重复调用一致性和假阳性。只有合并通道对坐标审计有稳定增益时才保留；否则 snapshot 只作为报告，不进入循环决策。

这项判断参考了公开多模态化学基准对视觉推理脆弱性的结果：[Communications Chemistry 2025](https://www.nature.com/articles/s42004-025-01782-x)。OpenAI 接口可以在一次请求中提供多张原始细节图片，因此工程上可实现固定多视角 evidence bundle：[OpenAI Images and Vision](https://developers.openai.com/api/docs/guides/images-vision)。

## 可溯源和未来流程 graph

产品版 graph 现在不做 UI，但底层数据契约现在就要满足。每次生成、筛选、结构 sample、坐标审计、snapshot render、Codex review、FlexPepDock decoy 和晋级决策都是独立 attempt 节点；节点必须保存状态、时间、输入/输出哈希、代码与环境、权重、随机种子、参数和原始 artifact。节点之间使用显式多父依赖边，例如 `generated_from / evaluates / renders / reviews / refines / selected_by`。

PostgreSQL 是节点、边和生命周期的真相源；MinIO 保存按内容寻址的结构、图片、日志和原始响应；Temporal 保存执行、重试、心跳与恢复。未来 graph 页面只是这些记录的投影，不能另建一套不可回放的前端状态。Snapshot review 还必须保存模型精确版本、prompt template 哈希、render recipe/相机参数、全部图片哈希、结构化结论与原始响应；否则它不算实验节点。

## 第 4 步和第 5 步的关系

二者都在结构通道，但回答不同问题：

- **第 4 步（Boltz 多采样 + 界面几何）**回答“这个结合姿势是否稳定出现、是否真的落在目标口袋、结构证据是否可信”。它是晋级闸门。
- **第 5 步（FlexPepDock）**回答“在已经可信的局部姿势附近，哪条肽的界面经过高分辨率精修后相对更好”。它是精修与复排器。

Rosetta 不能救活一个低 pair-ipTM、采样不一致或根本没进目标口袋的姿势。计算预算应优先保证第 4 步有足够重复，再把第 5 步用在最前面的少量候选；两类分数分别保存，不提前揉成一个伪精确总分。

## MVP-v2 Rosetta 实施结论（2026-07-31）

Rosetta 高精度复排已经从“计划”变成可运行模块。正式实现固定使用 PyRosetta
`2026.29+releasequarterly.80a0635615`、`ref2015`、FlexPepDock prepack/refinement 和
InterfaceAnalyzer。每个候选产生 200 个独立 seed 的 decoy，先按标准 `reweighted_sc` 排序，
主指标取前 10 个 decoy 的 `dG_separated` 中位数；完整分布、最小值、界面能、氢键、埋藏面积、
packstat、肽骨架 RMSD 和全部 PDB/JSON 都进入 PostgreSQL/MinIO。REU 不换算 kcal/mol 或 Kd。

三组公开短肽复合物核对均已完成：

| 复合物 | 正式 run | 主 dG（REU） | RMSD 中位数 | ≤2 Å |
| --- | --- | ---: | ---: | ---: |
| 2DS8 | `aa60bfad-97f4-44b0-a0b1-969d985fe5fe` | -32.9331 | 0.5629 Å | 94.5% |
| 1NVR | `6e5405f4-b1c1-4087-88be-dfdb5e76e346` | -26.8628 | 0.5477 Å | 100% |
| Rosetta 官方 1ER8 benchmark input | `5a121752-b844-4278-bfc5-a3149f4d1a1b` | -50.3950 | 1.0156 Å | 98.5% |

结论要说准：这三例证明了实现可复现、证据链完整，并且从已知口袋附近出发可以保留/恢复近天然
肽构象；它们没有证明 Rosetta dG 是实验结合自由能，也没有证明系统能盲找口袋。MVP-v2 因此
接纳 Rosetta 作为“高成本、后置、同靶点相对复排器”，不接纳为 Kd 预测器。

视觉 snapshot 模块保持完全解耦：主 Auto Research workflow 不调用、不等待，也不读取其结论
作为 metric 或晋级条件。未来可以把它做成异步 shadow critic，但当前 Rosetta 的正式验证没有
依赖任何人工或 Codex 看图判断。
