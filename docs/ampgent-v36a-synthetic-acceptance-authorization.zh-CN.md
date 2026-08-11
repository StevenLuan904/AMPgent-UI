# AMPgent v36a 合成数据库闭环验收授权对象

状态：`preregistered_not_authorized`

精确合同：`config/benchmarks/amp_harness_synthetic_acceptance_v36a.yaml`。

本文只把 v36 下一步收敛成一个可明确授权、不会误碰真实研究数据的对象，不构成授权，也不执行迁移。

## 1. 为什么需要这一阶段

v36 已有 typed harness lineage、repository primitive 和 database/object-store-only verifier，但现有证据只
来自内存 snapshot 与迁移 DDL 检查。它尚未证明：在真实 PostgreSQL 事务、外键、唯一约束、生命周期
事件和对象存储读取下，可以完整写入并重放一次 harness 演化闭环。

v36a 只验证工程闭环，不学习历史策略、不评价真实 harness，也不产生短肽。

## 2. 精确验收范围

授权后只允许：

1. 在共享 PostgreSQL 上以事务方式部署 `0009_candidate_occurrences → 0010_harness_evolution_lineage`；
2. 写入两个相互隔离的纯合成 scope：一个走到 `promote_for_declared_scope`，另一个走到
   `rollback_to_registered_ancestor`；
3. 每个 scope 固定 3 个 synthetic release、2 条 lineage edge、三阶段 trial、6 个配对 assignment；
4. 每个 assignment 对五类端点各写一条 synthetic outcome，共 60 条 outcome；
5. 把全部 immutable footprint、partition、assignment、blinding、budget、terminal decision 和 replay
   bundle 作为内容寻址 artifact，经 typed edge 接入数据库；
6. 只用 PostgreSQL 与对象存储分别重放两个场景，再生成 aggregate acceptance receipt。

总计必须是 0 Candidate、0 Evaluation。不得读取任何历史 candidate/evaluation，不得激活真实 harness，
不得提交 shadow/prospective challenger，也不得修改冻结 run。

## 3. 验收语义

允许结论只有：

- `synthetic_database_closure_accepted`；或
- `synthetic_database_closure_rejected`。

即使通过，也只证明数据库闭环可工作；不证明 harness 有改进，不授权历史 replay、shadow、prospective
trial、真实 promotion 或候选生成。那些阶段仍须各自预注册和授权。

## 4. 用户授权短语

只有用户明确回复 `授权 v36a 合成数据库闭环验收` 才视为授权。继续维护路线图、同意写代码或同意
离线测试都不能推定为执行授权。
