# AMPgent 大数据归属与位置账本

状态：`active_inventory`

最后核对：2026-08-13
适用范围：模型权重、运行时归档、原始生成批次、结构集合、Rosetta decoy、数据库备份、回放包、图像和其他大体积工件。

## 1. 目的与权威关系

本文件专门回答“大数据应放在哪里、谁拥有、如何恢复”。它不是科学协议，也不授权运行：

- `AGENTS.md` 和 `docs/ampgent-acea-execution-protocol.md` 决定操作与资源边界；
- 冻结 benchmark/config 决定科学合同；
- PostgreSQL 的 typed evidence graph 与内容寻址对象存储共同构成正式 Agent 流的权威证据；
- 本账本记录大体积字节的物理归属、缓存位置、完整性和生命周期。

单文件预计达到 `100 MiB`、目录/批次预计达到 `1 GiB`，或属于模型、数据库、原始生成、结构轨迹/decoy、运行时归档的内容，均按“大数据”管理。即使未达到阈值，只要持续增长或重复保存也应进入本账本。

## 2. 存放原则

1. 本地工作站只长期保存代码、配置、文档、manifest、紧凑报告和内容地址指针；大文件下载只作有界临时缓存。
2. 正式运行的权威字节进入内容寻址对象存储；PostgreSQL 保存 ToolCall、依赖、候选、Evaluation、AgentDecision、Artifact 和生命周期事件。CSV/JSON/远端目录只是导出或执行副本。
3. `192.168.99.19` 被授权作为 AMPgent 大文件存储主机，可承载模型、独立运行时、结构中间产物和运行缓存。首次写入任何新目录前，必须确认目录属于 AMPgent、容量充足且不会干扰他人，并在下表登记精确路径。
4. `.19` GPU 只在精确归属、release 和非干扰门禁通过后用于 AMPgent；存文件仍不授权启动未经批准的 formal run、访问他人目录或停止他人进程。
5. `192.168.99.32` 的 GPU2/GPU3 只允许逐卡只读检查，禁止计算、worker、预约或进程控制；GPU0/GPU1 也必须以最新实况与外来进程保护为准。该主机不是本账本的允许存储位置。
6. `192.168.99.2`（synth）获准作为 AMPgent 计算与大数据主机；数据根为 `/amax/data` 与 `/sdd_data`。首次写入前必须核验空间、owner 和外来任务，选择并登记精确 AMPgent 子目录。连接凭据只保存在外部 secret 机制，不进入本账本。
7. 迁移必须先记录计划位置，再校验源/目标 SHA-256 或 manifest；只有权威副本和 replay 路径验证通过后，才可删除明确归属于 AMPgent 的临时副本。

## 3. 当前位置与归属

| 数据类别 | 角色 | 物理位置 | 精确位置/命名空间 | 归属与状态 | 完整性/恢复规则 |
|---|---|---|---|---|---|
| Git 控制面 | 非大数据、版本权威 | 本地工作站 `StevensOMEN9` | `D:\\DWorkspace\\yangyang\\皮肤抗菌短肽\\agent-platform` | AMPgent 仓库；仅代码、配置、文档、manifest、紧凑报告 | Git commit/SHA；不得新增权重、数据库、批量结构或运行输出 |
| Agent typed evidence | 关系型权威证据 | 本地 Docker 服务 | volume `pepagent_postgres-data` → `/var/lib/postgresql/data` | PostgreSQL；正式 Agent 流的节点、边、身份和生命周期 | 数据库原生 replay；不得用本地 CSV/报告回填 |
| 内容寻址 artifacts | 大对象权威证据 | 本地 Docker 服务（当前） | volume `pepagent_minio-data` → `/data` | MinIO；当前正式运行 artifact 字节的权威存储 | Artifact SHA-256、对象 key 与数据库引用必须一致；禁止手工搬移/删除 |
| 本地 `var/`、`runtime/`、`output/`、`outputs/` | 临时执行副本/历史缓存 | 本地工作站 | 仓库工作区对应目录 | 非权威；既有用户/历史文件保持原状，新大文件不得继续无登记累积 | 先验证对象存储与数据库 replay，再仅清理精确识别的 AMPgent 临时副本 |
| `.19` 大文件区 | 允许的模型/运行时/中间产物/缓存主位置 | `192.168.99.19`（`admin.cluster.local`） | 现有项目根 `/data1/huangyueshan/pepagent`；新数据约定为 `/data1/huangyueshan/pepagent/data/{models,runtimes,artifacts,run-cache}/<content-or-run-id>/` | AMPgent 专属目录；新路径首次写入前须核验并补充下方登记 | 内容 SHA/manifest + 来源 run/release；正式证据仍须进入 PostgreSQL + 对象存储 |
| synth 计算副本 | worker release/计算缓存，不作默认大数据归属 | synth 主机 | `/sdd_data/pepagent` 下精确 release 或任务目录 | 仅已映射的 AMPgent worker 使用；不得占用或移动他人内容 | release SHA、PID/role/host 映射；完成后按权威证据状态处置缓存 |
| synth 大文件区 | 允许的模型/运行时/生成/结构/轨迹缓存与远端导出 | `192.168.99.2`（`synth`） | 获准根 `/amax/data`、`/sdd_data`；具体写入必须使用下表登记的 AMPgent 子目录 | 已获用户授权；实时容量、目录owner及外来任务尚须逐次核验 | 有序 SHA/manifest + 来源 run/release；正式证据仍须进入 PostgreSQL + 内容寻址对象存储 |

