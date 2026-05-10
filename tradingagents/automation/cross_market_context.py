from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
import json


_RISK_LEVEL_LABELS = {
    "strong_risk_on": "강한 risk-on",
    "weak_risk_on": "약한 risk-on",
    "neutral": "중립",
    "risk_off": "risk-off",
    "strong_risk_off": "강한 risk-off",
}


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _risk_level_from_score(score: int) -> str:
    if score >= 80:
        return "strong_risk_on"
    if score >= 60:
        return "weak_risk_on"
    if score >= 40:
        return "neutral"
    if score >= 20:
        return "risk_off"
    return "strong_risk_off"


def _primary_action(regime: str) -> str:
    if regime in {"strong_risk_off", "risk_off"}:
        return "신규 진입 축소"
    if regime == "neutral":
        return "확인 후 선별 진입"
    return "조건 충족 종목 우선 검토"


def _find_row(snapshot: Mapping[str, Any], tickers: Sequence[str]) -> dict[str, Any] | None:
    wanted = {ticker.upper() for ticker in tickers}
    for row in _as_list(snapshot.get("rows")):
        if isinstance(row, dict) and str(row.get("ticker") or "").upper() in wanted:
            return row
    return None


def _average_signal_score(rows: Sequence[Any]) -> int | None:
    scores = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if row.get("signal_score") is not None:
            scores.append(_safe_float(row.get("signal_score")))
    if not scores:
        return None
    return int(round(sum(scores) / len(scores)))


def _crypto_bias_score(bias: str, rows: Sequence[Any]) -> int:
    avg = _average_signal_score(rows)
    if avg is not None:
        return avg
    normalized = bias.lower()
    if normalized == "bullish":
        return 65
    if normalized == "bearish":
        return 40
    return 50


def _contains_geopolitical_risk(*texts: object) -> bool:
    blob = " ".join(str(text or "") for text in texts).lower()
    keywords = ["전쟁", "지정학", "geopolitical", "war", "sanction", "제재", "중동", "확전"]
    return any(keyword in blob for keyword in keywords)


