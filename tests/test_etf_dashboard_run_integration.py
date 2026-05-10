from __future__ import annotations

from fastapi.testclient import TestClient

from tradingagents.dashboard.app import create_dashboard_app
from tradingagents.dashboard.storage import AnalysisRepository


def _etf_record() -> dict:
    return {
        "run_id": "etf-adaptive-core-v2-2026-05-01-agent-run",
        "ticker": "ETF-ADAPTIVE-CORE-V2",
        "trade_date": "2026-05-01",
        "generated_at": "2026-05-04T00:00:00+00:00",
        "rating": "HOLD",
        "trader_action": "관망/월납입 유지",
        "research_recommendation": "Adaptive Core v2 균형형 유지",
        "decision_summary": "ETF 월납입 자산배분은 단순 DCA 대비 소폭 우위이나 표본 부족으로 실거래 투입은 금지한다.",
        "investment_thesis": "기존 보유분 매도보다 월납입/현금비중/리밸런싱 규칙으로 대응한다.",
        "price_target": None,
        "time_horizon": "장기 월납입",
        "snippets": {"market": "거시 국면", "sentiment": "", "news": "", "fundamentals": ""},
        "reports": {
            "market_report": "매크로 국면 분석 에이전트\n위험자산 우호 구간이지만 전쟁/유가 리스크는 감시한다.",
            "quant_strategy_report": "ETF 퀀트 배분 전략 에이전트\nSPY/QQQ/SCHD/GLD/BIL 목표비중을 월별 조정한다.",
            "news_report": "시스템 백테스트 검증 에이전트\n단순 DCA 대비 초과수익과 MDD를 검증했다.",
            "fundamentals_report": "리스크 제어 에이전트\nRisk-Off 발동은 0회이며 과최적화 위험이 있다.",
            "portfolio_manager_decision": "포트폴리오 매니저 최종 판단\n**Rating**: HOLD\n**Executive Summary**: 월납입 유지, 실거래 연결 금지.",
            "final_trade_decision": "**Rating**: HOLD\n**Executive Summary**: ETF 포트폴리오 run은 기존 상세보기에서 확인한다.",
            "trader_investment_decision": "**Action**: 관망/월납입 유지",
            "investment_plan": "**Recommendation**: Adaptive Core v2 균형형 유지",
        },
        "report_lengths": {},
        "raw_log_path": "",
        "structured_path": "",
        "metadata": {"market": "ETF", "display_name": "ETF Adaptive Core v2", "workflow": "portfolio_allocation"},
        "raw_state": {},
        "portfolio_backtest": {
            "available": True,
            "benchmark_name": "단순 DCA 70/30",
            "assumptions": ["월 100만원 납입", "비용/세금/환율 미반영", "live_capital_allowed=false"],
            "results": [
                {
                    "strategy_id": "satellite_soxl_momentum",
                    "display_name": "SOXL 위성 공격형",
                    "final_value": 18572004.49,
                    "profit": 5572004.49,
                    "total_return_percent": 42.8616,
                    "max_drawdown_percent": -11.5067,
                    "risk_off_count": 0,
                    "last_weights": {"SPY": 0.45, "QQQ": 0.25, "SOXL": 0.15, "GLD": 0.05, "BIL": 0.10},
                },
                {
                    "strategy_id": "simple_dca_70_qld_30_spy",
                    "display_name": "QLD 70 / SPY 30",
                    "final_value": 16167408.46,
                    "profit": 3167408.46,
                    "total_return_percent": 24.3647,
                    "max_drawdown_percent": -13.9742,
                    "risk_off_count": 0,
                    "last_weights": {"QLD": 0.7, "SPY": 0.3},
                },
                {
                    "strategy_id": "adaptive_core_v2_balanced_confirmed",
                    "display_name": "Adaptive Core v2 균형형",
                    "final_value": 14707705.68,
                    "profit": 1707705.68,
                    "total_return_percent": 13.1362,
                    "max_drawdown_percent": -7.3686,
                    "risk_off_count": 0,
                    "last_weights": {"SPY": 0.5, "QQQ": 0.3, "SCHD": 0.1, "GLD": 0.05, "BIL": 0.05},
                },
                {
                    "strategy_id": "simple_dca_70_spy_30_qqq",
                    "display_name": "단순 DCA 70/30",
                    "final_value": 14659680.97,
                    "profit": 1659680.97,
                    "total_return_percent": 12.7668,
                    "max_drawdown_percent": -7.8024,
                    "risk_off_count": 0,
                    "last_weights": {"SPY": 0.7, "QQQ": 0.3},
                },
            ],
            "data_range": ["2025-05-01", "2026-05-01"],
            "asset_returns": {"SPY": 30.52, "QQQ": 40.64, "GLD": 42.26},
            "live_readiness": {
                "status_label": "실거래 보류",
                "failure_reasons": [
                    "레버리지 ETF 포함",
                    "위기구간 MDD 초과",
                    "한국 계좌 비용/세금/환율 미반영",
                    "paper trading 검증 부족",
                ],
                "leveraged_weight_percent": 15.0,
                "worst_window_drawdown_percent": -38.54,
                "live_capital_allowed": False,
                "recommended_action": "미충족 게이트를 먼저 해소하고 paper trading 누적 후 재평가",
                "readiness_checks": {
                    "cost_model": {"passed": False, "required": "KR 세금/환율/거래비용/괴리율 또는 추적오차 반영"},
                    "paper_trading": {"passed": False, "observed_days": 0, "min_observed_days": 90, "closed_signals": 0, "min_closed_signals": 6},
                },
            },
            "evaluation_windows": [
                {
                    "label": "2022 금리인상 하락장",
                    "result": {"total_return_percent": -12.65, "max_drawdown_percent": -16.60},
                    "benchmark": {"total_return_percent": -9.11, "max_drawdown_percent": -9.08},
                }
            ],
        },
        "live_capital_allowed": False,
    }


