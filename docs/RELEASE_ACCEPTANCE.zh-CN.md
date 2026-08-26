# AMPgent 发布验收矩阵

本矩阵用于证明发布目标，而不是记录开发意图。只有“证据”列中的文件、命令或 Playwright 产物实际存在且结果吻合时，才能将状态改为通过。

## 阻断级门槛

| 发布要求 | 验收方法 | 通过证据 | 当前检查点 |
|---|---|---|---|
| 顶层保留概览 / 分析 / 证据库 | Playwright 快照，逐个导航并确认页面标题和可返回路径 | `output/playwright/analysis-2560x1440.png`、`evidence-2560x1440.png` | 通过 |
| 不实现 Analysis Agent | 搜索 UI、路由、接口和文案；只允许 deterministic analytics 表述 | `Select-String` 审计结果 | 数据内核通过；主线待验收 |
| 真实轮次数据闭环 | 浏览器加载真实快照，展示生成、唯一候选、候选池/结构资格、生成器、指标、损失、帕累托前沿 | 900/773/35/11；Playwright 查询结果 | 通过 |
| 真实状态不被包装 | 页面明确显示轮次已取消，结构与最终组合未完成 | 页面状态、提示与证据库文案 | 通过 |
| 来源完整 | 展示覆盖率、轮次状态、方法与工具身份；传输摘要仅内部校验，不暴露给用户 | `evidence-1920x1080.png`；`release-check.ps1` | 通过 |
| 示例数据不混入真实结果 | 加载与错误状态不渲染示例卡片，真实卡片只读取已校验快照 | Playwright 文本审计 | 通过 |
| 卡片粒度查询 | 同屏至少两张卡片具有不同查询、指标或筛选，修改一张不改变另一张 | `analysis-generated-1920x1080.png` | 通过 |
| 概览多选生成 | 多选流程节点/指标，生成多张卡并自动填充行、列、数值、分类 | `overview-selection-1920x1080.png` 与生成结果 | 通过 |
| 透视字段拖动 | 合法字段可跨槽移动；不合法字段显式拒绝并说明原因 | `pivot-1920x1080.png`；合法与非法拖动断言 | 通过 |
| 图表语义推荐 | 分布、阶段、二维目标分别推荐兼容图表；不提供无意义饼图 | 两档透视抽屉截图 | 通过 |
| 卡片拖动/缩放/显隐 | 标题拖动、右下角 resize、卡片库隐藏/恢复均稳定 | Playwright 路径与截图 | 待主线验收 |
| 尺寸响应 | 紧凑尺寸只显示关键信号，标准/展开尺寸逐步恢复细节 | `card-compact-resize-1920x1080.png`；浏览器断言 `compact` | 通过 |
| 布局持久化与损坏恢复 | 刷新后布局不变；损坏 localStorage 后恢复默认而不白屏 | Playwright reload + localStorage 注入 | 待主线验收 |
| 加载 / 空态 / 错误 / 离线 | 各状态都有可理解说明和恢复操作；后端离线仍可用本次会话已校验快照 | `offline-recovery-1920x1080.png`、`offline-frozen-analysis-1920x1080.png` | 通过 |
| 1080p / 1440p 发布视觉 | 1920×1080、2560×1440 下无重叠、裁切、横向溢出 | 两套分析、证据库、透视抽屉截图 | 通过 |
| 控制台无阻断错误 | 每条在线发布路径检查控制台；0 错误、0 警告 | Playwright 控制台输出 | 通过；离线请求失败为预期网络事件 |
| 可复现构建 | 安装、测试、构建、审计均通过 | `scripts/release-check.ps1` | 113/113；构建通过；审计 0 |
| 发布材料齐全 | 启动说明、演示脚本、限制、后续任务、验收矩阵均在仓库 | `docs/` 与 `scripts/` | 通过 |
| 独立分支已推送 | local/remote SHA 一致，工作树干净 | `git rev-parse`、`git ls-remote`、`git status` | 本数据分支通过；最终发布分支待验收 |

## 必测查询样例

1. AMP Designer 的 300 条原始 proposal、promoted unique 数和 admission 数。
2. 三个生成器在 `llamp_log10_mic_um` 上的分布，包含 n、median、IQR、missing、OOD。
3. Candidate pool 与 admitted 在溶血预测上的分布变化。
4. 按生成器查看 mature core / exploration / rejected 构成。
5. 点击 rejection reason，查看相应候选与保留集合的差异。
6. `llamp_log10_mic_um` × `macrel_hemolysis_probability` Pareto scatter，标出 structure eligible 候选。
7. 同屏保留上述两张不同 metric/filter 卡片并独立修改。
8. 将连续评分错误拖入 category 或选择 funnel without stage，确认显式拒绝。

## 发布前最终命令

```powershell
.\scripts\release-check.ps1 -Install -RequireCleanWorktree
git status --short --branch
git rev-parse HEAD
git ls-remote --heads origin '<final release branch>'
```
