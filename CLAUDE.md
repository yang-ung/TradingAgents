# CLAUDE.md

Project-specific coding-agent guidelines for TradingAgents. These rules adapt Karpathy-style agent guidance to this repository: think before coding, prefer simple solutions, make surgical changes, and verify goal completion.

## 1. Think Before Coding

Before changing code, identify the smallest interpretation that satisfies the request.

- State assumptions when a requirement is ambiguous enough to change the implementation.
- Ask only when the ambiguity materially changes what file/API/data path should be touched.
- Push back on requests that imply live trading, capital deployment, or guaranteed profit before validation gates are satisfied.
- Prefer deterministic program logic for validation, replay, trigger detection, and paper-trading state updates. Use agents to interpret and summarize, not to silently redefine execution rules.
- Use `docs/agent_program_boundary.md` as the source of truth when deciding whether new trading behavior belongs in Agent prompts or deterministic program code.

## 2. Simplicity First

Implement the minimum code that solves the current TradingAgents problem.

- Do not add speculative frameworks, generic plugin systems, or broad configurability unless the task requires them.
- Do not introduce new abstractions for a single use case.
- Keep validation and paper-trading logic explicit and auditable, even if that is less clever.
- Add complexity only for real safety requirements: live-capital blocking, validation gates, cost/slippage handling, state immutability, replay correctness, or data integrity.

## 3. Surgical Changes

Touch only files directly related to the request.

- Do not refactor adjacent code, prompts, templates, comments, formatting, or CSS as a drive-by improvement.
- Match existing style and naming in the file being edited.
- Remove only imports, variables, or helpers made unused by your change.
- If unrelated dead code or design debt is noticed, mention it separately rather than fixing it in the same change.
- Every changed line should trace back to the user request, a failing test, or a required verification fix.

## 4. Goal-Driven Execution

Convert implementation tasks into verifiable outcomes.

For non-trivial behavior changes:

1. Define the behavior and success criteria.
2. Add or update focused tests first when practical.
3. Run the focused test and verify it fails for the expected reason.
4. Implement the smallest passing change.
5. Run focused tests, relevant tests, and any lightweight smoke checks needed for the changed path.
6. Check `git diff --check` before committing.

Documentation-only or instruction-only changes do not need RED/GREEN tests, but still require diff review and formatting sanity checks.

## TradingAgents Product Rules

- User-facing text should be Korean-first when building dashboard/UI/report features.
- Do not expose internal state keys or raw markdown labels in user-facing UI, such as `quant_strategy_report`, `final_trade_decision`, `market_report`, `**Recommendation**`, or `**Action**`.
- Translate action/strategy labels for users: `Buy` → `매수`, `Hold` → `보유/관망`, `Sell` → `매도`, `take profit` → `익절`, `stop loss` → `손절`, `pullback` → `눌림목`, `breakout` → `돌파`.
- KRW amounts shown to users should use comma formatting and no decimal places where possible.
- Avoid profit-certification language. Prefer `과거 적용 시뮬레이션`, `백테스트 시뮬레이션`, or `전략 실행 리플레이`.

## Trading Validation and Safety Rules

- Default stance before sufficient out-of-sample, walk-forward, and paper-trading evidence is `실거래 보류`.
- `live_capital_allowed` must remain `False` unless the user explicitly asks for a live-trading feature and separate safety gates are designed and reviewed.
- Gate A/pre-live validation can promote a strategy only to a paper-trading candidate, not to live capital.
- Validation and paper-trading calculations should account for costs/slippage using conservative defaults unless the task explicitly changes the model.
- For paper trading with daily OHLC data, use conservative assumptions: do not overstate fills, prefer stop-loss when take-profit and stop-loss are both touched in the same daily candle, preserve open-state fill data across incremental updates, and keep closed trades immutable.
- Do not claim a strategy is profitable or safe based only on a single backtest, a small sample, or in-sample results.

## Review Checklist Before Commit

- [ ] The diff is limited to the requested scope.
- [ ] New or changed behavior has focused tests where practical.
- [ ] Relevant tests and `git diff --check` pass.
- [ ] User-facing labels are Korean/localized where applicable.
- [ ] No secrets, credentials, API keys, tokens, or connection strings were added.
- [ ] No live-capital path was introduced accidentally.
