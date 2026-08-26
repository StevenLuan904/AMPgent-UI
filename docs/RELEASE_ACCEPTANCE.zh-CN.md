# AMPgent 发布验收矩阵

本矩阵用于证明发布目标，而不是记录开发意图。只有“证据”列中的文件、命令或 Playwright 产物实际存在且结果吻合时，才能将状态改为通过。

## 阻断级门槛

| 发布要求 | 验收方法 | 通过证据 | 当前检查点 |
|---|---|---|---|
| 顶层保留 Overview / Analysis / Evidence | Playwright snapshot，逐个导航并确认页面标题和可返回路径 | 主线 Playwright snapshot + screenshot | 待主线验收 |
| 不实现 Analysis Agent | 搜索 UI、路由、接口和文案；只允许 deterministic analytics 表述 | `Select-String` 审计结果 | 数据内核通过；主线待验收 |
| 真实 run 数据闭环 | 浏览器加载真实 snapshot，展示 raw、unique、pool/admitted、generator、metric、loss、Pareto | run `57afecc7…013d`；900/773/35/11；Playwright 查询结果 | 数据内核通过；UI 待验收 |
| 真实状态不被包装 | 页面明确显示 run=`cancelled`，structure/final portfolio incomplete | 页面状态 badge、warning 与 Evidence 文案 | 数据内核通过；UI 待验收 |
| provenance 完整 | 可查看 snapshot ID/SHA、coverage、warning、method/tool identity | Evidence 页面或卡片详情截图 | 数据内核通过；UI 待验收 |
| fixture 不混入真实结果 | 任何 fixture 均带“示例/未连接”标识；真实卡片不出现 fixture 数字 | Playwright 文本审计 | 待主线验收 |
| 卡片粒度查询 | 同屏至少两张卡片具有不同 query、metric 或 filter，修改一张不改变另一张 | Playwright 前后状态与 query 标识 | 规则测试通过；UI 待验收 |
| Overview 多选生成 | 多选流程节点/指标，生成多张卡并自动填充 row/column/value/category | Playwright 操作与卡片结果 | 规则测试通过；UI 待验收 |
| Pivot 字段拖动 | 合法字段可跨槽移动；不合法字段显式拒绝并说明原因 | Playwright 合法/非法各一例 | 规则测试通过；UI 待验收 |
| 图表语义推荐 | 分布、stage、二维目标分别推荐兼容图表；不提供无意义饼图 | 推荐列表和拒绝提示截图 | 规则测试通过；UI 待验收 |
| 卡片拖动/缩放/显隐 | 标题拖动、右下角 resize、卡片库隐藏/恢复均稳定 | Playwright 路径与截图 | 待主线验收 |
| 尺寸响应 | compact 只显示关键信号，standard/expanded 逐步恢复细节 | 三种尺寸截图；无遮挡/溢出 | 规则测试通过；UI 待验收 |
| 布局持久化与损坏恢复 | 刷新后布局不变；损坏 localStorage 后恢复默认而不白屏 | Playwright reload + localStorage 注入 | 待主线验收 |
| loading / empty / error / offline | 四态都有可理解说明和恢复操作；后端离线仍可用冻结快照演示 | Playwright 请求拦截或停后端复测 | adapter 测试通过；UI 待验收 |
| 1080p / 1440p 发布视觉 | 1920×1080、2560×1440 下无重叠、裁切、横向溢出 | 两套整页及关键抽屉截图 | 待主线验收 |
| 控制台无阻断错误 | 每条发布路径检查 console；0 error，允许解释后的非阻断 warning | Playwright console 输出 | 待主线最终验收 |
| 可复现构建 | clean install、测试、build、audit 均通过 | `scripts/release-check.ps1 -Install -RequireCleanWorktree` | 本检查点已通过非 clean install；最终分支重跑 |
| 发布材料齐全 | 启动说明、演示脚本、限制、后续任务、验收矩阵均在仓库 | `docs/` 与 `scripts/` | 本检查点完成模板；主线补最终证据 |
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

