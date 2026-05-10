from __future__ import annotations


def test_trading_graph_forwards_generic_llm_timeout_and_retry_config(monkeypatch, tmp_path):
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    captured = []

    class FakeClient:
        def __init__(self, *, provider, model, base_url=None, **kwargs):
            captured.append({
                "provider": provider,
                "model": model,
                "base_url": base_url,
                "kwargs": kwargs,
            })

        def get_llm(self):
            class FakeLLM:
                pass
            return FakeLLM()

    monkeypatch.setattr("tradingagents.graph.trading_graph.create_llm_client", lambda **kwargs: FakeClient(**kwargs))
    monkeypatch.setattr("tradingagents.graph.trading_graph.set_config", lambda config: None)
    monkeypatch.setattr("tradingagents.graph.trading_graph.TradingMemoryLog", lambda config: type("FakeMemory", (), {"get_past_context": lambda self, ticker: ""})())
    monkeypatch.setattr("tradingagents.graph.trading_graph.GraphSetup", lambda *args, **kwargs: type("FakeSetup", (), {"setup_graph": lambda self, selected: type("FakeWorkflow", (), {"compile": lambda self: object()})()})())

    config = DEFAULT_CONFIG.copy()
    config.update({
        "data_cache_dir": str(tmp_path / "cache"),
        "results_dir": str(tmp_path / "results"),
        "memory_log_path": str(tmp_path / "memory.md"),
        "llm_timeout": 12,
        "llm_max_retries": 0,
    })

    TradingAgentsGraph(selected_analysts=["quant"], config=config)

    assert len(captured) == 2
    assert captured[0]["kwargs"]["timeout"] == 12
    assert captured[0]["kwargs"]["max_retries"] == 0
    assert captured[1]["kwargs"]["timeout"] == 12
    assert captured[1]["kwargs"]["max_retries"] == 0
