# 실전 투입 전 수익성 검증 절차

이 프로젝트는 투자 조언을 바로 실거래로 연결하지 않는다. Agent는 매매 계획을 제안하고, 프로그램은 그 계획을 동일 규칙으로 검증한 뒤 통과한 전략만 paper trading 후보로 올린다.

책임 경계의 기준 문서는 `docs/agent_program_boundary.md`다. 이 문서는 Agent가 해석/제안을 맡고, 프로그램이 반복 실행/검증/상태 전이를 맡는 계약을 정의한다.

## 핵심 원칙

1. **Agent 판단과 프로그램 검증 분리**
   - Agent: 종목/시장/뉴스/펀더멘털/차트 의미 해석, 진입·익절·손절 계획 제안
   - 프로그램: 동일 규칙을 과거/미래 샘플에 적용, 비용·슬리피지·낙폭 기준으로 탈락 처리

2. **실전 자금 전 3단계 게이트**
   - Gate A: walk-forward 과거 검증
   - Gate B: paper trading 실시간 모의 운용
   - Gate C: 소액 제한 실거래 검증

3. **점수판 기준과 매매 실행 기준 분리**
   - 방향성 점수가 좋아도 진입 타이밍/시장 공통 리스크가 나쁘면 매수 금지
   - 전쟁, 금리, 환율, 유가, 관세, 제재 등 시장 공통 리스크는 모든 종목에 공통으로 반영

## Gate A: Walk-forward 검증

- 최소 표본: 종목/전략 조합별 30회 이상
- 비용 반영: 매수·매도 양쪽 수수료 + 슬리피지
- 금지: 같은 기간을 보고 만든 전략을 같은 기간 성과로 인증하지 않기
- 측정:
  - 총수익률
  - 벤치마크 대비 초과수익
  - 승률
  - 손익비 / profit factor
  - 최대낙폭
  - 연속 손실
  - 1억원 기준 손익

기본 통과 기준:

```text
표본 >= 30회
승률 >= 50%
profit factor >= 1.2
최대낙폭 <= 10%
비용 반영 후 총수익률 > 0
```

## Gate B: Paper trading

- 실제 API/브로커 주문 전, 매일 신호를 저장하고 다음 날/다음 주 체결 가능성을 검증
- 최소 기간: 4~8주
- 반드시 저장:
  - 신호 생성 시점
  - 당시 가격
  - 제안 진입가/익절가/손절가
  - 시장 공통 리스크 점수
  - 실제 체결 가능 여부
  - 비용 반영 후 손익

## Gate C: 소액 실거래

- Gate A/B 모두 통과한 전략만 후보
- 시작 비중: 총 투자 가능 금액의 1~5% 이하
- 자동 중단 조건:
  - 연속 손실 3회
  - 누적 손실 -3% 또는 설정 낙폭 초과
  - 시장 공통 리스크 `매우 부정`
  - 손절 이후 Agent 재분석 전 자동 재진입 금지

## 현재 구현된 검증 도구

`tradingagents.validation.performance.evaluate_trade_returns()`는 gross trade return 목록에 비용을 차감한 뒤 아래 값을 계산한다.

- 비용 반영 수익률 목록
- 거래 수
- 승률
- 평균 수익률
- 복리 총수익률
- 최대낙폭
- profit factor
- 통과 여부
- 실패 사유

예시:

```python
from tradingagents.validation.performance import ValidationConfig, evaluate_trade_returns

result = evaluate_trade_returns(
    [5.0, -2.0, 3.0],
    ValidationConfig(commission_bps=5, slippage_bps=10),
)
```

`tradingagents.validation.pre_live.build_pre_live_validation_report()`는 dashboard 백테스트 거래 로그를 Gate A pre-live 검증 리포트로 변환한다.

- 거래별 수익률에 수수료/슬리피지 왕복 비용 차감
- 표본 수, 승률, profit factor, 최대낙폭, 총수익률 gate 적용
- 벤치마크 대비 초과수익 계산
- 1억원 기준 손익 계산
- 시장 공통 리스크가 `매우 부정`이면 fail-closed 처리
- 통과해도 `live_capital_allowed=False` 유지, `paper trading 후보`로만 표시

Dashboard 상세 페이지와 API에서도 확인할 수 있다.

```text
GET /api/runs/{run_id}/pre-live-validation
GET /api/pre-live-validation?market=KR&latest_only=true&limit=100
```

다종목 요약에는 `build_batch_pre_live_validation_report()`를 사용한다. 이 함수는 각 run의 pre-live 결과를 모아 `paper trading 후보`, `검증 실패`, `실전 허가 수(항상 0이어야 함)`를 요약한다.

Gate B 시작을 위한 append-only paper trading ledger도 추가했다.

```text
POST /api/paper-trading/signals
GET /api/paper-trading/signals?ticker=005930.KS
PATCH /api/paper-trading/signals/{signal_id}/fill
```

저장되는 기본 항목:

- `signal_id`, `created_at`, `status=paper_trading`
- `run_id`, `ticker`, `trade_date`, `strategy_id`, `signal_date`
- `current_price`, `entry_low`, `entry_high`, `take_profit`, `stop_loss`
- 방향성/진입 타이밍/시장 공통 리스크 점수
- fill/PnL placeholder
- `live_capital_allowed=False` 고정

`PATCH /api/paper-trading/signals/{signal_id}/fill`은 일봉 OHLC를 받아 진입 가능 여부와 익절/손절/PnL을 업데이트한다.
append-only snapshot 방식이라 같은 `signal_id`의 최신 상태만 조회에 노출된다.
이미 종료된 paper trade는 재개/강등하지 않고, 열린 포지션에 누적 OHLC를 다시 넣더라도 기존 진입일 이전 candle은 exit 판정에서 제외한다.
일봉에서 익절가와 손절가가 같은 날 모두 터치되면 보수적으로 손절 우선으로 계산한다.
기본 비용 모델은 pre-live와 동일하게 `commission_bps=5.0`, `slippage_bps=10.0`이며, 왕복 비용 0.30%를 수익률에서 차감한다.

`live_capital_allowed=True` payload는 거부한다. 이 ledger는 실전 주문장이 아니라 Gate B 모의 운용 기록 저장소다.

## 다음 자동화 대상

1. 저장된 `strategy_spec`와 가격 데이터를 묶어 다종목 walk-forward 결과 생성
2. 셀프 피드백 루프 운영
   - 사용자가 반복 횟수 N을 지정하면 `StrategySpec → 일봉 백테스트 → pre-live 검증 → Quant Strategy Analyst 수정`을 N회 반복
   - 각 회차의 전략, 성과, 실패 사유, 수정 피드백, best iteration을 `strategy_self_feedback_loops`에 저장
   - API: `GET /api/strategy-self-feedback`, `GET /api/strategy-self-feedback/{loop_id}`
   - 이 루프도 `live_capital_allowed=False`를 유지하며, 최종 후보는 paper trading 검토 대상으로만 표시
3. 점수판 factor별 성과 분해
   - 시장 공통 리스크가 나쁠 때 매수한 경우의 성과
   - 진입 타이밍 점수가 양수/음수일 때 성과
   - 방향성 점수와 실제 forward return 상관관계
4. Paper trading ledger를 매일 실행해 새 OHLC로 fill/PnL 자동 업데이트
5. 통과 전략만 dashboard에 `paper trading 후보`로 표시하고, 실전 투입은 Gate B/C 완료 전까지 계속 차단
