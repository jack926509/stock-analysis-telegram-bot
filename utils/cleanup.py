"""
背景檔案清理工具。

tenk pipeline 會在 TENK_CACHE_DIR / TENK_OUTPUT_DIR 累積：
- HTM 原檔（每份財報數百 KB ~ 數 MB）
- XBRL JSON（companyfacts，可能數十 MB）
- pipeline 中間 markdown 與 raw json

DB 那邊的 tenk_reports 已有 TTL（TENK_REPORT_TTL_DAYS），但檔案系統
沒有任何清理機制，長時間運行會吃光磁碟。這個模組提供一支 helper
讓 bot 啟動時 fire-and-forget 跑一次。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from config import Config

logger = logging.getLogger(__name__)


def _sweep(root: Path, max_age_days: int) -> tuple[int, int]:
    """
    刪掉 root 下 mtime 早於 cutoff 的檔案。
    回傳 (deleted_count, freed_bytes)。
    """
    if not root.exists():
        return 0, 0

    cutoff = time.time() - max_age_days * 86400
    deleted = 0
    freed = 0
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        if st.st_mtime >= cutoff:
            continue
        try:
            f.unlink()
            deleted += 1
            freed += st.st_size
        except OSError as e:
            logger.debug(f"[cleanup] 刪除失敗 {f}: {e}")
    return deleted, freed


async def cleanup_tenk_files(max_age_days: int | None = None) -> None:
    """
    清掉 tenk 快取與輸出超過 TTL 的檔案。

    與 DB 那邊 tenk_reports.created_at 的 TTL（TENK_REPORT_TTL_DAYS）對齊；
    多保留 30 天 buffer，避免「DB 還在指但檔案先被掃掉」。
    """
    age = max_age_days or (Config.TENK_REPORT_TTL_DAYS + 30)
    cache_dir = Path(Config.TENK_CACHE_DIR)
    output_dir = Path(Config.TENK_OUTPUT_DIR)

    try:
        cache_d, cache_b = await asyncio.to_thread(_sweep, cache_dir, age)
        out_d, out_b = await asyncio.to_thread(_sweep, output_dir, age)
    except Exception as e:
        logger.warning(f"[cleanup] tenk 清理失敗: {e}")
        return

    if cache_d or out_d:
        logger.info(
            f"[cleanup] tenk 清理完成: cache {cache_d} files / {cache_b // 1024} KB, "
            f"output {out_d} files / {out_b // 1024} KB（>{age}d）"
        )
    else:
        logger.debug(f"[cleanup] tenk 無可清理檔案（>{age}d）")
