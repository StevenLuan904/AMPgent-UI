# MVP-v2 Auto Research 运行审计

## Run 959c8367-30cd-4505-9d59-78c8f121e31d

该 run 于 2026-08-01 完成三轮真实闭环：48 个候选、11 次 PepMLM 调用、36 次独立 Boltz-2 结构采样、12 次坐标界面审计、3 次各 200 decoy 的 FlexPepDock/InterfaceAnalyzer，以及 3 个保留原始 prompt/response 的 Agent 决策节点。PostgreSQL、MinIO 和 Temporal 证据链完整；上游失败 run `96892855-3d84-4334-8910-43092776eaaf` 保持不变。

| generation | Rosetta 候选 | PPL | pair-ipTM 中位数 | pose cluster | Rosetta dG_separated (REU) | 判断 |
| ---: | --- | ---: | ---: | ---: | ---: | --- |
| 0 | KSISGVVVVPAG | 5.6931 | 0.1875 | 0.333 | -0.3713 | 弱结构证据；未过 gate |
| 1 | KSAVDISIPA | 3.0177 | 0.1951 | 0.667 | +13.3274 | Rosetta 不支持；未过 gate |
| 2 | KSIPGVEVVDAA | 4.7429 | 0.2025 | 0.333 | +17.4776 | Rosetta 不支持；未过 gate |

工程验收通过，但科学验收未通过。pair-ipTM 从 0.1875 小幅升至 0.2025，PPL 曾改善，但 12 个结构候选全部未通过集合 gate，后两轮 Rosetta dG 明显不利，不能称为可信 AceA 命中。

本轮暴露两个可复现的流程问题：

1. Boltz pocket constraint 被记录但 `force=false`，搜索没有稳定落在目标 pocket；下一版本改为显式 pocket-conditioned sampling。
2. 旧选择器把“存在 Rosetta 数值”本身当成奖励，导致唯一进入高成本精修、但 dG 为正的候选被自动抬高。v2 策略只奖励小于 0 REU 的相对有利 Rosetta 结果；不利结果保留为负证据，但不获得“做过计算”奖励。

另外，三 seed 的“至少两个姿势一致”应是 `2/3`；旧配置写成 `0.67`，实际只允许 `3/3`。下一版本固定使用精确的 `0.6666666666666666`。这些变更不修改旧 run，只通过新提交、新部署和新 run 验证。
