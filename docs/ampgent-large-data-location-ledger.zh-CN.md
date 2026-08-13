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
4. `.19` 的存储授权不改变 GPU 边界：`.19` GPU4 仍绝对禁止；存文件也不授权启动未经批准的 formal run、访问他人目录或停止他人进程。
5. `192.168.99.32` 仍为整机禁止访问/探测/使用，不能作为存储位置。
6. 迁移必须先记录计划位置，再校验源/目标 SHA-256 或 manifest；只有权威副本和 replay 路径验证通过后，才可删除明确归属于 AMPgent 的临时副本。

## 3. 当前位置与归属

| 数据类别 | 角色 | 物理位置 | 精确位置/命名空间 | 归属与状态 | 完整性/恢复规则 |
|---|---|---|---|---|---|
| Git 控制面 | 非大数据、版本权威 | 本地工作站 `StevensOMEN9` | `D:\\DWorkspace\\yangyang\\皮肤抗菌短肽\\agent-platform` | AMPgent 仓库；仅代码、配置、文档、manifest、紧凑报告 | Git commit/SHA；不得新增权重、数据库、批量结构或运行输出 |
| Agent typed evidence | 关系型权威证据 | 本地 Docker 服务 | volume `pepagent_postgres-data` → `/var/lib/postgresql/data` | PostgreSQL；正式 Agent 流的节点、边、身份和生命周期 | 数据库原生 replay；不得用本地 CSV/报告回填 |
| 内容寻址 artifacts | 大对象权威证据 | 本地 Docker 服务（当前） | volume `pepagent_minio-data` → `/data` | MinIO；当前正式运行 artifact 字节的权威存储 | Artifact SHA-256、对象 key 与数据库引用必须一致；禁止手工搬移/删除 |
| 本地 `var/`、`runtime/`、`output/`、`outputs/` | 临时执行副本/历史缓存 | 本地工作站 | 仓库工作区对应目录 | 非权威；既有用户/历史文件保持原状，新大文件不得继续无登记累积 | 先验证对象存储与数据库 replay，再仅清理精确识别的 AMPgent 临时副本 |
| `.19` 大文件区 | 允许的模型/运行时/中间产物/缓存主位置 | `192.168.99.19`（`admin.cluster.local`） | 现有项目根 `/data1/huangyueshan/pepagent`；新数据约定为 `/data1/huangyueshan/pepagent/data/{models,runtimes,artifacts,run-cache}/<content-or-run-id>/` | AMPgent 专属目录；新路径首次写入前须核验并补充下方登记 | 内容 SHA/manifest + 来源 run/release；正式证据仍须进入 PostgreSQL + 对象存储 |
| synth 计算副本 | worker release/计算缓存，不作默认大数据归属 | synth 主机 | `/sdd_data/pepagent` 下精确 release 或任务目录 | 仅已映射的 AMPgent worker 使用；不得占用或移动他人内容 | release SHA、PID/role/host 映射；完成后按权威证据状态处置缓存 |

“当前”不表示已经把本地历史大文件迁移到 `.19`。在逐项核验大小、所有权、SHA 和 replay 前，不得声称迁移完成，也不得批量删除现有目录。

## 4. `.19` 新数据登记表

每次首次写入、迁移或生命周期变化时追加一行；不要修改历史行。`planned` 只表示路径预留，不表示字节已经存在。

| 日期 | 数据集/工件 | canonical/cache | 精确路径 | owner | 来源 run/release | 大小 | SHA-256/manifest | 保留/删除条件 | 状态 |
|---|---|---|---|---|---|---:|---|---|---|
| 2026-08-13 | AMPgent 大数据分层目录约定 | cache/execution-copy | `/data1/huangyueshan/pepagent/data/{models,runtimes,artifacts,run-cache}/<content-or-run-id>/` | AMPgent | n/a | 待首次写入核验 | 待首次写入核验 | 每个具体对象单独登记；不得覆盖内容地址目录 | planned |

## 5. 写入、迁移与清理清单

1. 在写入前确定数据类别、预计大小、canonical/cache 角色、owner、来源 run/release 和目标精确路径。
2. 对 `.19` 做最小只读所有权/容量核验；不得调度或探测 `.19` GPU4，不得停止共享 session 或他人任务。
3. 写入后计算 SHA-256 或生成有序 manifest，记录实际大小和完成时间。
4. 若属于 formal Agent 流，将 proposal occurrence、ToolCall、依赖、Evaluation、AgentDecision、Artifact、重试/失败和对象引用写入 PostgreSQL；权威 artifact 字节写入内容寻址对象存储。
5. 从 PostgreSQL + 对象存储执行 replay/完整性验证。远端目录存在或文件可打开不等于证据闭环完成。
6. 更新本账本状态。清理时只处理精确识别且归属于 AMPgent 的副本，禁止使用宽泛路径、未解析变量或跨 shell 拼接进行递归删除。

## 6. 维护责任

- 规则、主机、路径、run/release、对象存储后端、迁移状态或保留策略变化时，同一次变更维护本账本和执行协议。
- 每个登记必须可追溯到 Git commit；正式运行相关位置还必须可追溯到数据库 Artifact/ToolCall。
- 不在本文件记录密码、token、私钥或连接串。凭据只来自既定 secret 管理方式。
