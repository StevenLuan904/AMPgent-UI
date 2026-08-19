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
