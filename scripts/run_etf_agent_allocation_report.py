from __future__ import annotations

import json
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from tradingagents.dashboard.batch import build_batch_config
from tradingagents.llm_clients.openai_client import OpenAIClient

BACKTEST_JSON = Path("artifacts/etf-strategy-reports/adaptive-core-v2-1y.json")
OUT_JSON = Path("artifacts/etf-strategy-reports/adaptive-core-v2-agent-run.json")

AGENTS = [
    {
        "key": "macro_regime_analyst_report",
        "name": "매크로 국면 분석 에이전트",
        "role": "전쟁, 유가, 금리, 달러, 금 가격, 주식 추세를 ETF 장기 운용 관점에서 분석한다.",
    },
    {
        "key": "etf_quant_allocation_report",
        "name": "ETF 퀀트 배분 전략 에이전트",
        "role": "단순 DCA 대비 개선 가능한 목표비중, risk-on/risk-off 조건, 리밸런싱 규칙을 제안한다.",
    },
    {
        "key": "system_backtest_auditor_report",
        "name": "시스템 백테스트 검증 에이전트",
        "role": "프로그램 백테스트 결과를 감사하고, 성과가 진짜 전략 우위인지/표본 부족인지 판단한다.",
    },
    {
        "key": "risk_control_analyst_report",
        "name": "리스크 제어 에이전트",
        "role": "MDD, 과최적화, 현금대기, 세금/환율/슬리피지, 한국 계좌 적용 리스크를 점검한다.",
    },
    {
        "key": "portfolio_manager_decision",
        "name": "포트폴리오 매니저 최종 판단",
        "role": "위 에이전트 의견과 시스템 백테스트를 종합해 실제 다음 개발/검증 액션을 결정한다.",
    },
]


def _build_llm():
    config = build_batch_config("artifacts/dashboard-pilot-20260430", use_hermes_codex_auth=True)
    runtime = {}
    base_url = None
    if config.get("openai_use_hermes_codex_auth"):
        from tradingagents.llm_clients.openai_auth import build_openai_runtime_config
        runtime_config = build_openai_runtime_config(config)
        runtime = dict(runtime_config.get("auth_kwargs") or {})
        base_url = runtime_config.get("base_url")
    return OpenAIClient(
        model=config.get("deep_think_llm", "gpt-5.5"),
        provider=config.get("llm_provider", "openai"),
        base_url=base_url,
        **runtime,
        reasoning_effort="medium",
        timeout=240,
        max_retries=2,
    ).get_llm()


def _compact_backtest(data: dict) -> str:
    lines = [
        f"기간: {data['data_range'][0]} ~ {data['data_range'][1]}",
        "월납입: 매월 첫 거래일 100만원, 총 납입 1,300만원, 월 1회 리밸런싱",
        "비용/세금/환율/슬리피지: 미반영",
        "기초 ETF 수익률: " + ", ".join(f"{k} {v:+.2f}%" for k, v in data["asset_returns"].items()),
        "전략 결과:",
    ]
    for i, r in enumerate(data["results"], 1):
        lines.append(
            f"{i}. {r['display_name']}({r['strategy_id']}): 최종 {r['final_value']:,.0f}원, "
            f"수익 {r['profit']:+,.0f}원, 수익률 {r['total_return_percent']:+.2f}%, "
            f"MDD {r['max_drawdown_percent']:+.2f}%, Risk-Off {r['risk_off_count']}회, "
            f"최종비중 {r['last_weights']}"
        )
    return "\n".join(lines)


def main() -> None:
    backtest = json.loads(BACKTEST_JSON.read_text(encoding="utf-8"))
    context = _compact_backtest(backtest)
    llm = _build_llm()
    reports = {}
    execution_log = []
    prior_reports = ""
    for agent in AGENTS:
        started = time.time()
        system = f"""당신은 TradingAgents의 {agent['name']}입니다.
역할: {agent['role']}
반드시 한국어로 작성하세요.
사용자에게 보이는 종목 상세보기 수준의 상세 리포트가 필요합니다.
ETF라도 에이전트가 판단하고, 프로그램 백테스트가 검증하는 구조를 지키세요.
미래 수익 보장 표현 금지. 과거 시뮬레이션, 표본 부족, 과최적화, 비용/세금/환율 미반영을 명확히 언급하세요.
단순 DCA는 고정 벤치마크입니다.
내부 모델명/API/토큰/시크릿은 절대 언급하지 마세요.
"""
        human = f"""아래 ETF 시스템트레이딩 백테스트 결과를 바탕으로 {agent['name']} 리포트를 작성하세요.

[백테스트/프로그램 결과]
{context}

[앞선 에이전트 리포트 요약]
{prior_reports[-6000:] if prior_reports else '없음'}

요구 형식:
# {agent['name']}
## 핵심 판단
## 근거
## 단순 DCA 대비 평가
## 시스템 트레이딩 액션/규칙
## 리스크와 한계
## 다음 검증 액션
"""
        msg = llm.invoke([SystemMessage(content=system), HumanMessage(content=human)])
        content = str(msg.content or "").strip()
        reports[agent["key"]] = {
            "agent_name": agent["name"],
            "role": agent["role"],
            "report": content,
            "elapsed_seconds": round(time.time() - started, 2),
        }
        execution_log.append({
            "agent": agent["name"],
            "report_key": agent["key"],
            "elapsed_seconds": reports[agent["key"]]["elapsed_seconds"],
            "chars": len(content),
        })
        prior_reports += f"\n\n[{agent['name']}]\n{content}\n"
        print(f"completed {agent['name']} chars={len(content)} elapsed={reports[agent['key']]['elapsed_seconds']}")
    output = {
        "run_id": "etf-adaptive-core-v2-2026-05-01-agent-run",
        "title": "ETF Adaptive Core v2 Agent Run",
        "data_range": backtest["data_range"],
        "backtest": backtest,
        "reports": reports,
        "execution_log": execution_log,
        "live_capital_allowed": False,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUT_JSON)


if __name__ == "__main__":
    main()
