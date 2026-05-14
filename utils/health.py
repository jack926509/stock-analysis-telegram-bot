"""
健康檢查 HTTP 端點模組

端點：
- /health    Liveness（容器是否活著）— 永遠 200，給 Zeabur 重啟判斷
- /health/ready  Readiness（是否可服務流量）— DB ping 失敗回 503
- /metrics   基本 metrics（uptime / request count / cache size）

Liveness vs Readiness 分流原因：
- Zeabur 預設用單一健康檢查；若 DB 暫時失聯就被殺，會反覆重啟造成迴圈
- Liveness 只看 process 還活著、event loop 沒卡死
- Readiness 才檢查外部依賴；可用於 load balancer 流量切換
"""

import asyncio
import logging
import time
from aiohttp import web

logger = logging.getLogger(__name__)

_start_time: float = time.time()
_request_count: int = 0
_DB_PING_TIMEOUT = 3.0


def increment_request_count():
    """增加請求計數。"""
    global _request_count
    _request_count += 1


def _uptime_dict() -> dict:
    uptime = int(time.time() - _start_time)
    hours = uptime // 3600
    minutes = (uptime % 3600) // 60
    seconds = uptime % 60
    return {
        "uptime": f"{hours}h {minutes}m {seconds}s",
        "uptime_seconds": uptime,
        "total_requests": _request_count,
    }


async def liveness_handler(request: web.Request) -> web.Response:
    """Liveness：永遠 200。process 活著即可。"""
    return web.json_response({"status": "alive", **_uptime_dict()})


async def readiness_handler(request: web.Request) -> web.Response:
    """Readiness：檢查 DB 連通。失敗回 503。"""
    body: dict = {"status": "ready", **_uptime_dict()}
    try:
        # 用模組屬性而非 from-import，確保拿到最新 _pool（init_db 完成後才會非 None）
        from utils import database as _db_mod  # 延遲 import 避免循環相依
        pool = getattr(_db_mod, "_pool", None)
        if pool is None:
            body["status"] = "not_ready"
            body["reason"] = "db_pool_not_initialized"
            return web.json_response(body, status=503)

        async def _ping():
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")

        await asyncio.wait_for(_ping(), timeout=_DB_PING_TIMEOUT)
        body["db"] = "ok"
        return web.json_response(body)
    except asyncio.TimeoutError:
        body["status"] = "not_ready"
        body["db"] = f"timeout(>{_DB_PING_TIMEOUT}s)"
        return web.json_response(body, status=503)
    except Exception as e:
        body["status"] = "not_ready"
        body["db"] = f"error: {type(e).__name__}"
        logger.warning(f"[health] readiness DB ping failed: {e}")
        return web.json_response(body, status=503)


async def metrics_handler(request: web.Request) -> web.Response:
    """Lightweight metrics（無依賴外部 monitoring）。"""
    body = {**_uptime_dict()}
    try:
        from utils.cache import raw_cache, report_cache, news_cache
        body["cache"] = {
            "raw_size": raw_cache.size,
            "report_size": report_cache.size,
            "news_size": news_cache.size,
        }
    except Exception:
        pass
    return web.json_response(body)


async def start_health_server(port: int = 8080) -> web.AppRunner | None:
    """啟動健康檢查 HTTP 伺服器。"""
    try:
        app = web.Application()
        app.router.add_get("/health", liveness_handler)
        app.router.add_get("/health/live", liveness_handler)
        app.router.add_get("/health/ready", readiness_handler)
        app.router.add_get("/metrics", metrics_handler)
        app.router.add_get("/", liveness_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info(
            f"🏥 健康檢查端點已啟動: http://0.0.0.0:{port}"
            " (/health, /health/ready, /metrics)"
        )
        return runner
    except Exception as e:
        logger.warning(f"⚠️ 健康檢查端點啟動失敗: {e}")
        return None
