# Agent / Program 책임 경계 설계

TradingAgents는 투자 판단을 전부 Agent에게 맡기지 않는다. Agent는 시장의 의미를 해석하고 매매 아이디어를 제안한다. 프로그램은 그 제안을 검증 가능한 규칙으로 실행·검증·기록한다.

핵심 문장:

```text
프로그램은 이상 징후를 감지하고, Agent는 그 의미를 해석한다.
```

실전 투입 전 원칙:

```text
Agent는 매매 계획을 제안하고, 프로그램은 그 계획을 동일 규칙으로 검증한다.
검증 통과 전에는 실거래로 연결하지 않는다.
```

---

## 1. 설계 목표

1. **반복 가능한 투자 프로세스**
   - 같은 `StrategySpec`은 같은 가격 데이터와 같은 비용 가정에서 항상 같은 결과를 내야 한다.
   - Agent가 매번 다른 표현으로 답하더라도 프로그램 검증 결과는 흔들리지 않아야 한다.

2. **Agent 비용과 판단 변동성 감소**
   - 매일 가격이 바뀔 때마다 전체 Agent 분석을 다시 부르지 않는다.
   - 단순 체결 가능성, 손절/익절, 유효기간 만료, risk gate 판정은 프로그램이 처리한다.

3. **실전 투입 전 fail-closed**
   - `strategy_spec`이 없거나, 검증 표본이 부족하거나, 비용 반영 성과가 부족하면 후보에서 제외한다.
   - Gate A를 통과해도 `paper trading 후보`까지만 허용하고 `live_capital_allowed=False`를 유지한다.

4. **사용자에게는 한국어 투자 언어로 표시**
   - 내부 key나 agent report field를 노출하지 않는다.
   - 사용자는 `진입 구간`, `익절`, `손절`, `검증 실패 사유`, `재분석 필요`를 본다.

---

## 2. 한 줄 책임 분리

| 영역 | Agent 책임 | 프로그램 책임 |
|---|---|---|
| 시장 이해 | 뉴스/펀더멘털/차트/수급의 의미 해석 | 데이터 수집, 결측/범위/통화 검증 |
| 전략 제안 | 전략 유형, 진입/익절/손절, 회피 조건 제안 | `StrategySpec` schema 검증, 방향성 규칙 검증 |
| 매일 운영 | 재분석이 필요한 사건의 의미 해석 | 가격 touch, 손절/익절, 유효기간, 거래 비용, PnL 계산 |
| 검증 | 실패 원인 해석, 전략 개선 아이디어 제안 | backtest/replay/walk-forward/paper ledger/gate 판정 |
| 실전 연결 | 왜 후보인지 설명 | 실전 차단, position/risk limit, audit log, immutability |

---

## 3. 전체 아키텍처

```text
[가격/뉴스/재무/공시 데이터]
        |
        v
[Program: data adapter]
- ticker/date/currency 정규화
- OHLCV/뉴스/재무 데이터 수집
- 결측, 통화, 범위 검증
        |
        v
[Agent Analysis]
- Market / News / Fundamentals / Sentiment / Quant Strategy
- Trader / Research Manager / Portfolio Manager
- 방향성, 진입 타이밍, 시장 공통 리스크 해석
- StrategySpec JSON 제안
        |
        v
[Program: contract boundary]
- StrategySpec parse/normalize/validate
- stop_loss < entry <= take_profit 검증
- invalid spec fail-closed
- record에 validated strategy_spec 저장
        |
        v
[Program: deterministic engine]
- signal evaluation
- reanalysis trigger detection
- strategy replay
- pre-live validation
- paper trading fill/PnL update
        |
        v
[Dashboard/API]
- 한국어 view model
- 검증 실패 / paper trading 후보 / 재분석 필요 표시
- live_capital_allowed=False 유지
        |
        v
[Optional future Gate C]
- Gate A/B 통과 후 제한적 live trial만 가능
- 별도 안전장치/리뷰 전까지 비활성
```

---

