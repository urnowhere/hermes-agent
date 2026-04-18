"""Metrics API routes for the Hermes Web Console backend."""

import logging
import psutil
import time
from aiohttp import web

logger = logging.getLogger("metrics_api")

async def handle_get_metrics_global(request: web.Request) -> web.Response:
    try:
        from hermes_state import SessionDB
        from agent.insights import InsightsEngine
        from tools.process_registry import process_registry
        
        cron_jobs_count = 0
        try:
            from cron.scheduler import scheduler
            cron_jobs_count = len(scheduler.get_jobs()) if hasattr(scheduler, "get_jobs") else 0
        except Exception:
            pass
            
        db = SessionDB()
        engine = InsightsEngine(db)
        
        # Fetch current day usage as a representative metric snippet
        report = engine.generate(days=1)
        db.close()
        
        metrics = {
            "token_usage_today": report.get("total_tokens", 0),
            "cost_today": report.get("estimated_cost_usd", 0.0),
            "active_processes": len([s for s in process_registry.list_sessions() if s.get("status") == "running"]),
            "cron_jobs": cron_jobs_count,
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": psutil.virtual_memory().percent,
            "uptime_seconds": time.time() - psutil.boot_time(),
        }
        
        return web.json_response({"ok": True, "metrics": metrics})
    except Exception as e:
        logger.error(f"Failed to fetch global metrics: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

def register_metrics_api_routes(app: web.Application) -> None:
    app.router.add_get("/api/gui/metrics/global", handle_get_metrics_global)