def _factor_summary(stock_snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    factors = []
    for factor in _as_list(stock_snapshot.get("factor_breakdown")):
        if not isinstance(factor, Mapping):
            continue
        score = _safe_int(factor.get("score"))
        tone = "positive" if score > 0 else "negative" if score < 0 else "neutral"
        factors.append(
            {
                "key": factor.get("key"),
                "label": factor.get("label") or factor.get("key"),
                "score": score,
                "tone": tone,
                "description": factor.get("description") or "",
            }
        )
    return factors


def _market_contexts(stock_snapshot: Mapping[str, Any], crypto_snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    market_score = _safe_int(stock_snapshot.get("market_score"), 50)
    qqq = _find_row(stock_snapshot, ["QQQ", "QLD", "SOXL"])
    spy = _find_row(stock_snapshot, ["SPY"])
    crypto_view = _as_dict(crypto_snapshot.get("llm_market_view"))
    crypto_rows = _as_list(crypto_snapshot.get("rows"))
    crypto_bias = str(crypto_view.get("bias") or "neutral").lower()
    crypto_score = _crypto_bias_score(crypto_bias, crypto_rows)
    return [
        {
            "id": "global",
            "title": "글로벌 공통 시장",
            "score": market_score,
            "regime": _risk_level_from_score(market_score),
            "bias": _RISK_LEVEL_LABELS.get(_risk_level_from_score(market_score), "중립"),
            "summary": stock_snapshot.get("change_summary") or "공통 시장점수 기반으로 자산군별 진입 강도를 조절합니다.",
        },
        {
            "id": "us_equity",
            "title": "미국/나스닥 선행 시장",
            "score": _safe_int((qqq or spy or {}).get("pattern_score"), market_score),
            "regime": _risk_level_from_score(market_score),
            "bias": str((qqq or spy or {}).get("action") or "관찰"),
            "summary": f"{(qqq or spy or {}).get('ticker', 'QQQ/SPY')} {(qqq or spy or {}).get('status_label', '상태 미확인')} · 다음 한국장 리스크 프리뷰에 사용",
        },
        {
            "id": "korea_equity",
            "title": "한국/코스피 적용 시장",
            "score": market_score,
            "regime": _risk_level_from_score(market_score),
            "bias": "미국장 선행 신호 반영 대기",
            "summary": "전일 미국 성장주·달러·금리·지정학 리스크를 다음 KRX/NXT 진입 강도에 연결합니다.",
        },
        {
            "id": "crypto",
            "title": "암호화폐 위험선호 시장",
            "score": crypto_score,
            "regime": _risk_level_from_score(crypto_score),
            "bias": crypto_bias,
            "summary": crypto_view.get("summary") or "코인별 1시간 전략 점수와 공통 risk-on/risk-off를 결합합니다.",
        },
    ]


def _agent_blueprint() -> list[dict[str, str]]:
    return [
        {
            "id": "global_macro_synthesizer",
            "name": "Global Macro Synthesizer",
            "role": "금리·달러·유가·전쟁·변동성 같은 시장 공통 변수를 한 번 정리해 모든 자산군에 배포",
            "output": "global_market_context",
        },
        {
            "id": "us_lead_market_agent",
            "name": "US Lead Market Agent",
            "role": "전일 나스닥/미국 성장주/급등·급락 신호를 다음 한국장 선행 변수로 변환",
            "output": "us_to_korea_implications",
        },
        {
            "id": "korea_opening_agent",
            "name": "Korea Opening Agent",
            "role": "미국장 선행 신호를 KOSPI/KOSDAQ 섹터·종목 진입 강도와 신규매수 허용 여부로 재해석",
            "output": "korea_opening_playbook",
        },
        {
            "id": "crypto_risk_appetite_agent",
            "name": "Crypto Risk Appetite Agent",
            "role": "BTC/ETH 등 24시간 위험선호 변화를 주식시장 risk-on/off의 조기 경보로 연결",
            "output": "crypto_cross_asset_signal",
        },
        {
            "id": "cross_market_context_agent",
            "name": "Cross-Market Context Agent",
            "role": "시장별 분석 결과를 서로 재사용해 충돌·공통 리스크·다음 액션을 최종 정리",
            "output": "cross_market_context",
        },
    ]


def build_cross_market_context(
    stock_snapshot: Mapping[str, Any] | None,
    crypto_snapshot: Mapping[str, Any] | None,
    *,
    hourly_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a reusable cross-market context from existing dashboard snapshots.

    This is intentionally deterministic: it does not call LLMs or brokers. Agent runs can
    consume the resulting JSON as a shared context layer before ticker-specific analysis.
    """
    stock_snapshot = stock_snapshot if isinstance(stock_snapshot, Mapping) else {}
    crypto_snapshot = crypto_snapshot if isinstance(crypto_snapshot, Mapping) else {}
    hourly_report = hourly_report if isinstance(hourly_report, Mapping) else {}
    if not stock_snapshot and not crypto_snapshot:
        return {"available": False, "reason": "market snapshots unavailable"}

    market_score = _safe_int(stock_snapshot.get("market_score"), 50)
    regime = _risk_level_from_score(market_score)
    qqq = _find_row(stock_snapshot, ["QQQ", "QLD", "SOXL"])
    crypto_view = _as_dict(crypto_snapshot.get("llm_market_view"))
    geopolitics = _contains_geopolitical_risk(
        stock_snapshot.get("change_summary"),
        crypto_view.get("summary"),
        hourly_report.get("report_purpose"),
        json.dumps(stock_snapshot.get("factor_scores") or {}, ensure_ascii=False),
    )
    nasdaq_action = str((qqq or {}).get("action") or "관찰")
    nasdaq_status = str((qqq or {}).get("status_label") or "선행 신호 확인")
    crypto_bias = str(crypto_view.get("bias") or "neutral").lower()
    links = [
        {
            "id": "nasdaq_to_kospi",
            "title": "나스닥 선행 신호 → 코스피 개장 체크",
            "source": "미국 성장주/ETF 가격표",
            "target": "KOSPI/NXT 다음 세션",
            "signal": f"{(qqq or {}).get('ticker', 'QQQ')} {nasdaq_status} · {nasdaq_action}",
            "implication": "코스피 개장 전 반도체·성장주 신규 진입 강도를 낮추고, 전일 미국장 급등주는 추격보다 눌림 확인을 우선합니다." if regime in {"risk_off", "strong_risk_off"} else "코스피 개장 전 성장주/반도체 후보의 진입구간 터치 여부를 우선 점검합니다.",
            "action": _primary_action(regime),
            "tone": "negative" if regime in {"risk_off", "strong_risk_off"} else "neutral",
        },
        {
            "id": "geopolitical_cross_asset",
            "title": "지정학 리스크 자산군 영향",
            "source": "시장 공통 뉴스·거시 리스크",
            "target": "나스닥 · 코스피 · 암호화폐",
            "signal": "전쟁/지정학 키워드 감지" if geopolitics else "명시적 전쟁/지정학 키워드 없음",
            "implication": "나스닥은 밸류에이션 압축, 코스피는 외국인 수급/환율 부담, 암호화폐는 레버리지 청산·변동성 확대 가능성을 각각 별도로 감점합니다." if geopolitics else "공통 거시 리스크는 낮지만 시장점수 변화가 크면 자산군별 진입 강도를 재검토합니다.",
            "action": "공통 리스크 게이트 적용" if geopolitics else "일반 시장점수 게이트 적용",
            "tone": "negative" if geopolitics else "neutral",
        },
        {
            "id": "crypto_to_risk_appetite",
            "title": "암호화폐 위험선호 → 성장주 심리 체크",
            "source": "BTC/ETH 1시간 전략",
            "target": "나스닥·코스피 성장주",
            "signal": f"crypto bias {crypto_bias}",
            "implication": "암호화폐 약세는 24시간 risk-off 조기 경보로 사용하고, 나스닥/코스피 성장주의 돌파 매수 신뢰도를 낮춥니다." if crypto_bias == "bearish" else "암호화폐가 안정적이면 성장주 단기 심리 확인 지표로 보조 사용합니다.",
            "action": "성장주 추격매수 보류" if crypto_bias == "bearish" else "성장주 후보 선별 유지",
            "tone": "negative" if crypto_bias == "bearish" else "neutral",
        },
    ]
    return {
        "available": True,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "signal_snapshot_plus_crypto_snapshot",
        "summary": {
            "title": "시장 연결 인텔리전스",
            "regime": regime,
            "regime_label": _RISK_LEVEL_LABELS.get(regime, regime),
            "market_score": market_score,
            "primary_action": _primary_action(regime),
            "thesis": "전일 미국장, ETF 시장점수, 암호화폐 24시간 위험선호를 다음 세션 코스피/나스닥/크립토 전략에 재사용합니다.",
        },
        "agent_blueprint": _agent_blueprint(),
        "market_contexts": _market_contexts(stock_snapshot, crypto_snapshot),
        "cross_market_links": links,
        "factor_summary": _factor_summary(stock_snapshot),
        "reuse_contract": {
            "for_kospi": "global_market_context + us_to_korea_implications + korea_opening_playbook을 주입한 뒤 종목별 TradingAgentsGraph를 실행",
            "for_nasdaq": "global_market_context + us_equity_context를 주입하고 ticker별 market/news/quant 보고서와 결합",
            "for_crypto": "global_market_context + crypto_cross_asset_signal을 1시간 전략 bias/reanalysis trigger로 사용",
        },
    }


def build_cross_market_context_from_files(base_dir: str | Path) -> dict[str, Any]:
    base = Path(base_dir)

    def load_json(name: str) -> dict[str, Any]:
        path = base / name
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    return build_cross_market_context(
        load_json("signal_snapshot.json"),
        load_json("crypto_signal_snapshot.json"),
        hourly_report=load_json("hourly_report.json"),
    )


def write_cross_market_context(base_dir: str | Path) -> dict[str, Any]:
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    context = build_cross_market_context_from_files(base)
    (base / "cross_market_context.json").write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    return context
