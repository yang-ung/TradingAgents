from __future__ import annotations

import html
import json
import re
from pathlib import Path

AGENT_JSON = Path("artifacts/etf-strategy-reports/adaptive-core-v2-agent-run.json")
OUT_HTML = Path("tradingagents/dashboard/static/etf-adaptive-core-v2-agent-detail.html")

ASSET_NAMES = {"SPY":"S&P500","QQQ":"나스닥100","SCHD":"배당/퀄리티","IEF":"미국중기채","GLD":"금","BIL":"현금성/초단기채"}
REPORT_ORDER = [
    "macro_regime_analyst_report",
    "etf_quant_allocation_report",
    "system_backtest_auditor_report",
    "risk_control_analyst_report",
    "portfolio_manager_decision",
]


def won(v: float) -> str:
    sign = "+" if v > 0 else "" if v == 0 else "-"
    return f"{sign}{abs(v):,.0f}원"


def pct(v: float) -> str:
    return f"{v:+.2f}%"


def weights_text(weights: dict[str, float]) -> str:
    return " / ".join(f"{ASSET_NAMES.get(k,k)} {v*100:.0f}%" for k, v in weights.items() if abs(v) > 1e-9)


def render_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"^### (.+)$", r"<h4>\1</h4>", escaped, flags=re.M)
    escaped = re.sub(r"^## (.+)$", r"<h3>\1</h3>", escaped, flags=re.M)
    escaped = re.sub(r"^# (.+)$", r"<h2>\1</h2>", escaped, flags=re.M)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def main() -> None:
    data = json.loads(AGENT_JSON.read_text(encoding="utf-8"))
    backtest = data["backtest"]
    results = backtest["results"]
    best = results[0]
    dca = next(r for r in results if r["strategy_id"] == "simple_dca_70_spy_30_qqq")
    excess = best["total_return_percent"] - dca["total_return_percent"]
    profit_excess = best["profit"] - dca["profit"]

    perf_rows = []
    for i, r in enumerate(results, 1):
        perf_rows.append(f"""
        <tr>
          <td class="rank">{i}</td><td><strong>{html.escape(r['display_name'])}</strong><br><span>{html.escape(r['strategy_id'])}</span></td>
          <td>{r['final_value']:,.0f}원</td><td class="pos">{won(r['profit'])}</td><td class="pos">{pct(r['total_return_percent'])}</td>
          <td class="neg">{pct(r['max_drawdown_percent'])}</td><td>{r['risk_off_count']}회</td><td>{html.escape(weights_text(r['last_weights']))}</td>
        </tr>
        """)

    toc = []
    report_cards = []
    for idx, key in enumerate(REPORT_ORDER, 1):
        r = data["reports"][key]
        anchor = f"agent-{idx}"
        toc.append(f"<a href='#{anchor}'>{html.escape(r['agent_name'])}</a>")
        report_cards.append(f"""
        <section class="agent-card" id="{anchor}">
          <div class="agent-head">
            <div><div class="eyebrow">Agent {idx}</div><h2>{html.escape(r['agent_name'])}</h2><p>{html.escape(r['role'])}</p></div>
            <div class="badge">실행 {r['elapsed_seconds']:.1f}s · {len(r['report']):,}자</div>
          </div>
          <article class="markdown-body">{render_markdown(r['report'])}</article>
        </section>
        """)

    log_rows = []
    for log in data["execution_log"]:
        log_rows.append(f"<tr><td>{html.escape(log['agent'])}</td><td>{html.escape(log['report_key'])}</td><td>{log['elapsed_seconds']:.1f}s</td><td>{log['chars']:,}자</td></tr>")

    asset_rows = []
    for symbol, ret in sorted(backtest["asset_returns"].items(), key=lambda kv: kv[1], reverse=True):
        asset_rows.append(f"<tr><td>{ASSET_NAMES.get(symbol,symbol)}</td><td>{symbol}</td><td class='pos'>{pct(ret)}</td></tr>")

    html_text = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ETF Adaptive Core v2 에이전트 상세보기</title>