“当前”不表示已经把本地历史大文件迁移到 `.19`。在逐项核验大小、所有权、SHA 和 replay 前，不得声称迁移完成，也不得批量删除现有目录。

## 4. `.19` 新数据登记表

每次首次写入、迁移或生命周期变化时追加一行；不要修改历史行。`planned` 只表示路径预留，不表示字节已经存在。

| 日期 | 数据集/工件 | canonical/cache | 精确路径 | owner | 来源 run/release | 大小 | SHA-256/manifest | 保留/删除条件 | 状态 |
|---|---|---|---|---|---|---:|---|---|---|
| 2026-08-13 | AMPgent 大数据分层目录约定 | cache/execution-copy | `/data1/huangyueshan/pepagent/data/{models,runtimes,artifacts,run-cache}/<content-or-run-id>/` | AMPgent | n/a | 待首次写入核验 | 待首次写入核验 | 每个具体对象单独登记；不得覆盖内容地址目录 | planned |
| 2026-08-13 | v37.0.4 平台发布归档 | immutable execution release | `/data1/huangyueshan/pepagent/bootstrap/platform-e1f1d0a3e7211a83cc1fdd62e2989ba2511844f9eb8ed791b85caf87c130a3dd.tar.gz` | AMPgent | source `22f564e0fdde67aed97779d9185dbe929661c882` | 1,114,245 bytes | `e1f1d0a3e7211a83cc1fdd62e2989ba2511844f9eb8ed791b85caf87c130a3dd` | 保留至该 release 不再承担 worker/replay；删除前核对无 active worker 和可恢复权威副本 | active |
| 2026-08-13 | v37 capacity-v2 平台发布归档 | immutable execution release | `/data1/huangyueshan/pepagent/bootstrap/platform-926a1c9cc9c1c52ffd12404190b3397bd0b2649dee941cc5a0cb8ff142cc8eba.tar.gz` | AMPgent | source `ace90cd0e383c079caff7735bd7e664f2ca31c70` | 1,123,713 bytes | `926a1c9cc9c1c52ffd12404190b3397bd0b2649dee941cc5a0cb8ff142cc8eba` | 保留至 GPU4/GPU5 worker 与对应 replay 不再使用；删除前核对无 active worker 和可恢复权威副本 | active |
| 2026-08-13 | v37 corrected GPU-gate 平台发布归档 | immutable execution release | `/data1/huangyueshan/pepagent/bootstrap/platform-cda153111e3e4f6bbb01720f0587e899b178cf9ec2626cdae65bcaf17b3146f3.tar.gz` | AMPgent | source `8bdeb39fcc0df7c635e13a4aefa56a6c6a2bb4e3` | 1,124,763 bytes | `cda153111e3e4f6bbb01720f0587e899b178cf9ec2626cdae65bcaf17b3146f3` | 当前 GPU4/GPU5 worker release；删除前核对无 active worker 和可恢复权威副本 | active |
| 2026-08-27 | HemoPI2/APEXGo/PeptiVerse shadow 冲突分析包 | off-workstation content-addressed export/cache；非 formal object-store 证据 | `/data0/ampgent-pepglad-huangyueshan/v1/artifacts/shadow-challenger-bundles/fe190e874eb2ed9dfb5051838571523a736af9d8fc49ae2faffd43103f9a053a/` | AMPgent | code `0a48c429c741891d359baa71999493e94e30d76e`；600 条冻结 cohort | 9,075,312 bytes | `remote_receipt.json` SHA-256 `9dcca9e0030b2bdbf12269b9e264145da431f185eabe08af4c432a0762c19d6d`；`SHA256SUMS` SHA-256 `d31645d41104a566aed790e7221e25ee0c824d60213a7a11a3f1c0218aa165ff` | 已逐文件回读校验；本机模型、结果中转和本机 MinIO 本轮对象均已清除；待非本机正式对象存储可用后迁入并更新状态 | active_export |
| 2026-08-27 | 黄金候选003 GyrA/PBP2a复合物OpenMM轨迹 | remote execution copy / growing trajectory；紧凑收据进入正式对象存储 | `/data1/huangyueshan/pepagent/md/gold-003-20260827/` | AMPgent；GPU6 PID 2799132 / GPU7 PID 2799133 | 候选003；GyrA输入 `fbc07e...6579`、PBP2a输入 `a57258...c0d3`；runner `9f7516...04e2` | 当前约255 MiB，预计完整轨迹约15 GiB | 启动收据 `08974df52ad7055330c617bafbdc5d12775ed729eec71734b699c3d31bca2bff`；最新进度收据 `c99d6ad01e64cf07a9ed97b52d40993f0c45e784cb61b18f4f25a4c6128ef473` | 运行中保留checkpoint/日志/轨迹；完成后生成有序SHA manifest并把紧凑结果写PostgreSQL/对象存储；确认可恢复前不得删除 | active_running |
| 2026-08-28 | 黄金候选003双复合物MD NPT→NVT阶段转换 | lifecycle update；remote growing trajectory | `/data1/huangyueshan/pepagent/md/gold-003-20260827/production/{gyra,pbp2a}/` | AMPgent；同一未重启PID 2799132/2799133 | 启动收据 `08974d...2bff` 的连续运行 | 约3.8 GiB且持续增长 | 阶段转换收据 `66ed1efdfe813ab988e940c71826641ff85dc326dc9b7c053966bbe5f1a737ee`；GyrA 17.7%、PBP2a 32.2% production | 两分支1 ns NPT均完成；继续保留并监测50 ns NVT、checkpoint和非有限值；不得重复提交 | active_production |
| 2026-08-28 | synth AMPgent 分层目录预留 | cache/execution-copy/export | `/amax/data/<ampgent-project>/...` 与 `/sdd_data/<ampgent-project>/...`（精确子目录待首次写入前核验） | AMPgent；不得占用他人目录 | n/a | `/amax` 约18 TiB/余798 GiB；`/sdd_data`约7.0 TiB/余1.4 TiB | 首次写入前记录实际路径、owner、来源和 SHA/manifest；优先 `/sdd_data` | authorized_capacity_checked |
| 2026-08-28 | synth GPU2 PBP2a Boltz→Rosetta dG lane | remote execution copy；紧凑收据后续进入内容寻址对象存储 | `/sdd_data/pepagent/ampgent/structure/pbp2a-gpu2-rosetta-20260828-v1/` | AMPgent；父PID `904411`；GPU2仅供Boltz，Rosetta最多6个CPU decoy并发 | spec `278012...68ec`；runner `e6fb3b...a49`；两条PBP2a优秀候选 | 运行中，预计远小于MD轨迹 | launch receipt `d10d3bcd024ceed256519c7dbda291c5aefa9aaf1fa89d52c1ae989afcb4d6cc`；完成后补有序SHA manifest和dG结果收据 | 运行中保留输入、200 decoy/候选、日志和收据；内容寻址证据及replay校验完成前不得清理远端输出；本机中转按精确路径清除 | active_running |
| 2026-08-28 | synth PBP2a结构lane部分dG checkpoint | lifecycle/progress receipt | `/sdd_data/pepagent/ampgent/structure/pbp2a-gpu2-rosetta-20260828-v1/progress_receipts/checkpoint_20260827T180728Z.json` | AMPgent；Boltz已结束并释放GPU2；两条Rosetta CPU lane继续 | 同一task；PepGLAD 41/200、factorized 49/200 decoy | 紧凑JSON | `5cbc5b3c29428f3e2b2e192698d01235c852d261480289e8f5eb94f000f498ea`；Boltz结果 `e942bae9...856` | 仅作provisional checkpoint；完成收据与内容寻址证据闭合前保留，不据此启动MD | active_rosetta |
| 2026-08-28 | synth PBP2a因子化候选正式Rosetta dG结果 | lifecycle/result receipt；remote execution evidence | `/sdd_data/pepagent/ampgent/structure/pbp2a-gpu2-rosetta-20260828-v1/results/factorized_rosetta_result.json`；进度收据 `progress_receipts/checkpoint_20260827T200942Z.json` | AMPgent；factorized已200/200，PepGLAD继续 | 同一task；候选 `pbp2a-factorized-4782247c379eafe2654f` | 紧凑JSON；200个decoy留在同一远端任务根 | result `f9a5a4d1be3fb6b8dc412e80ad799bd52e74aaac1bd82d18d3a42547bb574896`；checkpoint `fb5922fae1357cf7111baf6a87cbbd806646977f14879b1d42f28525450ff201`；输入 `136577b1...59ba` | 正式主dG -67.9363 REU，但Boltz pair-ipTM 0.1147，标记低姿势置信度；保留并等待另一候选与lane总完成收据，不启动MD | factorized_rosetta_complete_pepglad_running |
| 2026-08-28 | synth PBP2a结构lane完整收敛与内容寻址结果包 | remote execution evidence + compact content-addressed export | 执行根 `/sdd_data/pepagent/ampgent/structure/pbp2a-gpu2-rosetta-20260828-v1/`；结果包 `/sdd_data/pepagent/ampgent/artifacts/structure-results/e25b3cc348ad9a90c30db789099f1610276652854ceba1c69014250545318dd7/` | AMPgent；两候选均200/200，父进程退出，GPU2释放 | 同一task；PepGLAD与factorized两端 | 执行根549 MiB/832个文件；紧凑包388 KiB | manifest `e25b3cc348ad9a90c30db789099f1610276652854ceba1c69014250545318dd7`；completion `e47fa0c96f2415c87b0bbd6bd0831b082d367022391957c8e82539f2aa4f8b83`；artifact receipt `8fea255d69da6ad1548291b9ccba2557532553801a42d0153a3804abb0d435b4`；PepGLAD result `7b299a6d...b13f` | 执行根与CAS包均保留至正式对象引用/replay验证；PepGLAD主dG +66.7342不通过，factorized -67.9363但低姿势置信；均不自动启动MD | succeeded_cas_frozen |
| 2026-08-28 | 黄金候选003 MD PBP2a完成/GyrA继续 | lifecycle/progress receipt | `/data1/huangyueshan/pepagent/md/gold-003-20260827/progress_receipts/checkpoint_20260827T220709Z.json` | AMPgent；PBP2a PID 2799133已退出，GyrA PID 2799132继续 | 启动收据 `08974d...2bff` 的连续运行；未新增MD | 紧凑JSON；大轨迹仍只在`.19` | `a355db4ebc284496e3fb7b8929c29e032c5b0f174e9ac085ba4baaead6452124`；PBP2a 100%、GyrA 77.3% | PBP2a结果保留待最终分析；只继续监测GyrA精确PID/checkpoint，不重复提交 | pbp2a_complete_gyra_running |
| 2026-08-28 | GyrA PepGLAD r19 10–13 aa新根批次 | remote generation execution copy；生成后评分再冻结 | `/data0/ampgent-pepglad-huangyueshan/v1/results/gyra-pepglad-novel-root-r19-short10-13-20260827T2212Z*` | AMPgent；`.19 GPU7` PID 1167776 | seed 2026082764；上一轮同靶点短根的独立新seed | 运行中；请求768 raw occurrence | request `08b7d10eb732b8213401305d0e352115b216f00a2877e3cabfba23f2ceb65327`；launch receipt `a157027bf2c4193472ab0fe6c2f4fe0d0dc0e8218b3e87e32d823beb04711ec0` | 完成后全量12项评分、硬门、全局去重和家族验收；本机不保存生成结构；不得重复提交 | generation_active_scoring_pending |
| 2026-08-28 | GyrA PepGLAD r19严格扩库完成包 | off-workstation content-addressed export；全量评分与冻结证据 | `/data0/ampgent-pepglad-huangyueshan/v1/artifacts/strict-library/ccd09ff8dbc10d682d4fecc70d26535391014c2dac834b2ec5841b9047e03a36/` | AMPgent；`.19 GPU7`已释放 | r19 generation `7ae9fbee...89d90`；12项评分后全局冻结 | 远端94 MiB | final CSV `ccd09ff8dbc10d682d4fecc70d26535391014c2dac834b2ec5841b9047e03a36`；score receipt `0672af9328bacc79d4de60870afeb37262391211ca9eee3f69f649151ac179bc`；strict subset `8c6b069cc1e59a988ff681c6cbffe1127974dece1ab382c3a53fc7b758b46732`；freeze receipt `5ff9320db33d76e8a320edaf35fef117bcfcd7788303c821fc257d365b88bbdc` | 88,421条严格候选、9,089家族；432条/432家族为本批新增；远端校验后删除本机精确中转目录 | succeeded_cas_frozen |
| 2026-08-28 | PBP2a PepGLAD r19 10–13 aa新根批次 | remote generation execution copy；生成后评分再冻结 | `/data0/ampgent-pepglad-huangyueshan/v1/results/pbp2a-pepglad-novel-root-r19-short10-13-20260828T0022Z*` | AMPgent；`.19 GPU7`父PID 1754411/GPU PID 1754414 | seed 2026082765；PBP2a独立新seed | 运行中；请求768 raw occurrence | request `4b79559dc2867112f11aa9d65a2eece0a68bb9d35c6fa97132c71cf186d9f364`；launch receipt `1fb6b43c7a6425a6f81fe8187970063f2be84448b36243e1a92bc0df44ffc9c9` | 完成后全量12项评分、展示硬门、全局去重和家族验收；不得重复提交；本机不保存生成结构 | generation_active_scoring_pending |
| 2026-08-28 | 黄金候选003双复合物MD完成 | lifecycle/completion receipt；大型轨迹远端保留 | `/data1/huangyueshan/pepagent/md/gold-003-20260827/`；完成收据 `progress_receipts/completion_20260828T0028Z.json` | AMPgent；GyrA/PBP2a进程均正常退出；未新增MD | 候选003；同一启动收据 `08974d...2bff` | 两条50 ns生产轨迹；大型字节仅远端 | completion `8de29f1f23e430a244dc328a8533d9c624d3df839d1a4274d94b6587225c86ff`；GyrA manifest `eae4dfab...4779`；PBP2a manifest `dbf029cb...d301` | 保留轨迹供RMSD/RMSF/界面接触后处理；紧凑分析入正式证据后再评估生命周期；不得据此自动新增MD | succeeded_analysis_pending |

