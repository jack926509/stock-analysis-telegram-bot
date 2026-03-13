"""
健康檢查 HTTP 端點模組
提供 /health 端點供 Zeabur 監控服務狀態。
"""

import asyncio
import logging
import time
from aiohttp import web

logger = logging.getLogger(__name__)

_start_time: float = time.time()
_request_count: int = 0


def increment_request_count():
    """增加請求計數。"""
    global _request_count
    _request_count += 1


async def health_handler(request: web.Request) -> web.Response:
    """健康檢查端點。"""
    uptime = int(time.time() - _start_time)
    hours = uptime // 3600
    minutes = (uptime % 3600) // 60
    seconds = uptime % 60

    return web.json_response({
        "status": "healthy",
        "uptime": f"{hours}h {minutes}m {seconds}s",
        "uptime_seconds": uptime,
        "total_requests": _request_count,
    })


async def start_health_server(port: int = 8080) -> web.AppRunner | None:
    """啟動健康檢查 HTTP 伺服器。"""
    try:
        app = web.Application()
        app.router.add_get("/health", health_handler)
        app.router.add_get("/", health_handler)  # 根路徑也回應

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info(f"🏥 健康檢查端點已啟動: http://0.0.0.0:{port}/health")
        return runner
    except Exception as e:
        logger.warning(f"⚠️ 健康檢查端點啟動失敗: {e}")
        return None
