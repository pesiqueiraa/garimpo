"""TrustMRR: startups com receita verificada (conectada ao Stripe/afins) — a prova de tração mais forte.

ATENÇÃO: parser frágil, feito na base do melhor esforço e a ser conferido na
primeira execução real. O TrustMRR não tem API oficial; lemos as páginas
públicas (listagem → /startup/<slug>) com pausa de 1,5 s e extraímos os números
com regex tolerante, tanto de JSON embutido (simples ou escapado, como no
payload RSC do Next.js: \\"mrr\\":12345) quanto do texto visível ("$12.3k MRR").
Suposições a verificar: nomes das chaves JSON, valores em dólares (não em
centavos, salvo chave *Cents) e o formato do og:title.
"""
import html
import re
import time
from urllib.parse import urlsplit

import requests

from app.config import get

BASE = "https://trustmrr.com"
TIMEOUT = 20
DEFAULT_UA = "garimpo-pessoal/2.0 (projeto de estudo)"

SLUG_RE = re.compile(r"/startup/([A-Za-z0-9][A-Za-z0-9_.%-]{0,120})")
Q = r'\\*"'  # aspas, escapadas (uma ou mais vezes) ou não
NUM = r'\\*"?\s*(-?\d+(?:\.\d+)?)'

MRR_KEYS = ("mrr", "currentMrr", "mrrUsd", "monthlyRecurringRevenue", "mrrCents")
REV30_KEYS = ("last30DaysRevenue", "revenueLast30Days", "last30Days", "revenue30d",
              "last_30_days_revenue", "revenue30Days", "last30DaysRevenueCents")
# "revenue"/"totalRevenue" puro costuma ser o acumulado: não serve como receita de 30 dias
GROWTH_KEYS = ("growthMoM", "growthMom", "momGrowth", "mrrGrowth", "growth30d", "growth")
ASKING_KEYS = ("askingPrice", "salePrice", "price_for_sale", "askingPriceCents")
SALE_FLAGS = ("onSale", "forSale", "isForSale", "for_sale")

# links externos que nunca são o site da startup (domínio ou sufixo de domínio)
NOT_WEBSITE = ("trustmrr.com", "x.com", "twitter.com", "stripe.com", "google.com", "gstatic.com",
               "googleapis.com", "googletagmanager.com", "facebook.com", "linkedin.com",
               "youtube.com", "instagram.com", "tiktok.com", "vercel.com", "vercel.app",
               "cloudflare.com", "schema.org", "w3.org", "github.com", "jsdelivr.net",
               "unpkg.com", "marclou.com", "lemonsqueezy.com", "paddle.com", "polar.sh",
               "plausible.io", "datafa.st", "codefa.st", "shipfa.st", "producthunt.com",
               "ycombinator.com", "twimg.com", "unsplash.com", "gravatar.com", "googleusercontent.com")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js", ".woff2")


def _fetch(url: str, cfg: dict) -> str | None:
    ua = get(cfg, "fontes.sinais.user_agent", DEFAULT_UA)
    try:
        r = requests.get(url, headers={"User-Agent": ua}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text or ""
    except Exception as e:
        print(f"[trustmrr] {url} falhou: {e}")
        return None


def listing_slugs(page: str) -> list[str]:
    """Slugs únicos de /startup/<slug>, na ordem da página."""
    seen: list[str] = []
    for m in SLUG_RE.finditer(page or ""):
        slug = m.group(1).rstrip(".").strip("\\")
        if slug and slug not in seen:
            seen.append(slug)
    return seen


def _money(number: str, suffix: str | None = None) -> float | None:
    try:
        value = float(number.replace(",", ""))
    except (AttributeError, ValueError):
        return None
    mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get((suffix or "").lower(), 1)
    return value * mult


def _json_num(page: str, keys: tuple[str, ...]) -> float | None:
    """Primeiro valor numérico de uma das chaves (em ordem de preferência)."""
    for key in keys:
        m = re.search(Q + re.escape(key) + Q + r"\s*:\s*" + NUM, page)
        if m:
            value = float(m.group(1))
            return value / 100 if key.lower().endswith("cents") else value
    return None


def _json_str(page: str, key: str) -> str | None:
    m = re.search(Q + re.escape(key) + Q + r'\s*:\s*\\*"((?:[^"\\]|\\(?!\\*"))*)\\*"', page)
    return m.group(1).replace("\\/", "/") if m else None


def _visible_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", page)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", page))
    return re.sub(r"\s+", " ", text).strip()


MONEY = r"\$\s?(\d[\d,]*(?:\.\d+)?)\s?([kKmMbB])?\b"


def _text_mrr(text: str) -> float | None:
    m = (re.search(MONEY + r"\s*(?:/\s*mo(?:nth)?\s*)?MRR\b", text)
         or re.search(r"\bMRR\b\s*:?\s*" + MONEY, text))
    return _money(m.group(1), m.group(2)) if m else None


def _text_rev30(text: str) -> float | None:
    m = (re.search(r"(?i)last\s*30\s*days?[^$\d]{0,40}" + MONEY, text)
         or re.search(MONEY + r"[^$\d]{0,30}last\s*30\s*days?", text, re.I))
    return _money(m.group(1), m.group(2)) if m else None


def _text_growth(text: str) -> float | None:
    m = re.search(r"([+\-−]?\d+(?:\.\d+)?)\s?%\s*(?:MoM|month[- ]over[- ]month|growth)", text, re.I)
    return float(m.group(1).replace("−", "-")) if m else None


