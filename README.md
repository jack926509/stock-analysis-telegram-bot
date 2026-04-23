<div align="center">

# 📊 美股 Telegram 分析機器人

**US Stock Analysis Telegram Bot**

基於 RAG + 12 維度量化信號引擎 + Anthropic Claude 四觀點分析  
所有論點皆需真實數據佐證，原始數據與 AI 分析同步展示供交叉驗證

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![Anthropic Claude](https://img.shields.io/badge/Anthropic-Claude_Sonnet_4-D97757?logo=anthropic&logoColor=white)](https://www.anthropic.com/)
[![Deploy on Zeabur](https://img.shields.io/badge/Deploy-Zeabur-7B61FF?logo=zeabur&logoColor=white)](https://zeabur.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[快速開始](#-快速開始) · [指令](#-指令) · [12 維信號引擎](#-12-維度量化信號引擎) · [架構](#-系統架構) · [部署](#️-zeabur-部署) · [更新記錄](#-修改歷程)

</div>

---

## ✨ 特色

- 🔍 **10+ 數據源並行** — Finnhub · FMP · yfinance · TradingView · Tavily · 宏觀 · 分析師 · 內部人 · EPS
- 🧮 **12 維度量化信號** — 純 Python 規則引擎，獨立於 LLM 的「確定性」共識
- 🤖 **Claude 四觀點深度分析** — 價值 / 成長 / 技術 / 風險，Prompt Caching + compact context + 噪音過濾三重節流
- ⭐ **自選股秒覽儀表板** — 總覽列、強弱排序、🚨 警示分組、52w 視覺長條 `▌▌▌░░░░░`、📅 財報臨近、🔄 強刷
- 🛡️ **反幻覺四層防護** — 缺失即標 `N/A`，原始數據與 AI 分析同步展示供交叉驗證
- 📈 **K 線圖 + 互動按鈕** — 60 日 MA5/20/60、「加入自選股 / 重新分析」InlineKeyboard
- ⚡ **非同步 + 三層快取** — `asyncio.gather` 並行；raw 30 分 / report 5 分 / watchlist 120 秒
- 🏥 **生產級穩定性** — 健康檢查、Rate Limiting、Graceful Shutdown、FMP↔yfinance Fallback

---

## 🚀 快速開始

### 1 · 取得 API Keys

| Key | 取得位置 | 費用 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) | 免費 |
| `FINNHUB_API_KEY` | [finnhub.io](https://finnhub.io/) | 免費方案 |
| `FMP_API_KEY` | [financialmodelingprep.com](https://financialmodelingprep.com/) | 免費 250 次/日 |
| `TAVILY_API_KEY` | [tavily.com](https://tavily.com/) | 免費方案 |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/) | 按量計費 |

> 💡 `yfinance` 與 `tradingview-ta` 無需 API Key。

### 2 · 本地啟動

> ⚠️ **需要 Python 3.10 以上**（使用 PEP 604 聯合型別語法）

```bash
git clone https://github.com/jack926509/stock-analysis-telegram-bot.git
cd stock-analysis-telegram-bot

python3.10 -m venv venv
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows

pip install -r requirements.txt

cp .env.example .env            # 填入上面的 API Keys
python main.py
```

### 3 · Zeabur 一鍵部署

1. [Zeabur](https://zeabur.com/) → New Project → Import from GitHub
2. 選擇此 repo，在 **Variables** 頁填入必要 API Keys（見[環境變數](#️-環境變數)）
3. Deploy — `zbpack.json` 已預先設好建構與啟動指令

---

## 💬 指令

### 🔍 分析

| 指令 | 功能 | 範例 |
|---|---|---|
| `/report TICKER` | 完整深度分析（12 信號 + Claude AI） | `/report AAPL` |
| `/chart TICKER` | 僅 60 日 K 線圖（秒回，省 Claude 費用） | `/chart TSLA` |
| `/compare T1 T2 …` | 並排對比 2–5 檔個股 | `/compare AAPL MSFT NVDA` |

### 📋 自選股管理

| 指令 | 功能 |
|---|---|
| `/watchlist` | 即時報價儀表板：總覽列（漲跌數 / 平均%）、強弱排序、👑 最強 / ⚠️ 最弱、52w 視覺長條、📅 財報臨近、🔄 強刷按鈕（120 秒結果快取） |
| `/scan` | 批次快掃進階版：🚨 警示分組（RSI 超買/超賣 · TV 強買/強賣 · 52w 高/低 · 量爆 · 財報 ≤7 天）+ 🟢 上漲 / 🔴 下跌分區 |
| `/watch TICKER` | 加入自選股 |
| `/unwatch TICKER` | 移除自選股 |

> 📊 **/watchlist 範例顯示**
> ```
> 📋 自選股 (5 檔)  🟢3 漲 / 🔴2 跌  平均 +0.85%
> 👑 最強 NVDA +3.21%  ⚠️ 最弱 TSLA -1.45%
> 📅 財報臨近：AAPL(3天) · MSFT(5天)
>
> 🟢 NVDA  $750.21  +3.21%  📍▌▌▌▌▌▌▌▌ 96% 🔝  🔥2.1x量
> 🟢 AAPL  $192.45  +1.23%  📍▌▌▌▌▌░░░ 72%  📅3天
> 🔴 TSLA  $245.10  -1.45%  📍▌▌░░░░░░ 28%
> ```

### 🧭 其他

| 指令 | 功能 |
|---|---|
| `/start` | 歡迎訊息 |
| `/help` | 完整指令手冊 |

---

## 🧮 12 維度量化信號引擎

純 Python 規則引擎，獨立於 AI 層產出「確定性」多空共識，交由 Claude 做最後的交叉驗證與解讀。

| 類別 | 信號 | 權重 | 觸發條件範例 |
|---|---|---:|---|
| **基本面** | 獲利能力 | 12% | ROE > 15% · 利潤率 > 20% |
| | 成長動能 | 8% | 營收 / 盈餘成長 > 10% |
| | 財務健康 | 8% | D/E < 50 · 流動比 > 1.5 · FCF > 0 |
| | 估值 | 12% | PE < 15 · PEG < 1 · 低於同業 20% |
| **技術面** | 趨勢 | 12% | 完美多頭 (P > EMA20 > SMA50 > SMA200) |
| | 動量 | 10% | RSI 超賣反彈 · MACD 金叉 |
| | 波動率 | 3% | 年化波動 < 20% 加分 |
| | 市場情緒 | 10% | TV 買入 > 65% · 分析師共識 |
| **Smart Money** | 內部人動向 | 8% | 近 90 天淨買入 > 賣出 1.5× |
| | EPS 紀錄 | 7% | 連 4 季超預期 |
| **宏觀** | 宏觀環境 | 5% | VIX < 20 且 10Y < 4% = risk-on |
| | 相對強弱 | 5% | 30d & 90d Alpha vs SPY 同時 > 0 |

**共識判定**：加權分數 > 0.2 → 🟢 BULLISH · < −0.2 → 🔴 BEARISH · 中間 → 🟡 NEUTRAL  
**信心度** = `max(多頭數, 空頭數) / 總信號數 × 100%`

---

## 🛡️ 反幻覺四層防護

| 層 | 機制 |
|---|---|
| **① 數據層** | 每欄位獨立驗證，缺失即標 `N/A`，禁止推測填充 |
| **② Prompt 層** | System Prompt 嚴格約束「只使用 Context 數據」，Prompt Caching 確保一致性 |
| **③ Context 層** | 結構化 JSON 注入、標明來源，AI 無法憑空編造 |
| **④ 輸出層** | 原始數據與 AI 分析同步展示，使用者可交叉驗證每項判斷 |

---

## 📐 系統架構

```
使用者 ─▶ /report AAPL
           │
           ▼
┌─────────────────────────────────────────────┐
│ 📡 10+ 數據源並行抓取（asyncio.gather）       │
│   Finnhub · FMP · yfinance · TradingView     │
│   Tavily · History · Peer · Analyst          │
│   Insider · EPS · Macro(VIX/10Y)             │
└─────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────┐
│ 🧮 12 維度量化信號引擎（純規則）              │
│   基本面 4 · 技術面 4 · Smart Money 2         │
│   宏觀 2 → 加權共識                           │
└─────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────┐
│ 🤖 Claude 四觀點深度分析                      │
│   價值 · 成長 · 技術 · 風險管理               │
│   (Prompt Caching · temperature=0.3)         │
└─────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────┐
│ 📊 HTML 報告 + 📈 K 線圖 + 🎮 互動按鈕        │
└─────────────────────────────────────────────┘
```

---

## 📁 專案結構

```
stock-analysis-telegram-bot/
├── main.py                  # 進入點（polling / webhook + Graceful Shutdown）
├── config.py                # 環境變數管理
├── requirements.txt         # 依賴清單（需 Python 3.10+）
├── Procfile · zbpack.json   # Zeabur 部署設定
│
├── fetchers/                # 📡 並行抓取層
│   ├── finnhub_fetcher.py              # 即時股價
│   ├── fmp_fetcher.py                  # 基本面（主）+ 批次報價
│   ├── yfinance_fetcher.py             # 基本面（備援）
│   ├── tavily_fetcher.py               # 新聞搜尋
│   ├── tradingview_fetcher.py          # 技術指標
│   ├── history_fetcher.py              # 歷史回測 + 支撐壓力 + Alpha
│   ├── peer_fetcher.py                 # 同業比較
│   ├── analyst_fetcher.py              # 分析師評級 + 目標價
│   ├── insider_fetcher.py              # 內部人交易
│   ├── earnings_surprise_fetcher.py    # EPS 驚喜紀錄
│   └── macro_fetcher.py                # VIX / 10Y / risk regime
│
├── analyzer/
│   └── anthropic_analyzer.py           # 🤖 Claude 四觀點分析
│
├── app/                     # 📰 Newsletter 管線
│   ├── pipeline.py
│   └── ai/{planner,writer}.py
│
├── bot/
│   └── telegram_bot.py                 # 💬 指令 + 回調 + HTML parse mode
│
└── utils/
    ├── ai_client.py                    # 🧩 Anthropic 共用 client + Prompt Caching
    ├── signals.py                      # 🧮 12 維度量化信號引擎
    ├── formatter.py                    # 🎨 HTML 報告組裝
    ├── chart.py                        # 📈 mplfinance K 線圖
    ├── cache.py                        # 💾 LRU 分層快取（raw 30m / report 5m）
    ├── retry.py                        # 🔁 指數退避重試
    ├── database.py                     # 🗃 SQLite 自選股 + 查詢歷史
    ├── rate_limiter.py                 # ⏱ 滑動窗口 per-user 限流
    └── health.py                       # 🏥 HTTP /health 端點
```

---

## ⚙️ 環境變數

<details open>
<summary><b>必要</b></summary>

| 變數 | 說明 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather 提供的 Bot Token |
| `FINNHUB_API_KEY` | Finnhub 即時股價 API |
| `FMP_API_KEY` | Financial Modeling Prep 基本面 API |
| `TAVILY_API_KEY` | Tavily 新聞搜尋 API |
| `ANTHROPIC_API_KEY` | Anthropic Claude API |

</details>

<details>
<summary><b>選用</b></summary>

| 變數 | 預設 | 說明 |
|---|---|---|
| `APP_ENV` | `production` | `dev` / `staging` / `production`（影響 log 格式） |
| `BOT_MODE` | `polling` | `polling` / `webhook` |
| `WEBHOOK_URL` | — | webhook 模式必填 |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | analyzer / writer 主模型，可選 opus / haiku |
| `ANTHROPIC_PLANNER_MODEL` | `claude-haiku-4-5-20251001` | Newsletter planner 用（純 JSON 結構規劃，Haiku 即可勝任，成本約 1/10） |
| `HEALTH_ENABLED` | `true` | `/health` HTTP 端點開關 |
| `HEALTH_PORT` | `8080` | HTTP 端口 |
| `RATE_LIMIT_PER_MINUTE` | `5` | 每用戶每分鐘請求上限 |
| `CACHE_TTL` | `300` | 報告快取秒數 |
| `DB_PATH` | `bot_data.db` | SQLite 檔案路徑 |
| `PEER_COMPARISON_ENABLED` | `true` | 啟用同業比較 |
| `HISTORY_ENABLED` | `true` | 啟用歷史回測 |
| `NEWSLETTER_ENABLED` | `true` | 啟動時生成日報（目前僅 log，未推送） |

</details>

<details>
<summary><b>模型選擇建議</b></summary>

| 模型 | 場景 |
|---|---|
| `claude-sonnet-4-6` | **推薦**，品質 / 成本平衡 |
| `claude-opus-4-6` | 最高品質分析，成本較高 |
| `claude-haiku-4-5-20251001` | 最快速度，適合高頻使用 |

</details>

---

## 🔧 技術棧

| 用途 | 技術 |
|---|---|
| 語言 | Python 3.10+ |
| Bot 框架 | `python-telegram-bot` 22.x |
| AI | Anthropic Claude（`anthropic>=0.52.0` + Prompt Caching） |
| 即時股價 | Finnhub |
| 基本面 | FMP（主） / yfinance（備援） |
| 技術指標 | TradingView-TA |
| 新聞 | Tavily |
| K 線圖 | mplfinance |
| 非同步 | `asyncio` + `asyncio.to_thread` |
| 儲存 | SQLite（WAL 模式） |
| 部署 | Zeabur |

### 關鍵設計決策

- **FMP primary → yfinance fallback**：FMP 付費穩定為主力，yfinance 免費但限流時無縫備援
- **Claude 三重節流**：
  - **Prompt Caching**：`system` prompt ≈ 2KB，第 2 次起 input token 折扣 ≈ 70%
  - **Compact JSON context**：`separators=(",",":")` 移除縮排空白，再省 25–35% input tokens
  - **噪音欄位過濾**：18 個對「解讀」零貢獻的欄位（`business_summary` / `description` / `logo_url` / `cusip` / `isin` / `cik` 等）送 AI 前剔除，formatter 仍正常顯示給使用者
  - **Planner → Haiku**：Newsletter 規劃步驟（純 JSON 結構）改用 Haiku 4.5，成本約一個量級
  - **`max_tokens` 收斂**：analyzer 4000→2800、writer 3000→2200、planner 2000→1200
- **Telegram HTML parse mode**：取代 legacy Markdown，對 `&` / `_` / URL 破版容忍度遠高
- **規則引擎 + LLM 分工**：純 Python 先算信號共識，LLM 只負責「解讀與交叉驗證」，降低幻覺
- **三層快取**：raw 30 分（API 數據）/ report 5 分（成品報告）/ watchlist 120 秒（per-user 報價結果）
- **Per-user rate limit + Semaphore(3)**：防單用戶濫用 + 全域並發上限

---

## 📝 修改歷程

### v5.2 — Watchlist 儀表板 v2 · Claude 三重節流 (2026-04-24)

**自選股強化**
- `/watchlist` 加入 **120 秒 per-user 結果快取**（LRU max 200）— 短時重覆敲指令不再重抓 FMP，命中時顯示 `⚡Ns 快取`
- **52w 位置改為視覺化迷你長條** `▌▌▌░░░░░`（watchlist 8 字元 / scan 6 字元）— 一眼看出貴 / 便宜
- **📅 財報臨近警示**：FMP `earnings_announcement` ≤7 天觸發，`/watchlist` 顯示「📅 財報臨近：AAPL(3天) · TSLA(5天)」摘要列＋每列 `📅N天` 標籤；`/scan` 列入 🚨 警示區
- **🔄 強刷按鈕**：`wl_refresh` callback 繞過快取重抓 FMP

**Claude API 費用精簡**（預估省 30–50% input tokens）
- analyzer / planner / writer **全改 compact JSON**（`separators=(",", ":")`，移除縮排空白）
- analyzer 新增 **`_AI_NOISE_KEYS` 過濾 18 個欄位**（`business_summary` / `description` / `logo_url` / `website` / `address` / `cusip` / `isin` / `cik` …），送 AI 前剔除，formatter 仍正常顯示
- 新增 **`ANTHROPIC_PLANNER_MODEL`**（預設 `claude-haiku-4-5-20251001`），Newsletter planner 改 Haiku
- **`max_tokens` 收斂**：analyzer 4000→2800、writer 3000→2200、planner 2000→1200

### v5.1 — 自選股快覽強化（2026-04-23）

- `/watchlist` 加入總覽列（漲跌數 / 平均 %）、強弱排序、👑 最強 / ⚠️ 最弱 highlight
- `/scan` 重構為三段：🚨 警示 / 🟢 上漲 / 🔴 下跌 — 警示自動分組（RSI 超買/賣 · TV 強買/賣 · 52w 高/低 · 量爆）
- `fetch_fmp_batch_prices` 擴充回傳欄位（52w 高低 / 50d 200d 均線 / 量能 / 市值 / earnings）— 零額外 API 成本

### v5.0 — Prompt Cache · 12 維度信號 · HTML 模式 (2026-04-23)

**AI 層**
- Anthropic **Prompt Caching** 上線：analyzer / planner / writer 共用 client，system prompt 走 `cache_control=ephemeral`，第 2 次起 input token 約 70% 折扣

**量化信號**
- 從 **8 維擴為 12 維**：新增「內部人動向 / EPS 紀錄 / 宏觀環境 / 相對強弱」四訊號（先前抓取但未納入加權的數據全部接入），`WEIGHTS` 重平衡

**Telegram UX**
- Markdown → **HTML parse mode**：新聞 URL / `&` 字元破版徹底解決
- 新增 `/help`、`/chart`、`/compare` 三個指令
- `setMyCommands` 註冊左下角指令選單
- Loading 期間發 `TYPING` / `UPLOAD_PHOTO` ChatAction
- 快取命中時 `disable_notification=True`，避免打擾

**程式碼品質**
- 重構 `_execute_analysis(chat_id, ticker, bot)`，移除 InlineKeyboard 回調的 fake-update hack
- `Semaphore.locked()` 取代私有 `_value` API
- 清除死碼 `_TICKER_PATTERN`

### v4.1 — 五大優化升級 (2026-04-23)

- **K 線圖生成**：mplfinance 暗色主題，60 日 MA5/20/60
- **API 指數退避重試**：所有 fetcher 自動重試 1 次、2s 退避
- **財報日曆提醒**：下次財報日 + 倒數天數（7 天內 ⚠️）
- **InlineKeyboard 互動**：「加入自選股」「重新分析」按鈕
- **分層 LRU 快取**：raw 30 分 / report 5 分

### v4.0 — Anthropic Claude 整合 (2026-03-23)

OpenAI → Anthropic Claude；200K context window；`system` 參數獨立於 messages，指令遵循度更佳

<details>
<summary>📜 v1.0 – v3.0 歷史版本</summary>

### v3.0 — 全面功能擴展 (2026-03-13)
- 歷史回測（7/30/60/90d 報酬 + 年化波動）
- 支撐壓力位（20/60 日高低點 + 動態均線）
- 同業比較（自動找同產業 4 家）
- ETF 支援（SPY / QQQ / VOO）
- 自選股、Webhook 模式、SQLite WAL、Per-user 限流、`/health` 端點、結構化 JSON log、Graceful Shutdown

### v2.1 — 三角色深度優化 v2 (2026-03-13)
- PEG 框架、均線排列、布林 / Stochastic / ATR、Short Ratio / 機構持股

### v2.0 — 三角色深度優化 (2026-03-12)
- 成交量 / 成長性 / 量化評分、Markdown 安全、並發控制

### v1.x — 初版 (2026-03-10 – 11)
- 基礎架構 4 數據源 + AI + 反幻覺四層、Zeabur 部署、Markdown bug 修復

</details>

---

## 🔮 Roadmap

### 📊 分析能力
- [ ] **多大師人格化 agents**（Buffett / Munger / Graham / Lynch / Wood / Burry…）— ai-hedge-fund 風格
- [ ] **`/backtest`** — 歷史信號回測驗證（過去 1 年 BULLISH 共識後 N 日報酬分布）
- [ ] **選擇權資料**（Put/Call Ratio、IV rank、ATM straddle implied move）
- [ ] **DCF / Damodaran 估值** — 合理價 vs 現價 upside
- [ ] **產業輪動**（XLK / XLF / XLE / XLV 相對強弱）
- [ ] **13F 機構持股變化**（Berkshire / ARK 增減持）

### 💬 UX / 互動
- [ ] **Progressive disclosure**：Verdict Card → 分頁展開（基本面 / 技術面 / Smart Money / 信號 / 新聞 / AI）
- [ ] **Telegram Mini App** — 自選股管理 Web UI
- [ ] **定時推送** — 盤前 / 盤後自動推個股報告
- [ ] **i18n** — 英文 UI

### ⚙️ 工程
- [ ] Redis 快取（替換記憶體 LRU，支援多實例）
- [ ] PostgreSQL（替換 SQLite，支援大規模）
- [ ] `aiosqlite` 重構 DB 層（解除全域 lock）
- [ ] GitHub Actions CI + 單元測試（優先補 `utils/signals.py`）

---

## ⚠️ 免責聲明

本工具僅供教育與研究用途，所有分析報告**不構成投資建議**。投資有風險，請自行判斷。

## 📄 License

[MIT](LICENSE) © jack926509
