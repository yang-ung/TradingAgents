from __future__ import annotations

import argparse
import json
from pathlib import Path

from tradingagents.automation.cross_market_context import write_cross_market_context
from tradingagents.dashboard.storage import AnalysisRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Build shared cross-market context for TradingAgents dashboard")
    parser.add_argument("--dashboard-dir", default="artifacts/dashboard-pilot-20260430")
    args = parser.parse_args()
    dashboard_dir = Path(args.dashboard_dir)
    context = write_cross_market_context(dashboard_dir)
    source_paths = [dashboard_dir / "signal_snapshot.json", dashboard_dir / "crypto_signal_snapshot.json", dashboard_dir / "hourly_report.json"]
    source_updated_at = str(max((path.stat().st_mtime for path in source_paths if path.exists()), default=0.0))
    AnalysisRepository(dashboard_dir).save_cross_market_context(context, source_updated_at=source_updated_at)
    print(dashboard_dir / "cross_market_context.json")
    print(json.dumps({"available": context.get("available"), "primary_action": (context.get("summary") or {}).get("primary_action"), "state_persistence": "sqlite"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
