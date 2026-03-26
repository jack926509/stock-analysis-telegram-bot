"""
結構化日誌設定模組
"""

import logging
import sys

from forex_trading.config import ForexConfig


def setup_logging() -> None:
    """設定結構化日誌。"""
    log_format = (
        '{"time":"%(asctime)s","level":"%(levelname)s",'
        '"module":"%(name)s","message":"%(message)s"}'
        if ForexConfig.is_production()
        else "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    logging.basicConfig(
        format=log_format,
        level=logging.INFO,
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.WARNING)
