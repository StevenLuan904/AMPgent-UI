from pathlib import Path


def test_ampgent_acea_execution_protocol_preserves_nonnegotiable_rules() -> None:
    root = Path(__file__).parents[1]
    protocol_path = root / "docs" / "ampgent-acea-execution-protocol.md"
    protocol = protocol_path.read_text(encoding="utf-8")
    normalized_protocol = " ".join(protocol.split())
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")

    assert "docs/ampgent-acea-execution-protocol.md" in agents
    required_footprints = (
        "192.168.99.32",
        "用户于 2026-08-12 明确授权使用除 `192.168.99.32` 外的其他 GPU",
        "AMPlify 已由用户永久停用",
        "absolute_difference <= 1e-8",
        "relative_difference <= 1e-6",
        "46796d6f-2c94-49fa-82e0-2d7716423b10",
        "0e9801456c1fcd6eddd3d87c6dbff9cd744228ace38208e03559d10af419cc7b",
        "20260911, 20260912, 20260913",
        "formal run not submitted",
        "amp_multiobjective_portfolio_v32.yaml",
        "database-only replay bundle",
        "Explicit positive-charge design is reserved",
        "255 passed",
        "role, physical host, PID, and explicit source revision",
        "812aa8404d7fae7620e13fafd66ff445ed9a1ec4424f8a287fe4fa6a9c78c62f",
        "fefaa3ce7c3b243e444fbd3037ab8a5829431759",
        "a12fc0d84b2e4fe3587eb1e351089f6a0d3b7172",
        "lost-response retry hazards",
        "255a412a79aa4e146b84429bda7ef0491cdc3130a281e820f4f932fba9a391c6",
        "control PID 34500, metrics PID 87616, and portfolio PID 67356",
        "不得为了推进而把任务发给位置或版本未知的 poller",
    )
    for footprint in required_footprints:
        assert footprint in protocol

    assert "资源许可不等于 formal run 科学授权" in protocol
    assert "### 21.17 2026-08-13 GPU 边界勘误与跨任务协调" in protocol
    assert "`.19 GPU4` 没有被禁止；`.32 GPU2/GPU3` 才是双方共同的绝对禁区" in normalized_protocol
    assert "019fcd9b-a14e-7741-a3ff-2fd0e1d3d4c7" in protocol
    assert "docs/ampgent-large-data-location-ledger.zh-CN.md" in protocol
    assert "eligible Boltz placement 为 `.19 GPU4`、`.19 GPU5`" in normalized_protocol
    assert "database-plus-object-store replay" in protocol
    assert "### 21.15 2026-08-13 持续工程环境与瓶颈评估规则" in protocol
    assert "pipeline barrier/backpressure → Agent 分析/决策延迟" in normalized_protocol
    assert "无 active workflow 不自动表示“可运行”或“健康”" in protocol
    assert "#### 21.15.1 API 端口勘误与关键路径修正" in protocol
    assert "错误检查 `127.0.0.1:8000`" in protocol
    assert "权威 API 地址为 `127.0.0.1:8080`" in normalized_protocol
    assert "不能凭记忆硬编码" in normalized_protocol
    assert "### 21.16 2026-08-13 `.19` GPU5 v37.0.4 worker 迁移" in protocol
    assert "PID `269615`" in protocol
    assert "v37.0.4 formal run 仍未提交" in normalized_protocol
    assert "### 21.18 2026-08-13 `.19 GPU4/GPU5` capacity-v2 部署" in protocol
    assert "PID `288726`" in protocol
    assert "PID 为 `289268`" in normalized_protocol
    assert "926a1c9cc9c1c52ffd12404190b3397bd0b2649dee941cc5a0cb8ff142cc8eba" in protocol
    assert "### 21.19 2026-08-13 submission GPU gate 勘误与最终 `.19` release" in protocol
    assert "PID 为 `290062`" in normalized_protocol
    assert "PID 为 `290212`" in normalized_protocol
    assert "cda153111e3e4f6bbb01720f0587e899b178cf9ec2626cdae65bcaf17b3146f3" in protocol
    assert "### 21.20 2026-08-13 implementation/worker revision identity correction" in protocol
    assert "execution.worker_source_revision" in protocol
    assert "Static preflight schema `1.3`" in protocol
    assert "073d24de432a784943379fcdb62d376e780d4164bb240695ab09cec5350d5711" in protocol
    assert "### 21.22 2026-08-13 current `.32` scoped GPU boundary" in protocol
    assert "unscoped GPU enumeration is forbidden" in protocol
    assert "### 21.23 2026-08-13 non-`.32` GPU availability snapshot" in protocol
    assert "`.19 GPU6` was the only newly available GPU" in protocol
    assert "### 21.24 scheduled idle-capacity wake rule" in protocol

    assert "sjtu@" not in protocol
    assert "forbids a weighted total" in protocol.lower()