<style>
:root{{--bg:#070d18;--panel:#101a2d;--panel2:#16243c;--line:#2a3b58;--text:#edf5ff;--muted:#a7b5ce;--pos:#6df0ad;--neg:#ff7d95;--accent:#91c7ff;--warn:#ffd166}}
body{{margin:0;background:linear-gradient(180deg,#07101e,#0b1220 40%,#050912);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.65}}
.wrap{{max-width:1220px;margin:0 auto;padding:28px 18px 80px}}
a{{color:#b9d8ff;text-decoration:none}} .hero,.panel,.agent-card{{background:rgba(16,26,45,.94);border:1px solid var(--line);border-radius:24px;box-shadow:0 24px 80px rgba(0,0,0,.28)}}
.hero{{padding:30px;background:linear-gradient(135deg,rgba(145,199,255,.18),rgba(109,240,173,.08)),var(--panel)}}
.eyebrow,.label{{color:var(--muted);font-size:13px;font-weight:700;letter-spacing:.02em}}h1{{font-size:36px;margin:8px 0 12px;letter-spacing:-.04em}}h2{{font-size:24px;margin:0 0 8px}}h3{{font-size:19px;color:#dbeaff;margin-top:24px}}h4{{font-size:16px;color:#cfe1ff;margin-top:18px}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:20px 0}}.stat{{background:rgba(10,17,30,.7);border:1px solid var(--line);border-radius:18px;padding:17px}}.value{{font-size:23px;font-weight:850;margin-top:7px}}.pos{{color:var(--pos)}}.neg{{color:var(--neg)}}.warn{{color:var(--warn)}}
.nav{{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}}.nav a{{padding:9px 12px;border:1px solid var(--line);border-radius:999px;background:#0c1526}}
.panel{{padding:22px;margin-top:22px}}table{{width:100%;border-collapse:collapse;border:1px solid var(--line);border-radius:16px;overflow:hidden;background:#0c1526}}th,td{{padding:13px 11px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}}th{{font-size:13px;color:#d6e7ff;background:var(--panel2)}}td span{{color:var(--muted);font-size:12px}}.rank{{color:var(--accent);font-weight:850}}
.agent-card{{padding:0;margin-top:24px;overflow:hidden}}.agent-head{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;padding:22px;border-bottom:1px solid var(--line);background:rgba(22,36,60,.7)}}.agent-head p{{margin:0;color:var(--muted)}}.badge{{white-space:nowrap;color:#c6dcff;background:#0b1424;border:1px solid var(--line);border-radius:999px;padding:8px 12px;font-size:13px}}
.markdown-body{{padding:8px 24px 28px;white-space:pre-wrap;overflow-wrap:anywhere}}.markdown-body code{{color:#c7f9ff;background:#0b1424;border:1px solid #20314b;border-radius:6px;padding:1px 5px}}.markdown-body strong{{color:#ffffff}}
.notice{{border-left:4px solid var(--warn);background:rgba(255,209,102,.08);padding:15px 17px;border-radius:12px;color:#f8edc8}}
@media(max-width:860px){{.grid{{grid-template-columns:1fr}}.agent-head{{display:block}}.badge{{display:inline-block;margin-top:12px}}h1{{font-size:28px}}table{{font-size:13px}}th,td{{padding:9px 7px}}}}
</style></head><body><div class="wrap">
<header class="hero">
  <div class="eyebrow">TradingAgents ETF Agent Run · 종목 상세보기급 리포트</div>
  <h1>ETF Adaptive Core v2 에이전트 상세보기</h1>
  <p>이번 페이지는 단순 백테스트 표가 아니라 실제 LLM 에이전트 5개가 순차 실행되어 생성한 리포트와, 프로그램 백테스트 결과를 함께 보여줍니다. ETF 대상이라 개별 종목의 매수/손절가 대신 자산배분 비중·risk-on/risk-off·월납입 리밸런싱을 시스템 트레이딩 계약으로 사용했습니다.</p>
  <div class="grid">
    <div class="stat"><div class="label">최종 판단 전략</div><div class="value">{html.escape(best['display_name'])}</div></div>
    <div class="stat"><div class="label">최종 평가금액</div><div class="value">{best['final_value']:,.0f}원</div></div>
    <div class="stat"><div class="label">수익률</div><div class="value pos">{pct(best['total_return_percent'])}</div></div>
    <div class="stat"><div class="label">DCA 대비 초과</div><div class="value pos">{pct(excess)} / {won(profit_excess)}</div></div>
  </div>
  <div class="nav">{''.join(toc)}</div>
</header>
<section class="panel"><h2>시스템 백테스트 결과</h2><table><thead><tr><th>순위</th><th>전략</th><th>최종금액</th><th>수익금</th><th>수익률</th><th>MDD</th><th>Risk-Off</th><th>최종 목표비중</th></tr></thead><tbody>{''.join(perf_rows)}</tbody></table></section>
<section class="panel"><h2>에이전트 실행 로그</h2><table><thead><tr><th>에이전트</th><th>저장 키</th><th>실행시간</th><th>리포트 길이</th></tr></thead><tbody>{''.join(log_rows)}</tbody></table></section>
{''.join(report_cards)}
<section class="panel"><h2>기초 ETF 수익률</h2><table><thead><tr><th>자산</th><th>프록시</th><th>기간 수익률</th></tr></thead><tbody>{''.join(asset_rows)}</tbody></table></section>
<section class="panel"><h2>주의사항</h2><div class="notice">미래 수익 보장이 아닙니다. 과거 조정종가 기반 시뮬레이션이며 비용, 세금, 슬리피지, 환율, 한국 상장 ETF 괴리율/환헤지 차이는 아직 미반영입니다. 최근 1년 표본만으로 우위를 확정하면 과최적화 위험이 있습니다. 실거래 연결은 차단되어 있으며 live_capital_allowed=false입니다.</div></section>
</div></body></html>"""
    OUT_HTML.write_text(html_text, encoding="utf-8")
    print(OUT_HTML)


if __name__ == "__main__":
    main()
