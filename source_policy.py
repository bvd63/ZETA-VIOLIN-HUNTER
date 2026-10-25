"""Sources explicitly removed by the owner, across discovery and delivery."""
from urllib.parse import urlsplit

DISABLED_SCRAPERS = frozenset({"Yahoo Auctions JP"})


def is_disabled_source(url: str) -> bool:
    try:
        parts = urlsplit(url or "")
        host = (parts.hostname or "").lower()
    except ValueError:
        return False  # Invalid URLs are handled by the ordinary URL validator.
    if host == "yahoo.co.jp" or host.endswith(".yahoo.co.jp"):
        return True
    path = parts.path.lower()
    if host in ("buyee.jp", "www.buyee.jp") and path.startswith("/item/yahoo/"):
        return True
    if host in ("zenmarket.jp", "www.zenmarket.jp") and (
            "/yahoo" in path or path.endswith("/auction.aspx")):
        return True
    return False
