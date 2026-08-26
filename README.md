# AMPgent UI

AMPgent 的顶层产品框架。当前分支将 `Overview` 与 `Analysis` 设为并行工作区，并刻意不实现分析 Agent。

本版关注：

- 科学问题驱动的 Query Composer；
- 可拖动、缩放、隐藏、恢复并本地持久化的 12 列 Dashboard；
- Run quality、来源与损失、评分分布、安全性、Pareto 冲突、候选实验台等顶层卡片；
- 与 React 无关的 `AnalysisQuerySpec` / `AnalysisDataset` / provenance 契约；
- 清晰标注的 framework fixture，确保示例数值不会被误认为实验事实。

详细设计与后续填充顺序见 [docs/analysis-dashboard-framework.zh-CN.md](docs/analysis-dashboard-framework.zh-CN.md)。

## Local development

```powershell
npm install
npm run dev
```

默认打开 `http://127.0.0.1:5173`。现有 Overview API 通过 Vite 将 `/v1` 代理到 `http://127.0.0.1:8081`；Analysis 在 Analytics API 接入前使用显式标注的 fixture。

## Validation

```powershell
npm run build
```

交互验收需覆盖：进入/退出布局编辑、拖动卡片、缩放卡片、卡片库隐藏/恢复、生成器筛选联动、布局刷新后保留。
