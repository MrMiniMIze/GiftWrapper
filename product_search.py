import httpx
import time
from duckduckgo_search import DDGS

_SKIP_DOMAINS = frozenset({
    "duckduckgo.com", "bing.com", "google.com",
    "facebook.com", "twitter.com", "x.com",
    "instagram.com", "tiktok.com", "pinterest.com",
})

_UNVERIFIED = (
    "URL could not be verified. "
    "Do NOT present this suggestion to the user. "
    "Call search_product again with a completely different product query."
)


def search_product(query: str, budget: float) -> dict:
    """Search DuckDuckGo for a product and verify the top link resolves."""
    results = _ddg_search(f"{query} buy")
    if results is None:
        return {"url": None, "title": None, "resolved": False, "note": _UNVERIFIED}

    for result in results:
        url = result.get("href", "")
        title = result.get("title", "")
        if any(skip in _extract_domain(url) for skip in _SKIP_DOMAINS):
            continue
        if _check_url(url):
            return {"url": url, "title": title, "resolved": True}

    return {"url": None, "title": None, "resolved": False, "note": _UNVERIFIED}


def _ddg_search(query: str, max_results: int = 5) -> list | None:
    """Run a DuckDuckGo search with exponential backoff on errors."""
    for attempt in range(3):
        try:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results))
        except Exception:
            if attempt < 2:
                time.sleep(2 ** attempt)  # 1 s, then 2 s
    return None


def _extract_domain(url: str) -> str:
    parts = url.split("/")
    return parts[2] if len(parts) > 2 else ""


def _check_url(url: str, timeout: float = 5.0) -> bool:
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.head(url)
            if resp.status_code == 405:
                resp = client.get(url)
            return resp.status_code < 400
    except Exception:
        return False