def _name(page: str, slug: str) -> str:
    m = (re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', page, re.I)
         or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title', page, re.I)
         or re.search(r"(?is)<title[^>]*>(.*?)</title>", page))
    raw = html.unescape(m.group(1)).strip() if m else ""
    for part in re.split(r"\s+[|–—-]\s+|:\s+", raw):
        part = re.sub(r"(?i)['’]s\s+(verified\s+)?(revenue|mrr)\b.*$", "", part)
        part = re.sub(r"(?i)\btrustmrr\b|\b(verified\s+)?(revenue|mrr)\b.*$", "", part).strip(" -|:")
        if part and not part.startswith("$"):
            return part[:80]
    return slug.replace("-", " ").title()[:80]


def _description(page: str) -> str | None:
    m = (re.search(r'<meta[^>]+(?:name|property)=["\'](?:og:)?description["\'][^>]+content=["\']([^"\']+)',
                   page, re.I))
    return html.unescape(m.group(1)).strip()[:300] if m else None


def _ok_website(url: str | None) -> bool:
    if not url or not url.startswith("http"):
        return False
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    blocked = any(host == b or host.endswith("." + b) for b in NOT_WEBSITE)
    return bool(host) and "." in host and not blocked and not parts.path.lower().endswith(IMAGE_EXT)


def _website(page: str) -> str | None:
    for key in ("website", "websiteUrl", "url"):
        for m in re.finditer(Q + key + Q + r'\s*:\s*\\*"(https?://[^"\\\s]+)', page):
            if _ok_website(m.group(1)):
                return m.group(1)
    for m in re.finditer(r'href=["\'](https?://[^"\'\s>]+)', page):
        if _ok_website(m.group(1)):
            return html.unescape(m.group(1))
    return None


def parse_startup(page: str, slug: str) -> dict:
    """Extrai nome, site, MRR, receita 30d, crescimento e se está à venda (None = não achou)."""
    page = page or ""
    text = _visible_text(page)
    mrr = _json_num(page, MRR_KEYS)
    if mrr is None:
        mrr = _text_mrr(text)
    rev30 = _json_num(page, REV30_KEYS)
    if rev30 is None:
        rev30 = _text_rev30(text)
    growth = _json_num(page, GROWTH_KEYS)
    if growth is None:
        growth = _text_growth(text)
    asking = _json_num(page, ASKING_KEYS)
    if asking is None:
        m = re.search(r"(?i)asking\s*price[^$]{0,30}" + MONEY, text)
        asking = _money(m.group(1), m.group(2)) if m else None
    flagged = any(re.search(Q + f + Q + r"\s*:\s*true", page) for f in SALE_FLAGS)
    # só preço pedido ou flag explícita: "for sale" solto aparece no menu de toda página
    for_sale = bool((asking and asking > 0) or flagged)
    return {"name": _name(page, slug), "tagline": _description(page), "website": _website(page),
            "category": _json_str(page, "category"), "mrr": mrr, "revenue_30d": rev30,
            "growth": growth, "for_sale": for_sale, "asking_price": asking}


def _item(slug: str, data: dict) -> dict:
    obs = [{"metric": "mrr_usd", "value_num": data["mrr"], "value_text": None}]
    if data.get("revenue_30d") is not None:
        obs.append({"metric": "revenue_30d_usd", "value_num": data["revenue_30d"], "value_text": None})
    if data.get("growth") is not None:
        obs.append({"metric": "growth_mom_pct", "value_num": data["growth"], "value_text": None})
    asking = f"US${data['asking_price']:,.0f}" if data.get("asking_price") else None
    obs.append({"metric": "for_sale", "value_num": 1 if data["for_sale"] else 0, "value_text": asking})
    return {"name": data["name"], "tagline": data.get("tagline"), "url": data.get("website"),
            "source": "trustmrr", "kind": "listing", "external_id": slug,
            "source_url": f"{BASE}/startup/{slug}", "points": 0,
            "category": data.get("category"), "observations": obs,
            "payload": {"mrr": data["mrr"], "asking_price": data.get("asking_price")}}


def collect(cfg: dict) -> list[dict]:
    """Listagens → até per_run páginas de startup → itens com MRR ≥ min_mrr_usd."""
    try:
        urls = get(cfg, "fontes.trustmrr.listing_urls", [BASE + "/"]) or []
        per_run = int(get(cfg, "fontes.trustmrr.per_run", 20))
        min_mrr = float(get(cfg, "fontes.trustmrr.min_mrr_usd", 1000))
        pause = float(get(cfg, "fontes.trustmrr.pause_s", 1.5))
        slugs: list[str] = []
        for i, url in enumerate(urls):
            if i:
                time.sleep(pause)
            page = _fetch(url, cfg)
            for slug in listing_slugs(page or ""):
                if slug not in slugs:
                    slugs.append(slug)
        if not slugs:
            print("[trustmrr] nenhuma startup na listagem (layout mudou?)")
            return []
        items, no_mrr = [], 0
        for slug in slugs[:per_run]:
            time.sleep(pause)
            page = _fetch(f"{BASE}/startup/{slug}", cfg)
            if page is None:
                continue
            data = parse_startup(page, slug)
            if data["mrr"] is None:
                no_mrr += 1
                continue
            if data["mrr"] >= min_mrr:
                items.append(_item(slug, data))
        print(f"[trustmrr] {len(items)} itens ({len(slugs)} na listagem, {no_mrr} sem MRR legível)")
        return items
    except Exception as e:
        print(f"[trustmrr] erro inesperado: {e}")
        return []