## 4. 단계별 책임 경계

### 4.1 데이터 수집과 정규화

**프로그램이 해야 한다.**

- ticker, market, exchange, currency 정규화
- OHLCV, 거래량, benchmark, 뉴스, 재무 데이터 수집
- 가격 데이터 결측/비정상 값 제거
- `.KS`/`.KQ` 종목 KRW 처리
- 최신 거래일 산출
- 동일 입력에 대한 cache/storage 관리

**Agent가 하면 안 된다.**

- 가격 데이터를 기억이나 추측으로 채우기
- 없는 OHLC 값을 만들어내기
- 통화 변환이나 주가 단위를 임의로 보정하기

### 4.2 의미 해석과 전략 제안

**Agent가 해야 한다.**

- 뉴스가 종목 특화인지 시장 공통 리스크인지 분리
- 펀더멘털 개선/악화의 투자 의미 해석
- 차트/지표가 지금 진입에 유리한지 해석
- 방향성 점수와 진입 타이밍을 분리해서 판단
- 전략 유형 제안:
  - 눌림목
  - 돌파
  - 추세추종
  - 평균회귀
  - 변동성 돌파
  - 거래 회피
- 증거가 충분하면 `StrategySpec` JSON 제안
- 증거가 부족하면 `StrategySpec`을 생략하고 이유 명시

**프로그램이 해야 한다.**

- Agent text에서 `StrategySpec` 추출
- 안전한 alias만 normalize:
  - `basis` string → list
  - `avoid_conditions` string → list
  - `reason_ko` → `reason`
  - `look_back_days` → `lookback_days`
- schema와 방향성 검증
- invalid JSON/spec은 저장하지 않고 fail-closed

### 4.3 StrategySpec 계약

Agent와 프로그램 사이의 핵심 계약은 `StrategySpec`이다.

최소 필드:

```json
{
  "strategy_id": "005930.KS_trend_pullback_20260430",
  "ticker": "005930.KS",
  "trade_date": "2026-04-30",
  "strategy_type": "price_timing_long",
  "execution_mode": "programmatic_rule_engine",
  "entry": {"type": "price_zone", "low": 217000, "high": 222000},
  "take_profit": {"type": "fixed_price", "price": 234000},
  "stop_loss": {"type": "fixed_price", "price": 214000},
  "currency": "KRW",
  "basis": ["10일 이평 지지", "VWMA 지지", "MACD 양호"],
  "avoid_conditions": ["214000원 아래 종가 마감", "거래량 없는 돌파 실패"],
  "confidence": 0.74,
  "valid_until": "2026-05-08",
  "reanalysis_triggers": [
    {"type": "price_below", "level": 214000, "reason": "손절가 이탈"},
    {"type": "price_above", "level": 234000, "reason": "목표가 도달 후 재평가"},
    {"type": "time_expired", "date": "2026-05-08", "reason": "전략 유효기간 만료"}
  ]
}
```

프로그램 검증 규칙:

```text
entry.low > 0
entry.high > 0
entry.low <= entry.high
stop_loss.price < entry.low
take_profit.price > entry.high
valid_until은 YYYY-MM-DD
trigger level/multiplier/date는 finite/positive/valid
```

검증 실패 시:

```text
strategy_spec = 없음
backtest/pre-live = 검증 불가 또는 실패
paper_trading_candidate = False
live_capital_allowed = False
```

### 4.4 신호와 체결 판단

**프로그램이 해야 한다.**

- 가격이 진입 구간을 touch했는지 판단
- 진입가 가정:
  - long entry range touch 시 보수적으로 `entry_high` 체결
- 익절/손절 touch 판단
- 같은 일봉에서 익절/손절 모두 touch 시 손절 우선
- 수수료/슬리피지 차감
- open/closed 상태 보존
- closed trade immutability 유지
- 손절/무효화 후 자동 재진입 금지

**Agent가 해야 한다.**

- 손절이 발생한 이유 해석
- 시장 공통 리스크 변화의 의미 해석
- 새로운 분석이 필요한지 판단
- 새 `StrategySpec`을 제안하거나 거래 회피를 권고