## 5. 写入、迁移与清理清单

1. 在写入前确定数据类别、预计大小、canonical/cache 角色、owner、来源 run/release 和目标精确路径。
2. 对 `.19` 做最小只读所有权/容量核验；GPU4/GPU5 仅用于已明确归属的 AMPgent worker，不得停止共享 session 或他人任务。
3. 写入后计算 SHA-256 或生成有序 manifest，记录实际大小和完成时间。
4. 若属于 formal Agent 流，将 proposal occurrence、ToolCall、依赖、Evaluation、AgentDecision、Artifact、重试/失败和对象引用写入 PostgreSQL；权威 artifact 字节写入内容寻址对象存储。
5. 从 PostgreSQL + 对象存储执行 replay/完整性验证。远端目录存在或文件可打开不等于证据闭环完成。
6. 更新本账本状态。清理时只处理精确识别且归属于 AMPgent 的副本，禁止使用宽泛路径、未解析变量或跨 shell 拼接进行递归删除。

## 6. 维护责任

- 规则、主机、路径、run/release、对象存储后端、迁移状态或保留策略变化时，同一次变更维护本账本和执行协议。
- 每个登记必须可追溯到 Git commit；正式运行相关位置还必须可追溯到数据库 Artifact/ToolCall。
- 不在本文件记录密码、token、私钥或连接串。凭据只来自既定 secret 管理方式。