def test_etf_run_is_saved_as_normal_analysis_record_and_rendered_on_run_detail(tmp_path, monkeypatch):
    from tradingagents.dashboard import app as dashboard_app

    monkeypatch.setattr(
        dashboard_app,
        "get_price_chart",
        lambda ticker, trade_date, lookback_days=180: {"available": False, "reason": "portfolio_run"},
    )
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(_etf_record())

    loaded = repository.get_run(stored["run_id"])
    assert loaded is not None
    assert loaded["metadata"]["workflow"] == "portfolio_allocation"
    assert loaded["portfolio_backtest"]["results"][0]["display_name"] == "SOXL 위성 공격형"
    assert loaded["portfolio_backtest"]["live_readiness"]["status_label"] == "실거래 보류"

    client = TestClient(create_dashboard_app(tmp_path))
    response = client.get(f"/runs/{stored['run_id']}")

    assert response.status_code == 200
    assert "ETF Adaptive Core v2" in response.text
    assert "ETF 포트폴리오 백테스트" in response.text
    assert "SOXL 위성 공격형" in response.text
    assert "QLD 70 / SPY 30" in response.text
    assert "Adaptive Core v2 균형형" in response.text
    assert "단순 DCA 70/30" in response.text
    assert "+42.86%" in response.text
    assert "반도체 3배 15%" in response.text
    assert "실거래 보류" in response.text
    assert "위기구간 MDD 초과" in response.text
    assert "한국 계좌 비용/세금/환율 미반영" in response.text
    assert "paper trading 검증 부족" in response.text
    assert "미충족 게이트를 먼저 해소" in response.text
    assert "2022 금리인상 하락장" in response.text
    assert "매크로 국면 분석 에이전트" in response.text
    assert "ETF 퀀트 배분 전략 에이전트" in response.text
    assert "시스템 백테스트 검증 에이전트" in response.text
    assert "포트폴리오 매니저 최종 판단" in response.text
    assert "live_capital_allowed=false" in response.text
    assert "etf-adaptive-core-v2-agent-detail.html" not in response.text
