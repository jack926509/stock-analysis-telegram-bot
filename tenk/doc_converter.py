"""
SEC 財報 HTM → markdown 轉換。

策略：BeautifulSoup 處理 iXBRL（SEC 標準格式，唯一保證可用路徑）。
LlamaParse / MarkItDown 為選用 fallback，套件未裝則跳過，
為部署輕量化已從 requirements 移除。
"""

import re
from pathlib import Path

from config import Config

BASE_DIR = Path(Config.TENK_CACHE_DIR)


def convert_to_markdown(doc_path: str | Path) -> str:
    doc_path = Path(doc_path)
    cache_path = BASE_DIR / "md" / doc_path.with_suffix(".md").name
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    md_text = (
        _llamaparse(doc_path)
        or _html_to_text_fallback(doc_path)
        or _markitdown_fallback(doc_path)
    )
    if md_text is None:
        raise RuntimeError(
            f"無法轉換 {doc_path.name}：非 SEC HTM 格式且未安裝 markitdown / llama-parse"
        )

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(md_text, encoding="utf-8")
    return md_text


def _llamaparse(doc_path: Path) -> str | None:
    if not Config.LLAMA_CLOUD_API_KEY:
        return None
    try:
        from llama_parse import LlamaParse
    except ImportError:
        return None
    try:
        docs = LlamaParse(
            api_key=Config.LLAMA_CLOUD_API_KEY,
            result_type="markdown",
            verbose=False,
        ).load_data(str(doc_path))
        md_text = "\n\n".join(doc.text for doc in docs)
        if len(re.findall(r"^#{1,3}\s*item\s+\d", md_text, re.MULTILINE | re.IGNORECASE)) < 3:
            return None
        return md_text
    except Exception:
        return None


def _strip_ixbrl(html: str) -> str:
    """剝除 SEC iXBRL metadata，留下可讀文字節點。"""
    html = re.sub(r"<ix:header>.*?</ix:header>", "", html, flags=re.DOTALL)
    html = re.sub(r"<ix:references>.*?</ix:references>", "", html, flags=re.DOTALL)
    html = re.sub(r"<ix:resources>.*?</ix:resources>", "", html, flags=re.DOTALL)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<ix:nonNumeric[^>]*>(.*?)</ix:nonNumeric>", r"\1", html, flags=re.DOTALL)
    html = re.sub(r"<ix:nonFraction[^>]*>(.*?)</ix:nonFraction>", r"\1", html, flags=re.DOTALL)
    html = re.sub(r"</?ix:[^>]+>", "", html)
    html = re.sub(r"<xbrli:[^>]+>.*?</xbrli:[^>]+>", "", html, flags=re.DOTALL)
    html = re.sub(r"</?xbrli:[^>]*>", "", html)
    html = re.sub(r"</?link:[^>]*>", "", html)
    return html


def _html_to_text_fallback(doc_path: Path) -> str | None:
    """SEC HTM/HTML 主要解析路徑（BeautifulSoup）。"""
    if doc_path.suffix.lower() not in (".htm", ".html"):
        return None
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None

    html = _strip_ixbrl(doc_path.read_text(encoding="utf-8", errors="replace"))
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["style", "script", "head"]):
        tag.decompose()

    lines = [line.strip() for line in soup.get_text(separator="\n").splitlines()]
    result = "\n\n".join(line for line in lines if line)

    # 沒抓到至少 2 個 Item 標題視為解析失敗
    if len(re.findall(r"(?i)item\s+\d", result)) < 2:
        return None
    return result


def _markitdown_fallback(doc_path: Path) -> str | None:
    try:
        from markitdown import MarkItDown
    except ImportError:
        return None

    suffix = doc_path.suffix.lower()
    if suffix in (".htm", ".html"):
        import tempfile

        html = re.sub(
            r'<(div|span|a)\s+id="([^"]+)">\s*</\1>',
            r'<\1 id="\2">[anchor:\2]</\1>',
            doc_path.read_text(encoding="utf-8", errors="replace"),
        )
        with tempfile.NamedTemporaryFile(
            suffix=".htm", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(html)
            tmp_path = f.name
        result = MarkItDown().convert(tmp_path).text_content
        Path(tmp_path).unlink(missing_ok=True)
        return result

    return MarkItDown().convert(str(doc_path)).text_content
