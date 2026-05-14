"""
Slack 訊息格式化工具

職責：
1. 將既有 HTML 報告（utils/formatter.py 的輸出）轉成 Slack mrkdwn
2. 提供 Block Kit builders（header / section / divider / actions / context）
3. 處理 Slack 訊息與 block 字數上限：
   - section.text.text  ≤ 3000 chars
   - 單則 message blocks ≤ 50

設計重點：
- 純函式、可單獨測試，bot 層只組裝 block list
- 不依賴 slack_sdk 型別，輸出純 dict 給 chat_postMessage 用
"""

from __future__ import annotations

import html
import re
from typing import Iterable

# ── Slack 限制常數 ──
SECTION_TEXT_LIMIT = 2900   # 留 100 字 buffer 給格式字元
BLOCKS_PER_MESSAGE = 45     # Slack 上限 50，預留給 header/divider/actions
MAX_TEXT_PER_MSG = 39000    # message.text fallback 上限 40k，留 buffer


# ──────────────────────────────────────────────
# HTML → Slack mrkdwn
# ──────────────────────────────────────────────

_A_TAG_RE = re.compile(
    r"<a\s+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
    re.DOTALL | re.IGNORECASE,
)
_INLINE_TAG_PATTERNS = [
    # <b>x</b> / <strong>x</strong>  →  *
    (re.compile(r"</?(?:b|strong)>", re.IGNORECASE), "*"),
    # <i>x</i> / <em>x</em>  →  _
    (re.compile(r"</?(?:i|em)>", re.IGNORECASE), "_"),
    # <code>x</code>  →  `
    (re.compile(r"</?code>", re.IGNORECASE), "`"),
    # <pre>x</pre>  →  ```
    (re.compile(r"</?pre>", re.IGNORECASE), "```"),
]
# 剝除殘存 HTML tag：要求 `<` 後接字母（避免誤吃 Slack <URL|label> 連結語法）
_STRIP_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
# vault placeholder：純 ASCII 控制字元，不會在自然文字裡出現
_PLACEHOLDER_RE = re.compile(r"\x00L(\d+)\x00")
# 用來在 _safe_escape_specials 階段跳過已成形的 Slack 連結語法
_LINK_TOKEN_RE = re.compile(r"<[^<>\n]+?\|[^<>\n]+?>|<https?://[^<>\s]+>")


def _link_label_safe(label: str) -> str:
    """Slack 連結 label 內必須轉義的字元（& < > |）。"""
    return (
        label.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "&#124;")
    )


def html_to_mrkdwn(text: str) -> str:
    """Telegram HTML → Slack mrkdwn。

    Pipeline：
      1. 把 <a href="URL">label</a> 抽出存到 vault，原位插入唯一 ASCII 佔位符
      2. <b>/<i>/<code>/<pre> → mrkdwn 對應字元
      3. 剝除殘存 HTML tag
      4. html.unescape（&amp; → &）
      5. 對純文字節點安全轉義 Slack 保留字元（& < >）
      6. 把佔位符換回 <URL|label>
    """
    if not text:
        return ""

    links: list[tuple[str, str]] = []

    def _a_to_placeholder(m: re.Match) -> str:
        url = html.unescape(m.group(1))
        label_raw = re.sub(_STRIP_TAG_RE, "", m.group(2)).strip()
        label = html.unescape(label_raw) if label_raw else url
        idx = len(links)
        links.append((url, label))
        return f"\x00L{idx}\x00"

    out = _A_TAG_RE.sub(_a_to_placeholder, text)

    for pat, repl in _INLINE_TAG_PATTERNS:
        out = pat.sub(repl, out)
    out = _STRIP_TAG_RE.sub("", out)
    out = html.unescape(out)
    out = _safe_escape_specials(out)

    def _placeholder_to_link(m: re.Match) -> str:
        url, label = links[int(m.group(1))]
        return f"<{url}|{_link_label_safe(label)}>"

    out = _PLACEHOLDER_RE.sub(_placeholder_to_link, out)
    return out


def _safe_escape_specials(text: str) -> str:
    """跳過 <URL|label> 與 <URL>，把其他位置裸露的 < > & 轉成 Slack entity。"""
    parts: list[str] = []
    cursor = 0
    for m in _LINK_TOKEN_RE.finditer(text):
        plain = text[cursor:m.start()]
        plain = plain.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        parts.append(plain)
        parts.append(m.group(0))
        cursor = m.end()
    tail = text[cursor:]
    tail = tail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    parts.append(tail)
    return "".join(parts)


