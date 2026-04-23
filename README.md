# 🛡️ 零幻覺美股 Telegram 分析機器人

> **Zero-Hallucination US Stock Analysis Telegram Bot**

一個基於 **RAG（檢索增強生成）** 架構的 Telegram Bot，完全基於真實數據進行邏輯推演，嚴格排除 AI 幻覺。

[![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4o-412991?logo=openai&logoColor=white)](https://openai.com/)
[![Deploy on Zeabur](https://img.shields.io/badge/Deploy-Zeabur-7B61FF?logo=zeabur&logoColor=white)](https://zeabur.com/)

---

## ✨ 功能特色

- 🔍 **多源數據整合** — 並行抓取 6 大數據源（即時股價、基本面、技術指標、新聞、歷史回測、同業比較）
- 🛡️ **反幻覺機制** — 四層防護確保 AI 分析 100% 基於真實數據
- ⚡ **非同步並行** — 使用 `asyncio.gather()` 並行抓取，大幅降低響應延遲
- 📊 **美觀報告** — 結構化 Markdown 報告，原始數據 + AI 分析一目瞭然
- 🧠 **深度分析** — 基本面、技術面、量能、籌碼面、歷史回測、同業比較、市場情緒七維度交叉驗證
- 📋 **自選股清單** — 個人化追蹤清單，快速查閱常用標的
- 🔒 **ETF 支援** — 支援 SPY、QQQ 等 ETF 分析
- 📈 **K 線圖** — 自動生成 60 日 K 線圖（MA5/MA20/MA60）以圖片發送
- 🏥 **健康監控** — HTTP `/health` 端點，支援 Zeabur 服務監控

---

## 📐 系統架構

```
使用者 ──▶ Telegram Bot ──▶ Rate Limiter ──▶ 數據檢索層 (並行)  ──▶ AI 分析層 ──▶ 格式化報告
                                               ├── Finnhub     (即時股價)
                                               ├── yfinance    (基本面)
                                               ├── TradingView (技術指標)
                                               ├── Tavily      (真實新聞)
                                               ├── yfinance    (歷史回測)
                                               └── yfinance    (同業比較)
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
├── main.py                    # 程式進入點（Graceful Shutdown + 健康檢查）
├── config.py                  # 設定管理（多環境支援）
├── requirements.txt           # Python 套件清單
├── .env.example               # 環境變數範本
├── Procfile                   # Zeabur 部署設定
├── zbpack.json                # Zeabur 建構設定
├── fetchers/                  # 📡 數據檢索層
│   ├── finnhub_fetcher.py     #   即時股價 (Finnhub API)
│   ├── yfinance_fetcher.py    #   基本面數據 (yfinance)
│   ├── tavily_fetcher.py      #   真實新聞 (Tavily Search)
│   ├── tradingview_fetcher.py #   技術指標 (TradingView-TA)
│   ├── history_fetcher.py     #   歷史回測 + 支撐壓力位
│   └── peer_fetcher.py        #   同業比較
├── analyzer/                  # 🤖 AI 分析層
│   └── openai_analyzer.py     #   OpenAI GPT 分析引擎
├── bot/                       # 💬 Telegram 介面層
│   └── telegram_bot.py        #   Bot 指令處理
└── utils/                     # 🔧 工具模組
    ├── formatter.py           #   報告格式化
    ├── chart.py               #   K 線圖生成 (mplfinance)
    ├── cache.py               #   分層 LRU 快取
    ├── retry.py               #   API 指數退避重試
    ├── database.py            #   SQLite 資料庫（自選股 + 查詢歷史）
    ├── rate_limiter.py        #   Per-user 請求限制
    └── health.py              #   HTTP 健康檢查端點
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

4. 部署完成，Bot 自動啟動！（健康檢查端點自動在 8080 port 運行）

### 進階部署設定

```
BOT_MODE=webhook                # 使用 Webhook 模式降低資源消耗
WEBHOOK_URL=https://your-bot.zeabur.app
APP_ENV=production              # 啟用結構化 JSON 日誌
HEALTH_ENABLED=true             # 啟用 /health 端點
```

---

## 💬 使用方式

在 Telegram 中與 Bot 對話：

| 指令 | 說明 | 範例 |
|------|------|------|
| `/start` | 查看歡迎訊息與使用說明 | `/start` |
| `/report [代碼]` | 產生完整股票分析報告 | `/report AAPL` |
| `/report [ETF]` | 分析 ETF | `/report SPY` |
| `/watchlist` | 查看自選股清單 | `/watchlist` |
| `/watch [代碼]` | 加入自選股 | `/watch TSLA` |
| `/unwatch [代碼]` | 移除自選股 | `/unwatch TSLA` |

---

## 🔧 技術細節

### 技術棧

| 技術 | 用途 |
|------|------|
| **Python 3.9+** | 主要語言 |
| **python-telegram-bot** | Telegram Bot 框架 |
| **OpenAI GPT-4o** | AI 分析引擎 |
| **Finnhub** | 即時股價 API |
| **yfinance** | 基本面 + 歷史數據 + 同業比較 |
| **Tavily** | 新聞搜尋 API |
| **TradingView-TA** | 技術指標分析 |
| **mplfinance** | K 線圖生成 |
| **SQLite** | 自選股 + 查詢歷史 |
| **aiohttp** | 健康檢查 HTTP 端點 |
| **asyncio** | 非同步並行處理 |

### 關鍵設計決策

| 決策 | 理由 |
|------|------|
| `asyncio.to_thread()` 包裝同步 API | 避免阻塞 event loop，實現真正並行 |
| GPT `temperature=0.3` | 降低創造性回答，提升事實性 |
| 原始數據同步展示 | 反幻覺最後防線，使用者可交叉驗證 |
| 模組化架構 | 數據源獨立，可輕鬆替換或擴展 |
| Singleton 客戶端 | 避免重複建立 API 連線 |
| 分層 LRU 快取 | Raw 數據 30 分鐘 + 報告 5 分鐘，LRU 淘汰防止記憶體膨脹 |
| API 指數退避重試 | 自動重試 1 次 + 2s 等待，提升數據源穩定性 |
| Per-user Rate Limiting | 防止單一使用者濫用（每分鐘 5 次） |
| SQLite WAL 模式 | 高併發讀寫效能 |
| Webhook 模式選項 | 降低 Zeabur 資源消耗 |

### 環境配置

| 變數 | 說明 | 預設 |
|------|------|------|
| `APP_ENV` | 環境（dev/staging/production） | production |
| `BOT_MODE` | Bot 模式（polling/webhook） | polling |
| `HEALTH_PORT` | 健康檢查端口 | 8080 |
| `RATE_LIMIT_PER_MINUTE` | 每用戶每分鐘請求上限 | 5 |
| `CACHE_TTL` | 快取存活時間（秒） | 300 |
| `PEER_COMPARISON_ENABLED` | 啟用同業比較 | true |
| `HISTORY_ENABLED` | 啟用歷史回測 | true |

---

## 📝 修改歷程 (Changelog)

### v4.0 — 五大優化升級 (2026-04-23)

#### 📈 K 線圖生成
- **mplfinance 整合**：自動生成 60 日 K 線圖，含 MA5/MA20/MA60 均線
- **暗色主題**：nightclouds 風格，漲跌分色（綠漲紅跌），搭配成交量柱
- **圖片發送**：在分析報告前以圖片方式傳送，直觀展示走勢

#### 🔄 API 指數退避重試
- **新增 `utils/retry.py`**：`retry_async_call()` 通用重試函數
- **所有 Fetcher 整合**：Finnhub、yfinance、Tavily、TradingView、歷史回測、同業比較全部啟用
- **策略**：失敗後等待 2s 重試 1 次（指數退避），大幅提升數據抓取成功率

#### 📅 財報日曆提醒
- **earningsDate 提取**：從 yfinance 取得下次財報發布日期
- **倒數天數顯示**：報告中顯示「距離財報 X 天」，7 天內加 ⚠️ 提醒
- **自動解析**：支援 earningsTimestamp（Unix）與 earningsDate（Timestamp list）兩種格式

#### 🎮 InlineKeyboard 互動
- **報告附帶互動按鈕**：每份報告底部顯示「加入自選股」與「重新分析」按鈕
- **一鍵加入自選**：點擊即加入自選股清單，免輸入指令
- **一鍵刷新**：點擊即重新抓取數據並生成最新分析
- **CallbackQueryHandler**：新增回調處理器，含 Rate Limit 保護

#### 🗃️ 分層快取 + LRU 上限
- **新增 `utils/cache.py`**：基於 OrderedDict 的 LRU Cache
- **Raw 數據快取**：TTL 30 分鐘，最多 100 條，避免重複 API 呼叫
- **報告快取**：TTL 5 分鐘，最多 50 條，確保即時性
- **LRU 淘汰**：超過上限自動淘汰最久未使用的項目
- **取代舊快取**：移除 `_report_cache` 字典，統一使用分層快取

### v3.0 — 全面功能擴展 (2026-03-13)

#### 🏦 美股分析師
- **歷史數據回測**：新增 7/30/60/90 天區間報酬率、30 日年化波動率
- **支撐壓力位計算**：基於近 20/60 日高低點 + SMA20/SMA50 動態支撐壓力
- **同業比較**：自動抓取同產業 4 家代表公司，比較 PE、利潤率、營收成長
- **ETF 支援**：ticker 驗證放寬，支援 SPY、QQQ、VOO 等 ETF
- **System Prompt 強化**：新增歷史回測分析指引、同業對比維度

#### 🎨 前端 UX/UI
- **自選股清單**：`/watchlist`、`/watch`、`/unwatch` 三指令完整自選股管理
- **歷史回測區塊**：顯示多時間區間報酬率 + 波動率
- **支撐壓力位區塊**：短期/中期支撐壓力 + 動態均線參考
- **同業比較區塊**：PE/利潤率/成長率 vs 同業平均

#### ⚙️ 後端工程
- **Webhook 模式**：新增 `BOT_MODE=webhook` 選項，降低 Zeabur 資源消耗
- **SQLite 資料庫**：WAL 模式，存儲自選股清單與查詢歷史
- **Per-user Rate Limiter**：滑動窗口限流，每分鐘 5 次（可配置）
- **HTTP 健康檢查**：`/health` 端點，回報 uptime、請求計數，供 Zeabur 監控
- **結構化 JSON 日誌**：production 環境自動啟用，便於日誌分析
- **Graceful Shutdown**：信號處理，確保進行中的分析完成後才停止
- **環境配置分離**：`APP_ENV` 支援 dev/staging/production 多環境
- **降低第三方日誌噪音**：httpx/httpcore 等設為 WARNING 等級
- **查詢記錄**：每次 `/report` 自動記入資料庫

### v2.1 — 三角色深度優化 v2 (2026-03-13)

#### 🏦 美股分析師優化
- 修正 PE 提示 emoji（Forward PE < Trailing PE → 📈成長預期）
- 強化 System Prompt（20 年華爾街經驗、PEG 框架、均線排列定義）
- 新增籌碼面數據（Short Ratio、機構持股）
- 新增技術指標（布林通道、Stochastic %D、ATR）

#### 🎨 前端 UX/UI 優化
- 新增快速摘要欄位 `⚡`（3 秒掌握重點）
- 新增均線趨勢判斷（多頭/空頭排列）
- 新增布林通道、籌碼面區塊

#### ⚙️ 後端工程優化
- 修復 Semaphore 並發控制 Bug
- Singleton 客戶端（Finnhub、Tavily、OpenAI）
- 5 分鐘 TTL 快取
- Markdown 衝突清理強化

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
- [ ] **選擇權資料**：整合 Put/Call Ratio、隱含波動率 (IV) 等衍生品數據
- [ ] **多時間框架技術分析**：日線 + 週線 + 月線綜合判斷
- [ ] **產業輪動分析**：判斷資金流向哪些產業
- [ ] **相關性分析**：與大盤指數（SPY）的 Beta 和相關係數

### 前端面
- [ ] **定時推送**：設定每日盤前/盤後自動推送追蹤個股報告
- [ ] **多語言支援**：英文/簡體中文版本切換
- [ ] **分析歷史查詢**：`/history` 查看過往分析記錄
- [ ] **深度分析模式**：InlineKeyboard 選擇快速/標準/深度三種分析層級

### 後端面
- [ ] **Redis 快取**：替換記憶體快取為 Redis，支援多實例水平擴展
- [ ] **PostgreSQL**：替換 SQLite，支援更大規模部署
- [ ] **Docker 化**：提供 Dockerfile 與 docker-compose.yml
- [ ] **CI/CD**：GitHub Actions 自動化測試與部署
- [ ] **單元測試**：為每個 fetcher 和 formatter 寫測試

---

## 📄 授權

本專案採用 [MIT License](LICENSE) 授權。

---

## ⚠️ 免責聲明

本工具僅供教育和研究用途。所有分析報告不構成投資建議。投資有風險，請自行判斷。
