"""Resolução de entidades: de URL/nome para a empresa certa.

A v1 deduplicava por host ("url:github.com"), o que juntava todo projeto do
GitHub numa linha só. A v2 usa site_key: domínio registrável para sites
próprios, host + caminho para plataformas compartilhadas e nome normalizado
quando não há URL. Heurística sem dependência externa — cobre os casos que
aparecem nas fontes, não a Public Suffix List inteira.
"""
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app import db

# Plataformas onde o caminho identifica o produto: host -> nº de segmentos.
PLATFORM_PATHS = {
    "github.com": 2, "gitlab.com": 2, "bitbucket.org": 2, "codeberg.org": 2,
    "huggingface.co": 2, "producthunt.com": 2, "ycombinator.com": 2,
    "linkedin.com": 2, "reddit.com": 2, "npmjs.com": 2, "pypi.org": 2,
    "wordpress.org": 2, "x.com": 1, "twitter.com": 1, "youtube.com": 1,
    "medium.com": 1, "apps.shopify.com": 1, "figma.com": 3,
    "chromewebstore.google.com": 3, "chrome.google.com": 4, "addons.mozilla.org": 4,
    "marketplace.visualstudio.com": 2, "notion.so": 1, "docs.google.com": 3,
    "news.ycombinator.com": 1, "loom.com": 2, "gumroad.com": 2,
}

# Sufixos compartilhados: cada subdomínio é um produto diferente.
SHARED_SUFFIXES = (
    "github.io", "gitlab.io", "vercel.app", "netlify.app", "herokuapp.com",
    "pages.dev", "workers.dev", "web.app", "firebaseapp.com", "notion.site",
    "substack.com", "itch.io", "glitch.me", "replit.app", "repl.co", "fly.dev",
    "onrender.com", "streamlit.app", "framer.website", "framer.app", "carrd.co",
    "webflow.io", "bubbleapps.io", "wixsite.com", "blogspot.com", "wordpress.com",
    "tumblr.com", "gitbook.io", "readthedocs.io", "mintlify.app", "lovable.app",
    "myshopify.com", "square.site", "ghost.io", "super.site", "softr.app",
    "glide.page", "gumroad.com", "azurewebsites.net", "cloudfront.net",
    "amazonaws.com", "appspot.com", "deno.dev", "railway.app", "up.railway.app",
    "beehiiv.com", "typedream.app", "durable.co", "hashnode.dev", "bsky.social",
)

# TLDs de dois níveis comuns nas fontes.
MULTI_PART_TLDS = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "com.br", "net.br", "org.br",
    "app.br", "com.au", "net.au", "org.au", "co.nz", "co.jp", "ne.jp", "or.jp",
    "co.kr", "com.mx", "com.ar", "com.co", "co.in", "co.za", "com.sg", "com.tr",
    "com.cn", "com.hk", "com.tw", "co.il", "com.pe", "com.uy", "com.ec",
    "co.id", "com.my", "com.ph", "com.vn", "com.pk", "com.ng", "co.ke", "com.eg",
    "com.sa", "com.es", "com.pt", "co.th",
}

TRACKING_PARAMS = re.compile(r"^(utm_.*|ref|ref_src|fbclid|gclid|mc_cid|mc_eid|via|source|s)$")


def normalize_name(text: str | None) -> str:
    """Nome em ascii minúsculo sem pontuação — chave de fallback."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def clean_url(url: str | None) -> str | None:
    """Normaliza a URL: esquema, host minúsculo, sem fragmento nem parâmetros de rastreio."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not host or "." not in host:
        return None
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query)
                       if not TRACKING_PARAMS.match(k.lower())])
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower() or "https", host, path, query, ""))


def registrable_domain(host: str) -> str:
    """'app.acme.com.br' -> 'acme.com.br'; 'foo.vercel.app' -> 'foo.vercel.app'."""
    host = host.lower().removeprefix("www.")
    for suffix in SHARED_SUFFIXES:
        if host == suffix:
            return host
        if host.endswith("." + suffix):
            label = host[: -len(suffix) - 1].split(".")[-1]
            return f"{label}.{suffix}"
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_PART_TLDS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _is_shared(domain: str) -> bool:
    return any(domain == s or domain.endswith("." + s) for s in SHARED_SUFFIXES)


def site_key(url: str | None, name: str | None) -> tuple[str, str | None]:
    """Chave da empresa e domínio registrável (None em plataformas compartilhadas)."""
    fallback = ("name:" + normalize_name(name), None)
    cleaned = clean_url(url)
    if not cleaned:
        return fallback
    parts = urlsplit(cleaned)
    host = (parts.hostname or "").removeprefix("www.")
    segments = [s for s in parts.path.lower().split("/") if s]

    if host == "play.google.com":
        app_id = dict(parse_qsl(parts.query)).get("id")
        return (f"play.google.com/{app_id.lower()}", None) if app_id else fallback
    if host == "apps.apple.com":
        app_id = next((s for s in segments if re.fullmatch(r"id\d+", s)), None)
        return (f"apps.apple.com/{app_id}", None) if app_id else fallback
    if host in PLATFORM_PATHS:
        n = PLATFORM_PATHS[host]
        if host == "news.ycombinator.com":
            item = dict(parse_qsl(parts.query)).get("id")
            return (f"news.ycombinator.com/{item}", None) if item else fallback
        if len(segments) < 1:
            return fallback
        return (host + "/" + "/".join(segments[:n]), None)

    domain = registrable_domain(host)
    if _is_shared(domain):
        return domain, None
    return domain, domain


def ingest(conn, items: list[dict], max_new: int = 150) -> dict:
    """Grava os itens coletados como empresas + eventos + observações.

    Retorna contagens: new, seen, cross_source, skipped (novos acima do limite).
    """
    stats = {"new": 0, "seen": 0, "cross_source": 0, "skipped": 0}
    for item in items:
        if not item.get("name"):
            continue
        key, domain = site_key(item.get("url"), item["name"])
        name_key = normalize_name(item["name"])
        company = None
        if item.get("external_id"):
            # o site_key pode ter mudado (redirect resolvido); o evento não muda
            known = db.company_for_event(conn, item["source"], str(item["external_id"]))
            company = db.get_company(conn, known) if known else None
        if company is None:
            company = db.find_company_by_key(conn, key)
        if company is None and not key.startswith("name:"):
            # empresa conhecida só pelo nome ganha o domínio agora
            by_name = db.find_company_by_key(conn, "name:" + name_key)
            if by_name is not None:
                db.update_company_site(conn, by_name["id"], key, domain, clean_url(item.get("url")))
                company = db.get_company(conn, by_name["id"])
        if company is None and key.startswith("name:") and name_key:
            company = db.find_company_by_name_key(conn, name_key)

        if company is None:
            if stats["new"] >= max_new:
                stats["skipped"] += 1
                continue
            company_id = db.insert_company(conn, key, domain, item["name"],
                                           item.get("tagline"), clean_url(item.get("url")))
            stats["new"] += 1
        else:
            company_id = company["id"]
            previous = set(db.company_sources(conn, company_id))
            if previous and item["source"] not in previous:
                stats["cross_source"] += 1
            else:
                stats["seen"] += 1
            db.touch_company(conn, company_id, item.get("tagline"))

        db.add_event(conn, company_id, item)
        if item.get("observations"):
            db.add_observations(conn, company_id, [
                {**o, "source": o.get("source") or item["source"]}
                for o in item["observations"]])
    conn.commit()
    return stats
