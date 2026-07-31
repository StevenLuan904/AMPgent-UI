# 视觉结构检查 Tool

## 为什么值得做

蛋白—短肽设计不能只看一个 scalar。pair-ipTM、口袋接触数和 Rosetta dG 各自只描述局部性质，
可能同时漏掉一些整体上明显不合理的结构：肽横穿蛋白、贴在无序低置信区、只有一个残基悬挂、
尾部严重暴露、结合方向违背已知催化构象，或者 pocket 数值过线但整体界面并不可信。

视觉检查的价值不是“用眼睛预测 Kd”，而是让具有结构生物学 prior knowledge 的 Codex 同时查看
整体落位、局部界面、表面互补、置信度和接触图，发现固定规则没有覆盖的组合异常，并指出下一步
应该补算什么。

## 正确定位

- 它是独立、异步、非阻塞的辅助 tool，不属于 MVP-v2 主 Auto Research metric。
- 它不改变候选分数，不决定是否进入 Rosetta，不替代坐标距离、碰撞和能量计算。
- 它不能输出 Kd，也不能因为“看起来很好”把低可信结构升级为命中。
- 主流程失败或视觉服务不可用时，两者互不影响。

## 输入应该是什么

输入不是一张随意截图，而是由固定 recipe 生成的 evidence bundle：

- 复合物全局视图；
- pocket 近景和三个正交方向；
- 分子表面与电荷/疏水区域；
- pocket 残基、催化残基和肽的明确标注；
- 按置信度着色的结构；
- 接触图以及同一候选多个 seed 的并排视图；
- 对应 pocket evidence card 和坐标审计摘要。

相机、颜色、裁剪、结构版本和渲染参数全部固定并保存哈希，保证同一结构可以重新生成相同输入。

## 输出应该是什么

输出核心不是“有/没有问题”的标签，而是一组可定位的空间 `finding`。每个 finding 必须能落到具体结构、seed、chain、残基、原子或官能团，给出笛卡尔坐标、实际距离/角度/二面角、参考范围、超出量、计算方法和自然语言解释。完整契约见 [空间结构问题 Finding 契约](spatial-finding-contract.zh-CN.md)。

精确数值只能来自坐标计算；Codex 负责结合 pocket、催化位点、多 seed 和 snapshot 解释其生物学含义。若视觉上发现疑点但没有对应数值，输出 `needs_measurement` 并要求补算，不能从图片猜测 Å 或角度。

Codex 输出固定 schema，同时保留未经改写的原始回复：

- `flags`：off-pocket、gross clash、single-point attachment、low-confidence adsorption、
  implausible threading、视角不足等；
- `evidence`：问题对应的视角、残基和结构区域；
- `uncertainty`：能否仅凭当前 bundle 判断；
- `suggested_next_action`：补充坐标审计、增加 seed、改变 pocket constraint 或交给人工复核。

原始输入、原始回复、结构化投影、模型精确版本、prompt/render recipe 哈希及图片 artifact 都进入
PostgreSQL/MinIO，并通过 `renders / observes / reviews` 边连接原结构 tool call。

## 大致实施路线

1. 建立确定性结构渲染器，生成固定多视角 bundle。
2. 建立独立的 `VisualStructureCriticWorkflow`，只订阅已经完成的结构 artifact。
3. 先以 shadow mode 跑已知 protein–peptide complex、人工制造的 clash/off-pocket 负例和边界例。
4. 比较坐标审计、视觉检查以及二者合并时的错误发现率、重复调用一致性和假阳性。
5. 只有视觉通道对坐标审计产生稳定、可复现的增益时，才考虑把某些明确 failure flag 转成规则；
   否则长期保持报告型辅助证据。

## 最终判断

这个 tool 有意义，因为它能补充“多个数字分别正常、组合起来却明显异常”的失败模式；但它的价值
是审计和解释，不是亲和力预测。先独立验证，再决定是否保留，绝不能为了产品看起来像 Agent 而把
视觉判断强行耦合进科学主流程。
