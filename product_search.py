import httpx
from duckduckgo_search import DDGS
import time


def search_product(query: str, budget: float) -> dict:
    """Search DuckDuckGo for a product and verify the top link resolves."""
    search_query = f"{query} buy"
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(search_query, max_results=5))
    except Exception as e:
        return {"url": None, "title": None, "resolved": False, "error": str(e)}

    for result in results:
        url = result.get("href", "")
        title = result.get("title", "")
        # Skip ad/redirect aggregators
        if any(skip in url for skip in ["duckduckgo.com", "bing.com", "google.com"]):
            continue
        resolved = _check_url(url)
        if resolved:
            return {"url": url, "title": title, "resolved": True}

    # Return best guess even if unverified
    if results:
        return {
            "url": results[0].get("href"),
            "title": results[0].get("title"),
            "resolved": False,
        }
    return {"url": None, "title": None, "resolved": False}


def _check_url(url: str, timeout: float = 5.0) -> bool:
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.head(url)
            if resp.status_code == 405:
                resp = client.get(url)
            return resp.status_code < 400
    except Exception:
        return False
