import logging
from aiohttp import web

logger = logging.getLogger("usage_api")

async def handle_get_usage_insights(request: web.Request) -> web.Response:
    try:
        days = int(request.query.get("days", 30))
        source = request.query.get("source")
        
        from hermes_state import SessionDB
        from agent.insights import InsightsEngine
        
        db = SessionDB()
        engine = InsightsEngine(db)
        report = engine.generate(days=days, source=source)
        db.close()
        
        return web.json_response({"ok": True, "report": report})
    except Exception as e:
        logger.error(f"Failed to fetch usage insights: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def handle_get_session_usage(request: web.Request) -> web.Response:
    try:
        session_id = request.match_info.get("id")
        if not session_id:
            return web.json_response({"ok": False, "error": "Session ID required"}, status=400)
            
        from hermes_state import SessionDB
        db = SessionDB()
        try:
            session = db.get_session(session_id)
            if not session:
                return web.json_response({"ok": False, "error": "Not found"}, status=404)
                
            return web.json_response({"ok": True, "session_usage": {
                "input_tokens": getattr(session, "input_tokens", 0) or 0,
                "output_tokens": getattr(session, "output_tokens", 0) or 0,
                "cache_read_tokens": getattr(session, "cache_read_tokens", 0) or 0,
                "cache_write_tokens": getattr(session, "cache_write_tokens", 0) or 0,
                "total_tokens": getattr(session, "total_tokens", 0) or 0,
                "estimated_cost_usd": getattr(session, "estimated_cost_usd", 0.0) or 0.0,
            }})
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to fetch session usage: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

def register_usage_api_routes(app: web.Application) -> None:
    app.router.add_get("/api/gui/usage/insights", handle_get_usage_insights)
    app.router.add_get("/api/gui/usage/session/{id}", handle_get_session_usage)
