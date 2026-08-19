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

## recovery-009/011 运行时闭环与新 run（2026-08-19 16:29 UTC）

- `.19 -> .32` 内网直传完成；`.32 GPU1` 的正式 Boltz cache 已验证：`boltz2_conf.ckpt`
  SHA `090e82ac...28e1`、`mols.tar` SHA `39e076d9...1fd7`、45,227 个解包分子文件。
- 最终 source `fefbdbc86d1499dfe04ff109f5cde4823f0924de` / release
  `d8e2871b1f0dfab96b4d934cd0fca757b3cfbebb77f96001b76ede024b3d4dc7` 在 `.32 GPU1`
  完成真实最小 Boltz smoke；输出 SHA `aec3c3fdcebd18f1e1925b1c79828b0e10a500048f6960649503ead47f084aa6`，
  产生 CIF、PAE/PDE、pLDDT、confidence 与 processed constraints/structure 等产物。它只作为工程哨兵。
- local launcher 也改为 `python -S` 加显式 site-packages/release source，防止 worker 自身再次被中文路径
  editable `.pth` 的 GBK 解码阻断。五个 science worker 已统一到上述 source/release；Boltz PID 287161，
  Rosetta PID 320310，本地 control/generator/metrics PID 76692/24040/76496。
- 新控制 run `d85e7ca1-5d1a-4d23-a439-c4e3ee225e80` 继承 63 个 terminal run；新 science run
  `acd2c705-82ce-4505-b72f-4bb6a9790428` / Temporal `10228478-75bc-4053-b664-e6b6f9470561`
  已以 formal key `94d7d625...a8432b` exact-once 提交。旧 run 均保持不可变，未回填或复用输出。

## recovery-009 结构证据链失败与修复（2026-08-19 17:34 UTC）

- science run `acd2c705-82ce-4505-b72f-4bb6a9790428` 已 terminal failed 并保持不可变：900
  occurrence、773 Candidate、8,503 Evaluation、17 ToolCall、1 Decision、0 durable structure、0 replay。
- Boltz 与 Rosetta 科学计算均实际成功：首个 pose 生成完整 Boltz 坐标，Rosetta 生成 16/16 个唯一 decoy；
  失败发生在 `persist_v38_multitarget_rosetta` 的证据模型校验，不是结构计算失败。
- 原模型错误地要求 decoy 输入哈希等于 Boltz CIF 哈希。真实可靠链是 `Boltz CIF -> 转换后的 PDB ->
  prepared PDB -> prepacked PDB -> decoy`；这五个对象内容不同，哈希理应不同。
- evidence schema 已升级为 `.2`，显式绑定上述四个输入节点，并要求所有 decoy 精确绑定 prepacked
  输入；同时仍要求 provenance 中的源坐标精确等于 Boltz artifact。
- 28 项 focused tests 与 Ruff 通过；对 Temporal event 168 的真实 payload 原样重放通过，16 个 decoy
  输出均唯一，receipt SHA `d9459ec501d4957a91f6b7a3c3ccec9f014a7b9572ac3db526c278fb85d366d0`。
  该 smoke 只验证工程证据链，不回填旧 run，也不作为候选科学证据。

## recovery-010 指标 descriptor 回退与 fail-closed 修复（2026-08-19 19:37 UTC）

- science run `56e0acd2-72a3-41f8-8e95-7a1b464aafaa` / Temporal
  `d8cc4660-1bae-4a35-ad07-cd4a67c85330` 已 terminal failed 并保持不可变：900 occurrence、
  773 Candidate、3,092 Evaluation、12 ToolCall、0 Decision、0 structure、0 replay。
- AMP-READ、LLAMP 与 ToxinPred3 证据已落库；Macrel hemolysis 与 physicochemical Activity 在解释器执行
  adapter 之前因中文路径 `.pth` 的 GBK 解码失败。根因不是 no-site launcher 失效，而是 recovery-010
  preflight 未绑定已验证的两个 runtime override，允许 `adapter_index=1` 的旧 descriptor 通过。
- v38 preflight 现已 fail-closed：`physicochemical_developability` 与 `hemolysis_risk` 都必须冻结
  `adapter_index=2`（`python -S adapter.py`），否则在生成请求或提交之前立即拒绝。
- recovery-011 的真实 guarded smoke 已再次通过：physicochemical 2/2、Macrel 2/2，二者 returncode 均为 0；
  receipt 文件 SHA-256 为 `11d6f77415a9708212e6cbd621c7eaf544552aca7de16924ddbd8b668cdc6c2d`。
- 未使用 recovery-010 的科学输出进行回填；新 run 仍必须从 900 raw occurrence 独立开始。
