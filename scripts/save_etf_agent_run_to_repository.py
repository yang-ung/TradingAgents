from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tradingagents.dashboard.extract import make_snippet, utc_now_iso
from tradingagents.dashboard.storage import AnalysisRepository

AGENT_JSON = Path("artifacts/etf-strategy-reports/adaptive-core-v2-agent-run.json")

REPORT_KEY_MAP = {
    "macro_regime_analyst_report": "market_report",
    "etf_quant_allocation_report": "quant_strategy_report",
    "system_backtest_auditor_report": "news_report",
    "risk_control_analyst_report": "fundamentals_report",
    "portfolio_manager_decision": "portfolio_manager_decision",
}


def _report_text(entry: object) -> str:
    if isinstance(entry, dict):
        agent_name = str(entry.get("agent_name") or "").strip()
        role = str(entry.get("role") or "").strip()
        report = str(entry.get("report") or "").strip()
        prefix = "\n".join(part for part in [agent_name, role] if part)
        return f"{prefix}\n\n{report}".strip()
    return str(entry or "").strip()


def _build_reports(data: dict[str, Any]) -> dict[str, str]:
    raw_reports = data.get("reports") if isinstance(data.get("reports"), dict) else {}
    reports = {
        "market_report": "",
        "quant_strategy_report": "",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
        "investment_plan": "",
        "trader_investment_decision": "**Action**: 관망/월납입 유지",
        "final_trade_decision": "",
        "portfolio_manager_decision": "",
    }
    for source_key, target_key in REPORT_KEY_MAP.items():
        reports[target_key] = _report_text(raw_reports.get(source_key))
    portfolio_decision = reports.get("portfolio_manager_decision") or ""
    reports["final_trade_decision"] = portfolio_decision or "**Rating**: HOLD\n**Executive Summary**: ETF 포트폴리오 전략은 월납입 유지, 실거래 연결 금지."
    reports["investment_plan"] = reports.get("quant_strategy_report") or "**Recommendation**: Adaptive Core v2 균형형 유지"
    if not reports.get("sentiment_report"):
        reports["sentiment_report"] = "ETF 포트폴리오 전략에서는 개별 종목 심리 대신 위험자산 선호/회피 국면을 매크로·퀀트 리포트에서 통합 판단했습니다."
    return reports


def _portfolio_backtest(data: dict[str, Any]) -> dict[str, Any]:
    backtest = data.get("backtest") if isinstance(data.get("backtest"), dict) else {}
    return {
        "available": True,
        "data_range": backtest.get("data_range") or data.get("data_range") or [],
        "benchmark_strategy_id": "simple_dca_70_spy_30_qqq",
        "benchmark_name": "단순 DCA 70/30",
        "asset_returns": backtest.get("asset_returns") or {},
        "long_term_asset_returns": backtest.get("long_term_asset_returns") or {},
        "results": backtest.get("results") or [],
        "evaluation_windows": backtest.get("evaluation_windows") or [],
        "live_readiness": backtest.get("live_readiness") or {},
        "assumptions": [
            "최근 1년 조정종가 기반 월납입 포트폴리오 시뮬레이션",
            "2012년 이후 장기 구간 및 2018/2020/2022 위기구간 별도 점검",
            "월 100만원 납입, 월 1회 리밸런싱 기준",
            "비용·세금·슬리피지·환율·한국 상장 ETF 괴리율/환헤지 차이 미반영",
            "미래 수익 보장 아님, 표본 부족 및 과최적화 위험 존재",
            "live_capital_allowed=false",
        ],
    }


def build_record(data: dict[str, Any]) -> dict[str, Any]:
    reports = _build_reports(data)
    backtest = _portfolio_backtest(data)
    results = backtest.get("results") if isinstance(backtest.get("results"), list) else []
    best = results[0] if results and isinstance(results[0], dict) else {}
    data_range = backtest.get("data_range") if isinstance(backtest.get("data_range"), list) else []
    run_id = str(data.get("run_id") or "etf-adaptive-core-v2-2026-05-01-agent-run")
    trade_date = data_range[1] if len(data_range) >= 2 else "2026-05-01"
    generated_at = str(data.get("generated_at") or utc_now_iso())
    benchmark = next((item for item in results if str(item.get("strategy_id")) == "simple_dca_70_spy_30_qqq"), {})
    excess = float(best.get("total_return_percent") or 0) - float(benchmark.get("total_return_percent") or 0)
    live_readiness = backtest.get("live_readiness") if isinstance(backtest.get("live_readiness"), dict) else {}
    readiness_label = str(live_readiness.get("status_label") or "실거래 보류")
    reasons = live_readiness.get("failure_reasons") if isinstance(live_readiness.get("failure_reasons"), list) else []
    reason_text = ", ".join(str(reason) for reason in reasons[:3]) or "장기·위기구간 추가 검증 필요"
    decision_summary = (
        f"최상위 후보 {best.get('display_name') or best.get('strategy_id')} 수익률 {float(best.get('total_return_percent') or 0):+.2f}%, "
        f"단순 DCA 대비 {excess:+.2f}%p. {readiness_label}: {reason_text}. live_capital_allowed=false."
    )
    return {
        "run_id": run_id,
        "ticker": "ETF-ADAPTIVE-CORE-V2",
        "trade_date": trade_date,
        "generated_at": generated_at,
        "rating": "HOLD",
        "trader_action": "관망/월납입 유지",
        "research_recommendation": "Adaptive Core v2 균형형 유지",
        "decision_summary": decision_summary,
        "investment_thesis": "ETF 장기 운용은 완전 매도/재매수보다 신규자금·현금비중·리밸런싱 규칙으로 대응하는 편이 현실적입니다.",
        "price_target": None,
        "time_horizon": "장기 월납입",
        "snippets": {
            "market": make_snippet(reports.get("market_report", "")),
            "sentiment": make_snippet(reports.get("sentiment_report", "")),
            "news": make_snippet(reports.get("news_report", "")),
            "fundamentals": make_snippet(reports.get("fundamentals_report", "")),
        },
        "reports": reports,
        "report_lengths": {key: len(value or "") for key, value in reports.items()},
        "raw_log_path": "",
        "structured_path": "",
        "metadata": {
            "market": "ETF",
            "exchange": "Portfolio",
            "display_name": "ETF Adaptive Core v2",
            "company_name": "ETF Adaptive Core v2",
            "workflow": "portfolio_allocation",
            "source_agent_run": str(AGENT_JSON),
            "live_capital_allowed": False,
        },
        "raw_state": {
            "company_of_interest": "ETF-ADAPTIVE-CORE-V2",
            "trade_date": trade_date,
            "portfolio_workflow": "etf_allocation",
            "execution_log": data.get("execution_log") or [],
        },
        "portfolio_backtest": backtest,
        "live_capital_allowed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Save ETF agent/backtest JSON as a normal dashboard run record.")
    parser.add_argument("--agent-json", type=Path, default=AGENT_JSON)
    parser.add_argument("--dashboard-dir", type=Path, default=Path("artifacts/dashboard"))
    args = parser.parse_args()

    data = json.loads(args.agent_json.read_text(encoding="utf-8"))
    record = build_record(data)
    stored = AnalysisRepository(args.dashboard_dir).save(record)
    print(stored["run_id"])
    print(AnalysisRepository(args.dashboard_dir).payload_path(stored["run_id"]))


if __name__ == "__main__":
    main()
