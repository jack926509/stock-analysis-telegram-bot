"""
tenk — 10-K / 10-Q 多 agent 投資研究管線（async 版）

來源：https://github.com/twCarllin/10k-analysis（MIT License，原作 twCarllin）
本專案已重新整合進 bot：
- 改 async + 共用 utils.ai_client（含 prompt cache）
- 移除 PDF 生成（純 markdown 輸出，給 Telegram 推送）
- 移除自帶 config.json，統一讀 bot 的 Config
"""

from pathlib import Path

# 套件根目錄（包含 agents/、skills/、各 .py 模組）
PACKAGE_DIR = Path(__file__).resolve().parent
