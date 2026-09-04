# PostgreSQL 迁移运维规则

本文件是 PostgreSQL 迁移范围的唯一当前规则；具体字节与状态以紧凑 execution receipt 为准。

- 架构：本机 `127.0.0.1:55432` 仅为 SSH 转发；权威主库为 `.19:55433`。PostgreSQL 保存候选、评价、谱系和运行状态，迁移不得改变现行主库。
- 硬门：globals 与全部非模板库必须逐文件 SHA/size 校验；`.19` 恢复只能进入全新隔离实例/命名空间；禁止覆盖未知库、现行库、外来进程或远端大对象。
- 归属：`.19` AMPgent 目录归 `huangyueshan`；synth `.2` 只能使用已核验的 `synth` 子目录和容量。密码仅来自外部 DPAPI/SSH_ASKPASS，不进入文档、Git、命令或日志。
- 当前权威状态（2026-09-05 00:15:33 CST）：`.19` 与 `.2` 双备份目录及 6 文件 manifest 已逐文件互相校验；`.19:55434` 隔离恢复 5 库，`restore_verified=true`。源端 `public.ix_candidate_sequence_sha256` 因 `indisvalid=false` 明确列入排除对象，不声称 bitwise identical。
- 恢复入口：`deploy/windows/postgresql_migration_plan.ps1`；执行收据 `reports/postgresql_migration_execution_receipt_20260905.json`。本机未识别可安全删除的 PostgreSQL 数据目录，Docker VHD 不可访问，删除门保持关闭。
