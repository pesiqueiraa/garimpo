"""Sondagem do site: home + página de preços, texto limpo e features baratas (sem LLM).

O texto alimenta a extração de fatos; as features viram observações (preço
mínimo, plano grátis, motion de venda) e o hash dos preços permite notar
mudança de preço nas revisitas. Leitura única e leve: uma home e no máximo
uma página de preços por empresa, corpo limitado a ~1,5 MB.
"""
import hashlib
import html
import re
from urllib.parse import urljoin, urlsplit

import requests

from app.config import get
from app.resolve import registrable_domain

CAP_BYTES = 1_500_000
TEXT_MAX = 4000
PRICING_MAX = 2000
DEFAULT_UA = "garimpo-pessoal/2.0 (projeto de estudo)"

HREF_RE = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
PRICING_PATH_RE = re.compile(r"pricing|plans|price|precos|preços|planos", re.I)
# dólar americano: ignora R$/A$/C$ e valores de captação ("$1.5M", "$5 million")
PRICE_RE = re.compile(r"(?<![A-Za-z])(?:US\$|USD\s?|\$)\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?"
                      r"(?!\s?(?:[kKmMbB]|million|billion|thousand)\b)(?!\d|[.,]\d)")
FREE_RE = re.compile(r"\bfree\s+(?:plan|forever|tier)\b|\bforever\s+free\b|\$\s?0(?:\.00?)?(?!\.?\d|,\d)"
                     r"|gr[aá]tis|plano\s+gratuito", re.I)
SELF_SERVE_RE = re.compile(r"\bsign\s?-?up\b|\bget\s+started\b|\bstart\s+(?:for\s+)?free\b"
                           r"|\btry\s+(?:it\s+)?(?:for\s+)?free\b|\bstart\s+(?:your\s+)?free\s+trial\b"
                           r"|\bcreate\s+(?:an?\s+|your\s+)?(?:free\s+)?account\b|\bcomece\s+gr[aá]tis\b", re.I)
SALES_RE = re.compile(r"\bbook\s+a\s+(?:demo|call)\b|\bcontact\s+sales\b|\brequest\s+a\s+demo\b"
                      r"|\btalk\s+to\s+(?:sales|an?\s+expert)\b|\bschedule\s+a\s+demo\b|\bget\s+a\s+demo\b"
                      r"|\bfale\s+com\s+(?:vendas|um\s+especialista)\b", re.I)
INTEGRATIONS = ("stripe", "paypal", "shopify", "whatsapp", "twilio", "slack", "hubspot", "salesforce",
                "quickbooks", "xero", "zapier", "google calendar", "outlook", "zoom", "notion",
                "intercom", "mailchimp")


def html_to_text(raw: str) -> str:
    """Remove comentários, scripts, estilos e tags; junta os espaços."""
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw or "")
    raw = re.sub(r"(?is)<(script|style|noscript|svg|template|iframe)\b.*?</\1\s*>", " ", raw)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text).strip()


def _title(raw: str) -> str | None:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw or "")
    if not m:
        return None
    return re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()[:300] or None


def _lang(raw: str) -> str | None:
    m = re.search(r"""(?i)<html\b[^>]*?\blang\s*=\s*["']?([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?)""",
                  raw or "")
    return m.group(1) if m else None