### 4.5 재분석 트리거

프로그램은 trigger를 감지한다. Agent는 trigger의 의미를 해석한다.

| Trigger | 감지 주체 | 해석 주체 | 기본 동작 |
|---|---|---|---|
| 손절가 이탈 | 프로그램 | Agent | 전략 만료, 자동 재진입 금지 |
| 익절가 도달 | 프로그램 | Agent | 청산/부분익절 기록, 다음 전략 재평가 후보 |
| 유효기간 만료 | 프로그램 | Agent | 재분석 후보 등록 |
| 거래량 급증 | 프로그램 | Agent | 돌파/분배 여부 해석 요청 |
| 이평선 cross | 프로그램 | Agent | 추세 변화 해석 요청 |
| 시장 공통 리스크 급등 | 프로그램 점수/gate | Agent | 신규 매수 보류 또는 비중 축소 해석 |

중요:

```text
트리거 감지는 곧 매수/매도 결정이 아니다.
트리거 감지는 Agent 재분석 요청 또는 deterministic 상태 전환이다.
```

### 4.6 검증과 gate

**프로그램이 해야 한다.**

- backtest/replay 계산
- walk-forward/out-of-sample split
- 비용/슬리피지/세금 모델 적용
- 승률, profit factor, 최대낙폭, 총수익률 계산
- 시장 공통 리스크 fail-closed
- Gate A/B/C 상태 관리
- `live_capital_allowed=False` 강제

**Agent가 해야 한다.**

- 검증 실패 원인 해석
- 전략 조건이 너무 좁은지/넓은지 설명
- 어떤 시장 regime에서 유리/불리한지 설명
- 다음 개선 후보 제안

---

## 5. 상태 전이 모델

```text
analysis_created
  - Agent report와 validated strategy_spec 저장
  - 아직 후보 아님

pre_live_failed
  - Gate A 실패
  - paper_trading_candidate=False
  - live_capital_allowed=False

paper_candidate
  - Gate A 통과
  - paper_trading_candidate=True
  - live_capital_allowed=False

paper_trading
  - 후보 signal이 ledger에 저장됨
  - 실제 주문 없음

paper_not_filled
  - 관찰 기간 동안 진입 구간 미체결

paper_open
  - 진입 체결 가정 완료
  - exit 전까지 매일 OHLC 업데이트

paper_closed
  - 익절/손절/만료로 종료
  - closed trade immutable

reanalysis_required
  - 손절, 무효화, 유효기간 만료, 리스크 급등
  - 새 Agent 분석 전 자동 재진입 금지

live_candidate
  - Gate A/B 장기 통과 후만 가능
  - 현재 기본 비활성

live_capital_allowed
  - 별도 live trading 설계/리뷰/명시 요청 전까지 항상 False
```

---

## 6. 현재 코드 매핑

| 설계 요소 | 현재 파일 |
|---|---|
| Agent StrategySpec 제안 | `tradingagents/agents/analysts/quant_strategy_analyst.py` |
| StrategySpec schema/validation | `tradingagents/strategies/schema.py` |
| signal/reanalysis engine | `tradingagents/strategies/engine.py` |
| dashboard record materialization | `tradingagents/dashboard/extract.py` |
| strategy backtest/replay | `tradingagents/dashboard/backtest.py` |
| 판단 점수판 | `tradingagents/dashboard/scorecard.py` |
| pre-live Gate A | `tradingagents/validation/pre_live.py` |
| return/cost metrics | `tradingagents/validation/performance.py` |
| paper trading ledger | `tradingagents/validation/paper_trading.py` |
| dashboard/API | `tradingagents/dashboard/app.py` |
| validation plan | `docs/trading_validation_plan.md` |

---

## 7. API/모듈 책임 원칙

### Agent-facing output

Agent report는 설명 자료다. 직접 실행하지 않는다.

허용:

```text
quant_strategy_report markdown
StrategySpec JSON block
positive/negative evidence
market-common risk explanation
```

금지:

```text
Agent text를 그대로 주문 명령으로 사용
Agent가 계산한 PnL을 검증 없이 표시
Agent가 live_capital_allowed를 True로 설정
```

### Program-facing contract

프로그램은 `StrategySpec`과 OHLCV만 신뢰한다.

허용:

```text
parse_strategy_spec(validated JSON)
evaluate_signal(spec, point)
evaluate_reanalysis_triggers(spec, points)
build_strategy_backtest(record, chart)
build_pre_live_validation_report(backtest, scorecard)
PaperTradingLedger.update_signal_with_ohlc(...)
```

금지:

```text
free-form markdown에서 바로 live action 실행
유효하지 않은 가격 방향성 보정 후 강제 실행
closed paper trade 재개
손절 후 같은 StrategySpec 자동 재진입
```

---

## 8. 실패 처리 원칙

모든 애매한 상황은 보수적으로 처리한다.

| 상황 | 처리 |
|---|---|
| `StrategySpec` 없음 | 검증 불가, 후보 제외 |
| 가격 방향성 오류 | spec reject, 후보 제외 |
| 표본 부족 | 검증 실패 |
| 비용 반영 후 수익률 <= 0 | 검증 실패 |
| 시장 공통 리스크 매우 부정 | 신규 매수 fail-closed |
| OHLC 일봉에서 익절/손절 동시 touch | 손절 우선 |
| live-capital payload 유입 | 거부 |
| paper closed 상태 재업데이트 | 변경하지 않음 |

---

## 9. 다음 구현 로드맵

### Phase 1: Contract hardening

목표: Agent 출력이 항상 프로그램 검증으로 연결되도록 만든다.

- `strategy_spec` 추출 실패율 측정
- invalid spec 사유 저장
- prose fallback 결과를 `diagnostic_only=True`로 표시
- dashboard에 `검증 불가 사유`를 한국어로 노출

### Phase 2: Daily deterministic job

목표: Agent 호출 없이 매일 paper signal 상태를 갱신한다.

- open paper signals 조회
- ticker별 최신 OHLC fetch
- `update_signal_with_ohlc()` 호출
- fill/exit/PnL summary 저장
- reanalysis trigger 발생 목록 생성

### Phase 3: Reanalysis queue

목표: 프로그램이 재분석 후보를 만들고, Agent가 해석한다.

- `reanalysis_required` queue/table 추가
- trigger type/reason/price/date 저장
- Agent 재분석 실행 후 새 run과 이전 strategy 연결
- 새 StrategySpec이 나오기 전까지 자동 재진입 차단

### Phase 4: Walk-forward validation

목표: 현재 report의 과거 적용이 아니라 out-of-sample 검증을 만든다.

- calibration period / test period 분리
- rolling window 지원
- 전략 family별 성과 저장
- factor score와 forward return 상관관계 계산

### Phase 5: Paper performance dashboard

목표: 실제 후보 신호의 누적 모의 성과를 보여준다.

- open/closed/not-filled 수
- 누적 PnL
- 승률/profit factor/최대낙폭
- 평균 보유기간
- 미체결률
- stop/target/expired 비율
- 전략/점수 factor별 성과 분해

---

## 10. 개발 체크리스트

새 기능을 추가할 때 아래를 먼저 확인한다.

```text
1. 이 판단은 의미 해석인가, deterministic 계산인가?
2. 같은 입력이면 항상 같은 결과가 나와야 하는가?
3. Agent 호출 없이 매일 반복 실행되어야 하는가?
4. 실패하면 후보 제외가 안전한가?
5. live_capital_allowed가 False로 유지되는가?
6. 사용자-facing 문구가 한국어이고 수익 보장처럼 보이지 않는가?
```

판단 기준:

```text
의미 해석, 불확실한 원인 설명, 새 전략 제안 -> Agent
반복 계산, 가격 touch, 비용/PnL, gate, 상태 전이 -> Program
```
