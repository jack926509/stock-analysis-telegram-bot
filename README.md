# 🛡️ 零幻覺美股 Telegram 分析機器人

> **Zero-Hallucination US Stock Analysis Telegram Bot**

一個基於 **RAG（檢索增強生成）** 架構的 Telegram Bot，完全基於真實數據進行邏輯推演，嚴格排除 AI 幻覺。

[![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4o-412991?logo=openai&logoColor=white)](https://openai.com/)
[![Deploy on Zeabur](https://img.shields.io/badge/Deploy-Zeabur-7B61FF?logo=zeabur&logoColor=white)](https://zeabur.com/)

---

## ✨ 功能特色

- 🔍 **多源數據整合** — 並行抓取 4 大數據源，全面涵蓋股票分析所需資訊
- 🛡️ **反幻覺機制** — 四層防護確保 AI 分析 100% 基於真實數據
- ⚡ **非同步並行** — 使用 `asyncio.gather()` 並行抓取，大幅降低響應延遲
- 📊 **美觀報告** — 結構化 Markdown 報告，原始數據 + AI 分析一目瞭然

---

## 📐 系統架構

```
使用者 ──▶ Telegram Bot ──▶ 數據檢索層 (並行)  ──▶ AI 分析層 ──▶ 格式化報告
                             ├── Finnhub    (即時股價)
                             ├── yfinance   (基本面)
                             ├── Tavily     (真實新聞)
                             └── TradingView(技術指標)
```

### 反幻覺四層防護

| 層級 | 措施 |
|------|------|
| **① 數據層** | 每個欄位獨立驗證，缺失即標 `N/A`，不做任何推測填充 |
| **② Prompt 層** | System Prompt 嚴格約束 AI 只能使用提供的 Context 數據 |
| **③ Context 層** | 數據以結構化 JSON 注入，標明來源，AI 無法憑空編造 |
| **④ 輸出層** | 報告同時展示「原始數據」與「AI 分析」，使用者可交叉驗證 |

---

## 📁 專案結構

```
stock_bot_project/
├── main.py                    # 程式進入點
├── config.py                  # 設定管理（讀取 .env）
├── requirements.txt           # Python 套件清單
├── .env.example               # 環境變數範本
├── Procfile                   # Zeabur 部署設定
├── zbpack.json                # Zeabur 建構設定
├── fetchers/                  # 📡 數據檢索層
│   ├── finnhub_fetcher.py     #   即時股價 (Finnhub API)
│   ├── yfinance_fetcher.py    #   基本面數據 (yfinance)
│   ├── tavily_fetcher.py      #   真實新聞 (Tavily Search)
│   └── tradingview_fetcher.py #   技術指標 (TradingView-TA)
├── analyzer/                  # 🤖 AI 分析層
│   └── openai_analyzer.py     #   OpenAI GPT 分析引擎
├── bot/                       # 💬 Telegram 介面層
│   └── telegram_bot.py        #   Bot 指令處理
└── utils/                     # 🔧 工具模組
    └── formatter.py           #   報告格式化
```

---

## 🚀 快速開始

### 1. 取得 API Keys

| Key | 取得方式 | 費用 |
|-----|---------|------|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) 建立 Bot | 免費 |
| `FINNHUB_API_KEY` | [finnhub.io](https://finnhub.io/) 註冊 | 免費方案 |
| `TAVILY_API_KEY` | [tavily.com](https://tavily.com/) 註冊 | 免費方案 |
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com/) | 按量計費 |

> 💡 yfinance 和 TradingView-TA 不需要 API Key，直接免費使用。

### 2. 本地安裝

```bash
# Clone 專案
git clone https://github.com/jack926509/stock-analysis-telegram-bot.git
cd stock-analysis-telegram-bot

# 建立虛擬環境
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# venv\Scripts\activate   # Windows

# 安裝套件
pip install -r requirements.txt

# 設定環境變數
cp .env.example .env
# 編輯 .env 填入你的 API Keys
```

### 3. 啟動 Bot

```bash
python main.py
```

---

## ☁️ Zeabur 部署

1. 前往 [Zeabur](https://zeabur.com/) 建立專案
2. 選擇 **Import from GitHub** → 選擇 `stock-analysis-telegram-bot`
3. 在 **Variables** 頁面設定以下環境變數：

```
TELEGRAM_BOT_TOKEN=你的_Telegram_Bot_Token
FINNHUB_API_KEY=你的_Finnhub_API_Key
TAVILY_API_KEY=你的_Tavily_API_Key
OPENAI_API_KEY=你的_OpenAI_API_Key
```

4. 部署完成，Bot 自動啟動！

---

## 💬 使用方式

在 Telegram 中與 Bot 對話：

| 指令 | 說明 | 範例 |
|------|------|------|
| `/start` | 查看歡迎訊息與使用說明 | `/start` |
| `/report [代碼]` | 產生完整股票分析報告 | `/report AAPL` |

### 報告範例

```
📊 AAPL — Apple Inc.
━━━━━━━━━━━━━━━━━━━━━━━━

💰 即時報價
├ 當前價格: $178.72
├ 漲跌幅: 🟢 +1.25%
├ 盤中高/低: $179.50 / $176.80
└ 前收盤: $176.52

📈 基本面概覽
├ 產業: Technology / Consumer Electronics
├ 市值: $2.78T
├ 本益比 (TTM): 28.5
├ EPS: $6.27
└ 52 週高/低: $199.62 / $143.90

🔍 技術面信號
├ 整體建議: 🟢 BUY
├ RSI(14): 58.32
├ MACD: 1.2345
└ 信號統計: 買12 / 中性8 / 賣6

📰 近期新聞
  1. Apple unveils new AI features...
  2. ...

━━━━━━━━━━━━━━━━━━━━━━━━
🤖 AI 深度分析
━━━━━━━━━━━━━━━━━━━━━━━━

[基於以上真實數據的 AI 分析...]

⚠️ 免責聲明：本報告僅供參考，不構成投資建議。
```

---

## 🔧 技術細節

### 技術棧

| 技術 | 用途 |
|------|------|
| **Python 3.9+** | 主要語言 |
| **python-telegram-bot** | Telegram Bot 框架 |
| **OpenAI GPT-4o** | AI 分析引擎 |
| **Finnhub** | 即時股價 API |
| **yfinance** | 基本面數據 |
| **Tavily** | 新聞搜尋 API |
| **TradingView-TA** | 技術指標分析 |
| **asyncio** | 非同步並行處理 |

### 關鍵設計決策

| 決策 | 理由 |
|------|------|
| `asyncio.to_thread()` 包裝同步 API | 避免阻塞 event loop，實現真正並行 |
| GPT `temperature=0.3` | 降低創造性回答，提升事實性 |
| 原始數據同步展示 | 反幻覺最後防線，使用者可交叉驗證 |
| 模組化架構 | 數據源獨立，可輕鬆替換或擴展 |

---

## 📄 授權

本專案採用 [MIT License](LICENSE) 授權。

---

## ⚠️ 免責聲明

本工具僅供教育和研究用途。所有分析報告不構成投資建議。投資有風險，請自行判斷。
