# MVP-v2 Auto Research：run 2de37d8d 科学审计

## 结论

Run `2de37d8d-f1f4-4b41-a2a7-619fb66fcec2` 工程验收通过，但科学验收未通过。该 run 完成 3 代、48 个候选、11 次 PepMLM、36 次独立 Boltz-2、多种子界面审计、5 次各 200 decoy 的 FlexPepDock/InterfaceAnalyzer，以及 3 个保留原始 prompt/response 的决策节点。历史 run `959c8367-30cd-4505-9d59-78c8f121e31d` 和 `96892855-3d84-4334-8910-43092776eaaf` 均保持不变。

## 跨代证据

| generation | 候选 | PPL | pair-ipTM 中位数 | pocket coverage | pose cluster | gate | dG separated (REU) | 判断 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 0 | KSISGVVVVPAG | 5.693 | 0.192 | 0.840 | 0.333 | 失败 | -9.288 | 能量有利但种子姿态不一致 |
| 0 | KSSVNIIIPA | 6.339 | 0.177 | 0.800 | 0.667 | 通过 | +9.764 | 结构 gate 通过但 Rosetta 不支持 |
| 1 | KSSVNIAINH | 4.980 | 0.179 | 0.840 | 0.333 | 失败 | +34.254 | 两类证据均未改善 |
| 2 | ASSVNIAINH | 6.072 | 0.199 | 0.760 | 1.000 | 通过 | +36.874 | 多种子一致，但 Rosetta 明显不利 |
| 2 | KSAVVVVVVNGA | 6.204 | 0.168 | 0.800 | 0.333 | 失败 | -25.082 | Rosetta 强相对复排，但姿态不一致 |

PPL 均值由第 0 代 11.318 降至第 1 代 8.369，但第 2 代回升至 9.692。结构候选的 pair-ipTM 中位数均值从 0.175 降至 0.172、再降至 0.171；第 2 代只有 1/4 通过结构 gate。第 2 代出现互补证据：`ASSVNIAINH` 的三种子姿态完全一致，而 `KSAVVVVVVNGA` 获得最有利的 dG；但没有任何候选同时满足结构 gate 与负 dG，因此不能称为可信命中，也不能发布 MVP-v2。

## 最小可验证改动 v3

下一 run 保持 pocket 条件、三种子 Boltz-2、结构 gate、Rosetta 200 decoy、相似度上限和所有 evaluator 不变，只做一个相关的搜索预算调整：增加第 4 代，把每个父本的突变子代从 3 增至 4，并将每个长度的 de-novo 对照从 2 降至 1。假设是：围绕上述两个互补父本增加局部搜索，有机会产生同时保留姿态一致性与有利 Rosetta 相对复排的桥接候选；仍保留 de-novo 对照与 0.75 相似度上限，避免完全丢失多样性。

验收标准不变：必须存在结构 gate 通过、种子一致、Rosetta dG 为负且序列多样性仍受约束的候选；仅完成流程或单项指标改善不足以通过科学验收。
