from __future__ import annotations

import html
import json
from pathlib import Path

REPORT_JSON = Path("artifacts/etf-strategy-reports/adaptive-core-v2-1y.json")
OUT_HTML = Path("tradingagents/dashboard/static/etf-adaptive-core-v2-1y-report.html")

ASSET_NAMES = {
    "SPY": "S&P500",
    "QQQ": "나스닥100",
    "QLD": "나스닥100 2배",
    "SOXL": "반도체 3배",
    "SCHD": "배당/퀄리티",
    "IEF": "미국중기채",
    "GLD": "금",
    "BIL": "현금성/초단기채",
}


def won(value: float) -> str:
    sign = "+" if value > 0 else "" if value == 0 else "-"
    return f"{sign}{abs(value):,.0f}원"


def pct(value: float) -> str:
    return f"{value:+.2f}%"


def weights_text(weights: dict[str, float]) -> str:
    parts = []
    for symbol, weight in weights.items():
        if abs(weight) < 1e-9:
            continue
        parts.append(f"{ASSET_NAMES.get(symbol, symbol)} {weight*100:.0f}%")
    return " / ".join(parts)


def render() -> None:
    data = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    rows = []
    for i, item in enumerate(data["results"], start=1):
        rows.append(
            f"""
            <tr>
              <td class='rank'>{i}</td>
              <td><strong>{html.escape(item['display_name'])}</strong><br><span>{html.escape(item['strategy_id'])}</span></td>
              <td>{item['final_value']:,.0f}원</td>
              <td class='pos'>{won(item['profit'])}</td>
              <td class='pos'>{pct(item['total_return_percent'])}</td>
              <td class='neg'>{pct(item['max_drawdown_percent'])}</td>
              <td>{item['contribution_count']}회</td>
              <td>{item['risk_off_count']}회</td>
              <td>{html.escape(weights_text(item['last_weights']))}</td>
            </tr>
            """
        )

    asset_rows = []
    for symbol, ret in sorted(data["asset_returns"].items(), key=lambda kv: kv[1], reverse=True):
        asset_rows.append(
            f"<tr><td>{ASSET_NAMES.get(symbol, symbol)}</td><td>{symbol}</td><td class='pos'>{pct(ret)}</td></tr>"
        )

    best = data["results"][0]
    dca = next(x for x in data["results"] if x["strategy_id"] == "simple_dca_70_spy_30_qqq")
    excess = best["total_return_percent"] - dca["total_return_percent"]
    profit_excess = best["profit"] - dca["profit"]

    html_text = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>ETF Adaptive Core v2 백테스트</title>
