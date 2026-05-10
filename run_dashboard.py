import os
import uvicorn
from tradingagents.dashboard.app import create_dashboard_app

artifact_dir = os.environ.get("TRADINGAGENTS_DASHBOARD_DIR", "artifacts/dashboard-pilot-20260430")
app = create_dashboard_app(artifact_dir)

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8767")),
        log_level=os.environ.get("LOG_LEVEL", "info"),
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "*"),
    )
