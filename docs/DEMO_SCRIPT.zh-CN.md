# AMPgent 产品发布会演示脚本

建议总时长 8–10 分钟。演示前先运行 `scripts/release-check.ps1`，然后通过 `scripts/start-release.ps1` 启动生产 preview。浏览器使用 1920×1080，缩放 100%。

## 演示前准备

```powershell
.\scripts\release-check.ps1 -Install
.\scripts\start-release.ps1 -Port 4173 -SkipBuild
```

打开 `http://127.0.0.1:4173/`。确认页面无浏览器扩展遮挡，关闭开发者工具，保留备用离线截图和当前提交 SHA。

## 1. Overview：这次运行发生了什么（约 2 分钟）

1. 指出顶部三个平行空间：Overview、Analysis、Evidence；说明本版本没有 Analysis Agent，所有数字来自确定性查询。
2. 选择真实 run `57afecc7…013d`，主动说明其最终状态是 `cancelled`。
3. 强调取消发生在序列生成、11 项评分和 admission 完成之后；结构与最终 portfolio 不完整，所以只展示已经落库的证据。
4. 展示生成路径：三个生成器各 300 条，共 900 proposal；773 条合法 unique candidate；35 条 structure eligible。
5. 在 Overview 进入组合分析模式，选中 generation、scoring、admission，再选择 MIC 与溶血指标。

推荐讲法：

> 这里不是聊天记录，而是一次科学运行的只读观察面。我们先看流程，再把想追问的节点和指标直接变成可复现分析卡片。

## 2. Analysis：为什么得到这些候选（约 4 分钟）

1. 展示 Overview 自动生成的多张卡：funnel、评分分布、coverage、Pareto conflict。
2. 强调每张卡拥有独立查询；把一张卡筛选为 AMP Designer，另一张保留全部生成器。
3. 打开 AMP Designer funnel：原始 300 条、unique/pool、safety pass、admitted，指出每段损失可追溯。
4. 查看 `llamp_log10_mic_um` 分布：比较三个生成器的 n、median、IQR、missing 和 OOD。
5. 打开 pivot 编辑器，将 generator 从 column 拖到 row；展示系统重新推荐兼容图表。
6. 演示一次不合理操作，例如把连续评分放进 category 或让 funnel 缺少 stage；停留一秒让观众看到拒绝原因。
7. 打开 Pareto conflict：说明两个目标方向、共同覆盖样本和 structure eligible 标记；避免把相关性直接称为因果或“生成器更好”。
8. 拖动卡片位置并缩小一张卡，展示 compact 模式只保留主值与关键图形；再放大恢复图例、细节和 provenance。
9. 隐藏一张卡再从卡片库恢复；刷新页面确认布局和卡片状态持久化。

推荐讲法：

> 卡片不是共享一个全局筛选器。每张卡都是独立、可复现的问题；字段和图表之间有科学语义约束，所以系统会拒绝“看起来漂亮但没有意义”的组合。

## 3. Evidence：这些数字能否被相信（约 1.5 分钟）

1. 打开 Evidence，展示 run ID、snapshot SHA、spec SHA、coverage 8,503/8,503。
2. 展示一个 metric 的 tool version、weights/environment/output identity 和 limitations。
3. 指出标签型指标没有 numeric value 不等于缺失；OOD=0 仅代表该运行落库标志，不代表模型适用范围无限。
4. 再次展示 warnings：source run cancelled，structure/final portfolio 不完整，当前为冻结发布快照。

## 4. 离线兜底与收尾（约 1 分钟）

1. 若时间允许，在预先准备的离线窗口中展示：live analytics 不可用时加载同一 SHA 的冻结快照，界面明确提示来源。
2. 返回 Analysis 总览，展示 run quality、funnel、distribution、Pareto 和 candidate table 同屏。

收尾讲法：

> 当前发布版把真实运行事实、分析方法和交互展示分开：源数据只读，查询受控，结果绑定快照，界面可以灵活组合。后续可以增加更高级的置信区间与冲突统计，但不会牺牲今天已经可复现的证据链。

## 演示中不要宣称

- 不要说该 cancelled run 是“成功完成的最终设计”。
- 不要说 35 条已经是 final portfolio；它们是 structure eligible / admitted to structure budget。
- 不要把预测 MIC、溶血或毒性称为湿实验结果。
- 不要根据单个分布或负相关宣称某生成器必然更好。
- 不要演示尚未接入真实数据的 fixture 卡而不说明其身份。

## 故障切换

- 页面 API error：进入 Analysis，使用冻结快照；说明顶部 source badge。
- 布局异常：点击“重置布局”，再从卡片库恢复默认卡片。
- 图表空白：清除该卡片筛选，不要切换到未经验证的 run。
- 生产 preview 未启动：重新运行 `scripts/start-release.ps1 -Port 4173 -SkipBuild`。

