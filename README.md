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
- 🧠 **深度分析** — 基本面、技術面、量能、籌碼面、市場情緒五維度交叉驗證

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
Technology | Consumer Electronics
⚡ 🟢 +1.25% | 🟢 買入 | RSI 58 中性 | 🟢 多頭排列
🔋 數據: [●●●●] 4/4 ✅Finnhub ✅yfinance ✅Tavily ✅TradingView
━━━━━━━━━━━━━━━━━━━━━━━━

💰 現價: $178.72  🟢 +1.25% (+$2.20)
  高/低: $179.50 / $176.80  前收: $176.52
  52W位置: [▓▓▓▓▓▓░░░░] 60%

━━━━━━━━━━━━━━━━━━━━━━━━
📈 基本面
  市值: $2.78T  Beta: 1.24
  PE: 28.50  Forward PE: 25.30 📈成長預期
  EPS: $6.27  PEG: 1.85
  殖利率: 0.55%  利潤率: 26.31%
  營收成長: 8.12%  盈餘成長: 12.50%
  空頭比率: 1.2  機構持股: 60.15%
  52W: $143.90 ~ $199.62
  50MA: $172.50  200MA: $168.30

━━━━━━━━━━━━━━━━━━━━━━━━
📦 量能分析
  成交量: 65.2M
  平均量: 58.1M
  量比: 1.1x ➡️ 正常

━━━━━━━━━━━━━━━━━━━━━━━━
🔍 技術面信號
  建議: 🟢 買入
  🟢🟢🟢🟢🟢🟡🟡🟡🔴🔴 買12/中8/賣6
  RSI: 58.32 中性  ADX: 28.50 強趨勢
  MACD: 1.2345  Signal: 0.8765
  EMA20: $176.50  SMA50: $172.50  SMA200: $168.30
  趨勢: 🟢 多頭排列
  布林: $182.30 ~ $170.10
  均線: 🟢 買入  震盪: 🟡 中性

━━━━━━━━━━━━━━━━━━━━━━━━
📰 新聞
  [AI 新聞摘要...]

══════════════════════════
🤖 AI 深度分析
══════════════════════════

[基於真實數據的五維度深度分析...]

══════════════════════════
⚠️ 本報告僅供參考研究，不構成投資建議。
數據來源: Finnhub | yfinance | Tavily | TradingView
📅 2026-03-13 12:00 UTC | 🛡️ Zero-Hallucination Engine
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
| Singleton 客戶端 | 避免重複建立 API 連線 |
| 5 分鐘快取 | 防止短時間重複查詢浪費 API 額度 |

---

## 📝 修改歷程 (Changelog)

### v2.1 — 三角色深度優化 v2 (2026-03-13)

#### 🏦 美股分析師優化
- **修正 PE 提示邏輯**：Forward PE < Trailing PE 顯示 📈成長預期（原為反向 emoji）
- **強化 System Prompt**：
  - 分析師角色升級為「縱橫華爾街 20 年」的實戰背景
  - 新增 PEG 估值判斷框架（<1 低估 / 1-2 合理 / >2 偏高）
  - 新增均線排列定義（多頭排列 vs 空頭排列條件）
  - 新增布林通道、ATR 波動率分析指引
  - 新增短線/中線操作建議輸出
- **新增籌碼面數據**：空頭比率 (Short Ratio)、機構持股比例、內部人持股
- **新增技術指標**：布林通道上下軌、Stochastic %D、ATR 波動率

#### 🎨 前端 UX/UI 優化
- **新增快速摘要欄位** `⚡`：報告頂部一行顯示漲跌、技術建議、RSI、均線趨勢
- **新增均線趨勢判斷**：自動判斷多頭/空頭/偏多整理/偏空整理排列
- **新增布林通道顯示**：技術面區塊顯示布林上下軌
- **新增籌碼面區塊**：顯示空頭比率與機構持股
- **PE 提示 emoji 修正**：成長預期使用 📈，放緩使用 📉

#### ⚙️ 後端工程優化
- **修復 Semaphore 並發控制 Bug**：原 `locked()` 方法在 Semaphore(3) 下僅在全部 3 slot 用完才觸發，改為直接檢查 `_value`
- **新增 API 客戶端 Singleton 模式**：
  - Finnhub Client → 共用實例（避免每次請求重建）
  - Tavily Client → 共用實例
  - OpenAI Client → 已有共用實例（保持）
- **新增請求快取機制**：5 分鐘 TTL 快取，相同 ticker 短時間內不重複呼叫 API
- **強化 Markdown 衝突清理**：新增底線 (`_斜體_`) 與反引號清理，降低 Telegram 解析失敗率
- **啟動日誌增強**：顯示使用的 AI 模型名稱

### v2.0 — 三角色深度優化 (2026-03-12)
- 分析師：成交量分析、成長性評估、量化評分框架
- 前端：精簡結構、Markdown 安全處理、視覺化指標
- 後端：並發控制、超時管理、return_exceptions

### v1.1 — 修復 Bot 無反應 (2026-03-11)
- 修正 Markdown 格式導致 Telegram 無法顯示的問題
- 增強錯誤處理與 fallback 機制

### v1.0 — 初始版本 (2026-03-10)
- 基礎架構：4 數據源 + AI 分析 + 格式化報告
- 反幻覺四層防護
- Zeabur 部署支援

---

## 🔮 未來優化方向

### 分析師面
- [ ] **歷史數據回測**：加入 yfinance 歷史 K 線，提供近 30/60/90 天報酬率
- [ ] **同業比較**：抓取同產業 Top 5 公司 PE、成長率做橫向對比
- [ ] **財報日曆**：標示下一次財報發布日期，提醒投資人注意
- [ ] **選擇權資料**：整合 Put/Call Ratio、隱含波動率 (IV) 等衍生品數據
- [ ] **支撐壓力位計算**：根據技術指標自動計算關鍵價格區間
- [ ] **ETF 支援**：擴展支援 ETF 分析（如 SPY、QQQ）

### 前端面
- [ ] **圖表生成**：使用 matplotlib/plotly 生成 K 線圖，以圖片發送
- [ ] **互動按鈕**：InlineKeyboard 讓使用者選擇分析深度（快速/標準/深度）
- [ ] **自選股清單**：`/watchlist` 指令管理追蹤清單
- [ ] **定時推送**：設定每日盤前/盤後自動推送追蹤個股報告
- [ ] **多語言支援**：英文/簡體中文版本切換

### 後端面
- [ ] **Webhook 模式**：將 Polling 改為 Webhook，降低資源消耗（適合 Zeabur）
- [ ] **Redis 快取**：替換記憶體快取為 Redis，支援多實例部署
- [ ] **資料庫**：SQLite/PostgreSQL 記錄歷史查詢與使用者偏好
- [ ] **API Rate Limiter**：per-user 請求限制，防止濫用
- [ ] **健康檢查端點**：HTTP `/health` 端點供 Zeabur 監控
- [ ] **日誌持久化**：結構化 JSON 日誌，整合 Zeabur 日誌系統
- [ ] **Graceful Shutdown**：優雅關閉，確保進行中的分析完成後才停止
- [ ] **環境配置分離**：dev/staging/production 多環境設定檔

---

## 📄 授權

本專案採用 [MIT License](LICENSE) 授權。

---

## ⚠️ 免責聲明

本工具僅供教育和研究用途。所有分析報告不構成投資建議。投資有風險，請自行判斷。