def _read(r) -> str:
    """Lê o corpo até CAP_BYTES e decodifica (charset do header, do <meta> ou utf-8)."""
    try:
        if hasattr(r, "iter_content"):
            chunks, size = [], 0
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                chunks.append(chunk)
                size += len(chunk)
                if size >= CAP_BYTES:
                    break
            raw = b"".join(chunks)[:CAP_BYTES]
        else:
            raw = (r.content or b"")[:CAP_BYTES]
    finally:
        close = getattr(r, "close", None)
        if callable(close):
            close()
    encoding = getattr(r, "encoding", None)
    if not encoding or encoding.lower() in ("iso-8859-1", "latin-1"):
        m = re.search(rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9_-]+)""", raw[:4096], re.I)
        encoding = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _get(url: str, cfg: dict | None) -> tuple[str, str, bool] | None:
    """(url final, html, é html?) ou None se falhar."""
    ua = get(cfg or {}, "fontes.sinais.user_agent", DEFAULT_UA)
    timeout = float(get(cfg or {}, "fontes.sondagem.timeout_s", 10))
    try:
        r = requests.get(url, headers={"User-Agent": ua, "Accept": "text/html,*/*;q=0.8"},
                         timeout=timeout, allow_redirects=True, stream=True)
    except Exception as e:
        print(f"[site] {url} falhou: {e}")
        return None
    final_url = getattr(r, "url", None) or url
    if r.status_code >= 400:
        print(f"[site] {url} HTTP {r.status_code}")
        close = getattr(r, "close", None)
        if callable(close):
            close()
        # redirect resolvido para outro domínio que barra robôs: o destino ainda vale
        return (final_url, "", False) if not _same_site(final_url, url) else None
    ctype = ((getattr(r, "headers", None) or {}).get("Content-Type") or "").lower()
    if ctype and not any(t in ctype for t in ("html", "text", "xml")):
        close = getattr(r, "close", None)
        if callable(close):
            close()
        return final_url, "", False
    try:
        return final_url, _read(r), True
    except Exception as e:
        print(f"[site] {url} corpo ilegível: {e}")
        return final_url, "", False


def _same_site(a: str, b: str) -> bool:
    ha, hb = urlsplit(a).hostname or "", urlsplit(b).hostname or ""
    return bool(ha and hb) and registrable_domain(ha) == registrable_domain(hb)


def find_pricing_link(raw: str, base_url: str) -> str | None:
    """Link do próprio site cujo caminho fala de preço/plano; o de caminho mais curto ganha."""
    found = []
    for m in HREF_RE.finditer(raw or ""):
        href = html.unescape((m.group(1) or m.group(2) or m.group(3) or "").strip())
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        if not absolute.startswith("http") or not _same_site(absolute, base_url):
            continue
        parts = urlsplit(absolute)
        if PRICING_PATH_RE.search(parts.path + "?" + parts.query):
            found.append((len(parts.path), absolute))
    return min(found)[1] if found else None


def pricing_hash(pricing_text: str | None) -> str | None:
    """sha256 (16 hex) do texto de preços normalizado — muda quando o preço muda."""
    norm = re.sub(r"\s+", " ", (pricing_text or "").lower()).strip()
    return hashlib.sha256(norm.encode()).hexdigest()[:16] if norm else None


def min_price(text: str | None) -> float | None:
    """Menor valor em dólar > 0 e < 100 mil (ignora '$1.5M' e afins)."""
    values = []
    for m in PRICE_RE.finditer(text or ""):
        value = float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else ""))
        if 0 < value < 100_000:
            values.append(value)
    return min(values) if values else None


def features(text: str, pricing_text: str, raw: str = "") -> dict:
    both = f"{text} {pricing_text}"
    self_serve, sales = bool(SELF_SERVE_RE.search(both)), bool(SALES_RE.search(both))
    motion = ("misto" if self_serve and sales else "self_serve" if self_serve
              else "sales_led" if sales else "desconhecido")
    low = both.lower()
    price = min_price(pricing_text)
    return {
        "price_min_usd": price if price is not None else min_price(text),
        "has_free_plan": bool(FREE_RE.search(both)),
        "sales_motion": motion,
        "integrations": [n for n in INTEGRATIONS if re.search(r"\b" + re.escape(n) + r"\b", low)],
        "lang": _lang(raw),
    }


def probe(url: str, cfg: dict | None = None) -> dict | None:
    """Lê a home e a página de preços. None se a home não abriu."""
    try:
        home = _get(url, cfg)
        if home is None:
            return None
        final_url, raw, is_html = home
        text = html_to_text(raw)[:TEXT_MAX] if is_html else ""
        pricing_url, pricing_text = None, ""
        if is_html:
            if PRICING_PATH_RE.search(urlsplit(final_url).path):
                pricing_url, pricing_text = final_url, html_to_text(raw)[:PRICING_MAX]
            else:
                link = find_pricing_link(raw, final_url)
                parts = urlsplit(final_url)
                candidate = link or f"{parts.scheme}://{parts.netloc}/pricing"
                page = _get(candidate, cfg)
                if page is not None and page[2]:
                    pricing_url, pricing_text = page[0], html_to_text(page[1])[:PRICING_MAX]
        return {"final_url": final_url, "title": _title(raw), "text": text,
                "pricing_url": pricing_url, "pricing_text": pricing_text,
                "pricing_hash": pricing_hash(pricing_text),
                "features": features(text, pricing_text, raw) if is_html else {}}
    except Exception as e:
        print(f"[site] sondagem de {url} falhou: {e}")
        return None


def observations(page: dict | None) -> list[dict]:
    """Features do site como observações (só o que foi de fato lido)."""
    if not page or not (page.get("text") or page.get("pricing_text")):
        return []
    f = page.get("features") or {}
    out = []
    if f.get("price_min_usd") is not None:
        out.append({"metric": "price_min_usd", "value_num": f["price_min_usd"], "value_text": None})
    if "has_free_plan" in f:
        out.append({"metric": "has_free_plan", "value_num": 1 if f["has_free_plan"] else 0,
                    "value_text": None})
    if f.get("sales_motion") and f["sales_motion"] != "desconhecido":
        out.append({"metric": "sales_motion", "value_num": None, "value_text": f["sales_motion"]})
    if page.get("pricing_hash"):
        out.append({"metric": "pricing_hash", "value_num": None, "value_text": page["pricing_hash"]})
    return [{**o, "source": "site"} for o in out]