<style>
:root {{ color-scheme: dark; --bg:#09111f; --panel:#111b2e; --panel2:#17233a; --text:#eef4ff; --muted:#9fb0ca; --line:#283955; --pos:#67e8a5; --neg:#ff7b92; --accent:#8bbcff; --warn:#ffd166; }}
body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:linear-gradient(180deg,#08101d,#0d1424 42%,#070b13); color:var(--text); }}
.wrap {{ max-width:1180px; margin:0 auto; padding:34px 18px 70px; }}
.hero {{ border:1px solid var(--line); border-radius:24px; padding:28px; background:linear-gradient(135deg,rgba(139,188,255,.16),rgba(103,232,165,.08)),var(--panel); box-shadow:0 24px 70px rgba(0,0,0,.28); }}
h1 {{ margin:0 0 12px; font-size:34px; letter-spacing:-.04em; }}
p {{ color:var(--muted); line-height:1.7; }}
.grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:20px 0; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:18px; padding:18px; }}
.label {{ color:var(--muted); font-size:13px; }}
.value {{ margin-top:8px; font-size:23px; font-weight:800; }}
.pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }} .warn {{ color:var(--warn); }}
section {{ margin-top:24px; }}
h2 {{ font-size:22px; margin:0 0 12px; }}
table {{ width:100%; border-collapse:collapse; overflow:hidden; border-radius:16px; background:var(--panel); border:1px solid var(--line); }}
th,td {{ padding:14px 12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
th {{ color:#cfe0ff; font-size:13px; background:var(--panel2); }}
td span {{ color:var(--muted); font-size:12px; }}
.rank {{ font-weight:800; color:var(--accent); }}
.notice {{ border-left:4px solid var(--warn); background:rgba(255,209,102,.08); padding:15px 17px; border-radius:12px; }}
.trace {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }}
.trace .card strong {{ display:block; margin-bottom:7px; }}
code {{ color:#c7f9ff; }}
@media(max-width:820px) {{ .grid,.trace {{ grid-template-columns:1fr; }} h1 {{ font-size:27px; }} table {{ font-size:13px; }} th,td {{ padding:10px 8px; }} }}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <div class="label">TradingAgents ETF 시스템 트레이딩 백테스트 · 과거 적용 시뮬레이션</div>
    <h1>Adaptive Core v2 개선전략 반영 결과</h1>
    <p>기간 {data['data_range'][0]} ~ {data['data_range'][1]}, 매월 첫 거래일 100만원 납입, 월 1회 리밸런싱 기준입니다. 이번에는 임시 수기 계산이 아니라 <b>TradingAgents 전략 모듈의 ETF AllocationSpec + 규칙 기반 시스템 트레이딩 엔진</b>으로 실행 흔적을 남겼습니다.</p>
    <div class="grid">
      <div class="card"><div class="label">1위 전략</div><div class="value">{html.escape(best['display_name'])}</div></div>
      <div class="card"><div class="label">최종 평가금액</div><div class="value">{best['final_value']:,.0f}원</div></div>
      <div class="card"><div class="label">납입금 대비 수익률</div><div class="value pos">{pct(best['total_return_percent'])}</div></div>
      <div class="card"><div class="label">단순 DCA 대비 초과</div><div class="value pos">{pct(excess)} / {won(profit_excess)}</div></div>
    </div>
  </div>

  <section>
    <h2>핵심 결론</h2>
    <div class="notice">
      <b>Adaptive Core v2 균형형이 최근 1년 기준 단순 DCA를 소폭 이겼습니다.</b><br />
      단순 DCA 70/30은 +12.77%, Adaptive Core v2 균형형은 +13.14%입니다. 다만 이번 구간에서는 2개월 확인 + -10% drawdown 조건이 발동하지 않아 risk-off 전환은 0회였습니다. 성과 개선의 주된 원인은 완전 매도/재매수가 아니라 S&P500·나스닥 중심에 SCHD/GLD/BIL을 소량 섞은 risk-on 기본 비중입니다.
    </div>
  </section>

  <section>
    <h2>전략 성과 비교</h2>
    <table>
      <thead><tr><th>순위</th><th>전략</th><th>최종 평가금액</th><th>수익금</th><th>수익률</th><th>계좌 MDD</th><th>납입</th><th>Risk-Off</th><th>최종 목표비중</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </section>

  <section>
    <h2>TradingAgents 실행 흔적</h2>
    <div class="trace">
      <div class="card"><strong>Agent/Spec 계층</strong><p><code>ETFAllocationSpec</code>로 전략 의도, risk-on/risk-off 목표비중, confirmation rule을 구조화했습니다.</p></div>
      <div class="card"><strong>Program/System 계층</strong><p><code>run_etf_allocation_backtest</code>가 월납입, 리밸런싱, 신호 판정, equity curve, MDD를 deterministic하게 계산했습니다.</p></div>
      <div class="card"><strong>Live 자금 차단</strong><p><code>live_capital_allowed=false</code>. 이번 결과는 paper/검증용이며 실거래 주문 로직은 연결하지 않았습니다.</p></div>
    </div>
  </section>

  <section>
    <h2>기초 ETF 수익률</h2>
    <table><thead><tr><th>자산</th><th>프록시</th><th>기간 수익률</th></tr></thead><tbody>{''.join(asset_rows)}</tbody></table>
  </section>

  <section>
    <h2>주의사항</h2>
    <p>미래 수익 보장이 아닙니다. 과거 조정종가 기반 시뮬레이션이며 비용, 세금, 슬리피지, 환율, 한국 상장 ETF 괴리율/환헤지 차이는 아직 미반영입니다. 월 적립 계좌 기준 MDD는 신규 납입금 때문에 전략 자체 낙폭보다 작게 보일 수 있습니다. 최근 1년 표본만으로 전략 우위를 확정하면 과최적화 위험이 있습니다.</p>
  </section>
</div>
</body>
</html>
"""
    OUT_HTML.write_text(html_text, encoding="utf-8")
    print(OUT_HTML)


if __name__ == "__main__":
    render()
