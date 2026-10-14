"""Safety checks for browser probes: never load the production Pychess site."""

from urllib.parse import urlsplit


def require_nonproduction_https(url: str) -> str:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        raise ValueError("probe requires an HTTPS page without URL credentials")
    if hostname == "pychess.org" or hostname.endswith(".pychess.org"):
        raise ValueError("browser probes must not access production pychess.org")
    return url