def escape_mrkdwn(text: str) -> str:
    """純文字（非 HTML 來源）轉成 Slack 安全 mrkdwn。"""
    if text is None:
        return ""
    return _safe_escape_specials(str(text))


# ──────────────────────────────────────────────
# 訊息切片：大段 mrkdwn → 多個 section 區塊
# ──────────────────────────────────────────────


_SPLIT_PREFERENCES = (
    "━━━━",   # 粗分隔線（formatter.DIV_BOLD）
    "═════",  # 雙線
    "─ ─ ─",  # 細分隔線（formatter.DIV）
    "\n\n",   # 段落
    "\n",     # 行
)


def chunk_mrkdwn(text: str, limit: int = SECTION_TEXT_LIMIT) -> list[str]:
    """把長 mrkdwn 切成 ≤ limit 的片段；盡量切在分隔線/段落邊界。"""
    if len(text) <= limit:
        return [text] if text else []

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_pos = -1
        for sep in _SPLIT_PREFERENCES:
            split_pos = remaining.rfind(sep, 0, limit)
            if split_pos != -1:
                break
        if split_pos == -1:
            split_pos = limit
        chunks.append(remaining[:split_pos].rstrip())
        remaining = remaining[split_pos:].lstrip("\n")
    return [c for c in chunks if c]


# ──────────────────────────────────────────────
# Block Kit builders
# ──────────────────────────────────────────────


def section(text: str) -> dict:
    """單個 mrkdwn section block。"""
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text[:SECTION_TEXT_LIMIT] or " "},
    }


def header(text: str) -> dict:
    """header block。plain_text 上限 150。"""
    safe = text.replace("\n", " ").strip()
    if len(safe) > 150:
        safe = safe[:147] + "…"
    return {"type": "header", "text": {"type": "plain_text", "text": safe, "emoji": True}}


def divider() -> dict:
    return {"type": "divider"}


def context(elements: Iterable[str]) -> dict:
    """灰色小字 context（每個 element ≤ 75 chars 視覺較佳）。"""
    return {
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": e} for e in elements if e
        ],
    }


def button(text: str, action_id: str, value: str = "",
           style: str | None = None, url: str | None = None) -> dict:
    """單顆 button element（給 actions block 用）。"""
    btn: dict = {
        "type": "button",
        "text": {"type": "plain_text", "text": text, "emoji": True},
        "action_id": action_id,
    }
    if value:
        btn["value"] = value
    if style in ("primary", "danger"):
        btn["style"] = style
    if url:
        btn["url"] = url
    return btn


def actions(buttons: list[dict], block_id: str | None = None) -> dict:
    """actions block；單個 actions block 最多 25 顆 elements。"""
    blk: dict = {"type": "actions", "elements": buttons[:25]}
    if block_id:
        blk["block_id"] = block_id
    return blk


def mrkdwn_to_blocks(text: str) -> list[dict]:
    """把長 mrkdwn 文字切片並包成多個 section blocks。"""
    return [section(chunk) for chunk in chunk_mrkdwn(text)]


def split_blocks_into_messages(blocks: list[dict]) -> list[list[dict]]:
    """把過長的 block 列表切成多則訊息（每則 ≤ BLOCKS_PER_MESSAGE blocks）。"""
    out: list[list[dict]] = []
    for i in range(0, len(blocks), BLOCKS_PER_MESSAGE):
        out.append(blocks[i:i + BLOCKS_PER_MESSAGE])
    return out


def fallback_text(blocks: list[dict]) -> str:
    """從 blocks 提取純文字 fallback（給通知預覽用）。Slack 規範 text 欄位 ≤ 40k。"""
    parts: list[str] = []
    for blk in blocks:
        t = blk.get("type")
        if t == "section":
            parts.append(blk.get("text", {}).get("text", ""))
        elif t == "header":
            parts.append(blk.get("text", {}).get("text", ""))
        elif t == "context":
            for el in blk.get("elements", []):
                parts.append(el.get("text", ""))
    out = "\n".join(parts).strip()
    if len(out) > MAX_TEXT_PER_MSG:
        out = out[:MAX_TEXT_PER_MSG - 1] + "…"
    return out or " "
