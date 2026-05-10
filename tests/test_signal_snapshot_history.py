from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_update_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "update_signal_snapshot.py"
    spec = importlib.util.spec_from_file_location("update_signal_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_signal_snapshot_records_factor_deltas_and_explanations(monkeypatch):
    module = _load_update_script()
    monkeypatch.setattr(
        module,
        "_download_candles",
        lambda symbol: [
            {"date": "2026-05-04", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            {"date": "2026-05-05", "open": 101, "high": 103, "low": 100, "close": 102, "volume": 1200},
        ],
    )
    previous = {
        "timestamp": "2026-05-04T23:00:00+00:00",
        "market_score": 50,
        "factor_scores": {
            "equity_trend": 0,
            "volatility": 0,
            "rates": 0,
            "fx": 0,
            "news_sentiment": 0,
            "macro_risk": 0,
        },
    }

    snapshot = module.build_snapshot(
        ["SPY"],
        {"equity_trend": 7, "volatility": 5, "rates": -3, "fx": -2, "news_sentiment": 1, "macro_risk": -5},
        previous_snapshot=previous,
    )

    assert snapshot["market_score"] == 53
    assert snapshot["previous_market_score"] == 50
    assert snapshot["score_delta"] == 3
    assert snapshot["factor_deltas"] == {
        "equity_trend": 7,
        "volatility": 5,
        "rates": -3,
        "fx": -2,
        "news_sentiment": 1,
        "macro_risk": -5,
    }
    assert snapshot["score_base"] == 50
    assert snapshot["factor_breakdown"][0] == {
        "key": "equity_trend",
        "label": "지수 추세",
        "score": 7,
        "previous_score": 0,
        "delta": 7,
        "contribution_label": "+7점",
        "delta_label": "+7점",
        "description": "SPY/QQQ 등 주요 지수의 단기·중기 추세 강도",
    }
    assert "지수 추세 +7점" in snapshot["change_explanation"]
    assert "거시 리스크 -5점" in snapshot["change_explanation"]
    assert snapshot["change_summary"] == "시장 점수 50 → 53 (+3): 지수 추세 +7점, 변동성 +5점, 금리 -3점, 환율 -2점, 뉴스 심리 +1점, 거시 리스크 -5점"


def test_update_signal_snapshot_appends_jsonl_history(tmp_path, monkeypatch):
    module = _load_update_script()
    monkeypatch.setattr(
        module,
        "_download_candles",
        lambda symbol: [
            {"date": "2026-05-04", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            {"date": "2026-05-05", "open": 101, "high": 103, "low": 100, "close": 102, "volume": 1200},
        ],
    )
    previous = {
        "timestamp": "2026-05-04T23:00:00+00:00",
        "market_score": 50,
        "factor_scores": {"equity_trend": 0, "volatility": 0, "rates": 0, "fx": 0, "news_sentiment": 0, "macro_risk": 0},
    }
    (tmp_path / "signal_snapshot.json").write_text(json.dumps(previous), encoding="utf-8")

    snapshot = module.update_snapshot_files(
        tmp_path,
        ["SPY"],
        {"equity_trend": 7, "volatility": 5, "rates": -3, "fx": -2, "news_sentiment": 1, "macro_risk": -5},
    )

    assert snapshot["score_delta"] == 3
    saved = json.loads((tmp_path / "signal_snapshot.json").read_text(encoding="utf-8"))
    assert saved["change_summary"].startswith("시장 점수 50 → 53")
    history_lines = (tmp_path / "signal_snapshot_history.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(history_lines) == 1
    history_entry = json.loads(history_lines[0])
    assert history_entry["market_score"] == 53
    assert history_entry["previous_market_score"] == 50
    assert history_entry["factor_breakdown"][0]["label"] == "지수 추세"
    assert history_entry["factor_breakdown"][0]["delta_label"] == "+7점"
