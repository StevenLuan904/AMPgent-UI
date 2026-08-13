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
    assert "用户随后明确禁止 `.19` 的 GPU4" in protocol

    assert (
        "`192.168.99.32` remains a whole-host prohibition explicitly including GPU3 and GPU4"
        in protocol
    )
    assert "no login or probe is allowed" in normalized_protocol
    assert "must not be contacted even for inspection" in normalized_protocol
    assert "docs/ampgent-large-data-location-ledger.zh-CN.md" in protocol
    assert "该存储许可不授权 `.19` GPU4" in normalized_protocol
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

    assert "sjtu@" not in protocol
    assert "forbids a weighted total" in protocol.lower()
