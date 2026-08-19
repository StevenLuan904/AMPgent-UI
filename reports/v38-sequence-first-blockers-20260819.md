# AMPgent v38 序列优先执行卡点与恢复账本（2026-08-19）

## 当前科学产出

- 不可变失败 run：`71e014fc-2454-4e04-a238-a4c5f1fcea34`。
- 已持久化 900 个 raw occurrence、773 条唯一有效序列、3092 条 Evaluation、12 个 ToolCall；尚无 Decision、结构结果或 replay。
- 773 条序列均完成 AMP-READ MIC、LLAMP MIC、ToxinPred3 分数与标签。
- 477 条为 Non-Toxin；其中 63 条的两个 MIC 预测都不高于 10 µM，5 条都不高于 5 µM。它们只是“序列层候选核心”，在溶血和理化证据补齐前不得称为最终优秀短肽。
- 当前靠前但仍不完整的序列包括 `VKMRRRWLLEKLQWKLKKLKKKLAM`、`KGKWKIRRFRRRWPPKKWPNRWRKR`、`AFSKWWKKLKSKIRSKLVTKGYA`、`ARIKKRILVKKLLKGAKKIRRKK`、`VIRIAWRRILQKLGEKLAKAT`。

## 已确认卡点

1. **缺失的科学证据**：全部 773 条仍缺 4 项理化指标与 3 项溶血指标，因此安全门、非加权 Pareto、知识卡 refinement admission 都不能闭合。
2. **精确工程根因**：`.venv/Lib/site-packages/_editable_impl_pepagent_platform.pth` 含 UTF-8 中文绝对路径；Windows Python 3.11 在导入 `site` 时按 GBK 读取，解释器在评分器代码执行前即崩溃。
3. **旧修复为何无效**：`PYTHONUTF8=1` 和 `PYTHONIOENCODING=utf-8` 不改变 Python 3.11 对该 `.pth` 的 locale 解码路径。
4. **资源判断**：当前关键路径是 CPU 序列评分器能否可靠启动，不是 GPU 算力。结构 GPU 在序列证据收敛前不应空耗；GPU2/GPU3 仍为禁区。

## 已完成的恢复动作

- 将 Windows no-site 启动方式写入可冻结的运行时合同：`python -S adapter.py`，合同用 `adapter_index=2` 明确绑定 `-S` 的位置。
- 子进程只显式加载该解释器自己的 `site-packages` 和冻结 adapter 所属的项目 `src`；两者进入四边界启动收据，未修改机器上的 `.pth`。
- 新增部署前真实 smoke CLI，走与正式 Activity 相同的 `run_v37_guarded_provider_subprocess` 四边界启动器。
- 2026-08-19 本机真实 smoke 已通过：
  - physicochemical：2/2 序列成功，launch receipt `aecae6f842fa4b122f8da09b3a0bd8b7bb5c016ec1114e97b7f283710ca50075`；
  - Macrel hemolysis：2/2 序列成功，launch receipt `7cc265f4e52444b787f93b0ecba65268043b4c229cd647b7cca28beb7fcf9c15`。
- 同日完成 773 个独立哨兵 ID 的全候选规模 smoke（交替使用两条固定哨兵序列，不读取或复用旧 run 的科学输出）：
  - physicochemical：773/773，launch receipt `c219354e763bd6867354e4aa991e35fa81d52a619d8c8e26c2983cf133dc79ec`；
  - Macrel hemolysis：773/773，launch receipt `f6b7d07c35e39523d7e8c1453929a329bb0c834d6f1213275ce2091d8164efac`。

## 不停歇恢复门与下一关键路径

1. 完成 no-site 合同、descriptor、preflight 和 smoke 的回归测试及 Ruff。
2. 冻结新 worker source/release；迁移 control/generator/metrics 和获准结构 worker，验证 poller、隧道及 GPU 边界。
3. 生成全新版本控制 run、请求、静态/动态 preflight；旧 run 保持不可变，绝不回填其 773 条结果。
4. exact-once 提交新 science run，从 900 occurrence 重新生成和 score-all；只有序列安全/活性层闭合后才进入双靶点结构。
5. 每次定时唤醒若阶段无推进，必须立即定位阻塞并执行安全修复或下一实验，不能把“检查无变化”当作完成。

## recovery-008 实际推进（2026-08-19 15:26 UTC）

- 新 science run `01124f6e-1a0d-4256-8d71-78c192244179` / Temporal
  `692938bb-4b91-4197-944b-609411b50d68` 已 exact-once 启动；不得再次提交、原地重试或回填。
- 九个生成 cell 已全部完成：900 个 raw occurrence，去重后 773 条有效唯一序列。
- 五个序列评分 Activity 已全部完成并持久化 8,503 条 Evaluation，等于 773×11；此前缺失的
  理化和 Macrel 溶血证据已在正式边界补齐。
- 序列 admission 得到 26 条 `mature_core`、124 条 `promising_uncertain` 和 623 条拒绝；其中
  26 条成熟核心与 9 条固定探索候选进入结构，共 35 条。结构预算余下 13 个位置未强制填充，未降低
  毒性或溶血安全门，也未触发知识卡 refinement（成熟核心已经达到最小 12 条）。
- 两个隔离靶点的结构 Activity 已同时调度到冻结 Boltz 队列；当前一项运行、一项等待同一获准 GPU1。
  此时 26 条只能称序列层成熟候选，必须等待双靶点结构、controls、Pareto 和 replay 后才可称最终组合。
- 自动控制循环曾错误绑定默认旧 state，且普通 `.venv` 启动会在 UTF-8 editable `.pth` 上按 GBK
  崩溃。commit `5dd7845` 已让 supervisor 使用显式 site-packages/source 的 `python -S`，读取最新
  versioned structure placement，并把当前 recovery state 绑定到 5 分钟 tick；后台 supervisor PID 30832。

## recovery-008 结构失败与 recovery-009 门（2026-08-19 15:38 UTC）

- recovery-008 已在结构阶段 terminal failed 并保持不可变：900 occurrence、773 Candidate、8503
  Evaluation、15 ToolCall、1 Decision、0 structure、0 replay。序列层 26 条 mature core 与 9 条探索候选
  仍是有效的本 run 证据，但不得回填或复用于恢复 run。
- 两个双靶点 Boltz Activity 都在 attempt 2 因无网络下载模型失败；实际 `.32 GPU1` cache 只有 0 字节
  `boltz2_aff.ckpt` 禁用哨兵，缺失 `boltz2_conf.ckpt` 与 `mols` 资源。这是结构 worker 部署完整性缺陷，
  不是短肽科学失败。
- 已从 `.19` 的已验证缓存向 `.32 GPU1` 隔离临时目录流式复制权重与分子资源；仅在固定字节数和
  SHA-256 全部匹配后原子提升，并在同一拟部署 launcher 上完成真实结构 smoke。
- v38 preflight 新增 fail-closed runtime cache attestation：必须绑定 Boltz 可执行文件、权重与分子归档的
  文件名/字节数/SHA、解包分子数和 guarded smoke SHA。只声明 worker/poller/weights SHA 不再足以提交。
