"""Fetchers: cada función habla con una fuente y devuelve List[NewsItem].

Conjunto del prototipo (todo gratis y verificado):
  - arXiv API .............. preprints (Tier 1)
  - PubMed E-utilities ..... biomedicina (Tier 1)
  - Hacker News (Algolia) .. discusión tech (Tier 3)
  - Google News RSS ........ descubrimiento amplio (Tier 3)
  - RSS/Atom genérico ...... para añadir medios después
"""
from __future__ import annotations

import base64
import calendar
import logging
import random as _random
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import requests
from dateutil import parser as dateparser

from .config import Source, load_social_config
from .models import NewsItem, clean_text as _clean_text

log = logging.getLogger("sibylla")

UA = "Sibylla/0.1 (news research aggregator; +https://example.local)"
_session = requests.Session()
_session.headers.update({"User-Agent": UA})


# --- Consultas por tema -----------------------------------------------------
# news = consulta booleana para Google News (admite OR y comillas)
# hn   = consulta simple de texto para Hacker News (Algolia no entiende booleanos)
# arxiv= categoría de arXiv (si aplica)
# pubmed = True si el tema es biomédico (se consulta PubMed)
TOPIC_CONFIG: dict[str, dict] = {
    "ai":             {"news": '"artificial intelligence" OR "machine learning" OR "large language model"', "hn": "artificial intelligence", "arxiv": "cs.AI"},
    "computing":      {"news": '"computer science" OR semiconductor OR "quantum computing" OR chip', "hn": "computing", "arxiv": "cs.AR"},
    "space":          {"news": 'NASA OR astronomy OR "space exploration" OR telescope', "hn": "space astronomy", "arxiv": "astro-ph.EP"},
    "physics":        {"news": 'physics OR quantum OR "particle physics"', "hn": "physics", "arxiv": "physics.gen-ph"},
    "biotech":        {"news": 'biotechnology OR CRISPR OR "gene editing" OR genomics', "hn": "CRISPR biotech", "arxiv": "q-bio.GN", "pubmed": True},
    "medicine":       {"news": '"clinical trial" OR "medical breakthrough" OR "new treatment" OR "drug approval" OR "gene therapy"', "hn": "medicine health", "pubmed": True},
    "neuroscience":   {"news": 'neuroscience OR "brain research" OR neurology', "hn": "neuroscience brain", "arxiv": "q-bio.NC", "pubmed": True},
    "climate":        {"news": '"climate change" OR "global warming" OR "renewable energy"', "hn": "climate change"},
    "energy":         {"news": 'nuclear fusion OR "grid battery" OR "renewable energy" OR "solar power"', "hn": "fusion battery energy"},
    "general_science":{"news": 'science discovery research breakthrough', "hn": "science"},
    "general_tech":   {"news": 'technology OR software OR startup', "hn": "technology"},
    # Nacional (Chile): NO se filtra por palabras clave (todo lo que publican
    # estos medios ya es noticia nacional). Config vacía a propósito: las fuentes
    # nacionales son RSS pass-through + google_news_nacional (consulta `site:`),
    # ambas atadas al tema vía `topics: [nacional]` en sources.yaml.
    "nacional":       {},
}

# Palabras clave por tema (bilingüe ES/EN, sin tildes) para filtrar el ruido de
# los agregadores y clasificar los medios. Las cortas (<=3) usan límite de palabra
# para no casar dentro de otra (p. ej. 'ai' NO debe casar con 'airport').
TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ai": ("ai", "artificial intelligence", "inteligencia artificial", "machine learning",
           "aprendizaje automatico", "deep learning", "neural", "neuronal", "llm",
           "language model", "modelo de lenguaje", "gpt", "chatbot", "openai", "anthropic",
           "deepmind", "algorithm", "algoritmo", "model", "modelo", "generativ"),
    "computing": ("computing", "comput", "semiconductor", "chip", "processor", "procesador",
                  "gpu", "quantum comput", "computacion cuantica", "software", "hardware",
                  "data center", "centro de datos"),
    "space": ("nasa", "esa", "space", "espacio", "astronom", "telescope", "telescopio",
              "galaxy", "galaxia", "planet", "planeta", "mars", "marte", "moon", "luna",
              "rocket", "cohete", "satellite", "satelite", "cosmic", "cosmico", "asteroid",
              "spacex", "orbit"),
    "physics": ("physic", "fisic", "quantum", "cuantic", "particle", "particula", "photon",
                "foton", "laser", "fusion", "relativ"),
    "biotech": ("biotech", "biotecnolog", "crispr", "genetic", "genom", "dna", "adn", "rna",
                "arn", "protein", "proteina", "stem cell", "celula madre", "enzyme", "enzima",
                "gene editing", "edicion genetica"),
    "medicine": ("medic", "clinic", "trial", "ensayo", "drug", "farmac", "therap", "terap",
                 "treatment", "tratamiento", "patient", "pacient", "disease", "enfermedad",
                 "cancer", "vaccine", "vacuna", "surgery", "cirug", "diagnos", "health",
                 "salud", "fda", "tumor", "alzheimer", "diabet", "antibod", "anticuerp",
                 "infection", "infeccion", "neuro"),
    "neuroscience": ("neuro", "brain", "cerebr", "cogniti", "neuron", "synap", "sinap",
                     "dementia", "demencia", "alzheimer"),
    "climate": ("climate", "clima", "warming", "calentamiento", "carbon", "emission",
                "emision", "renewable", "renovable", "solar", "greenhouse", "invernadero",
                "co2", "temperature", "temperatura"),
    "energy": ("energy", "energia", "battery", "bateria", "fusion", "solar", "nuclear",
               "grid", "hydrogen", "hidrogeno", "renewable", "renovable"),
    "general_science": ("science", "ciencia", "research", "investigac", "study", "estudio",
                        "discover", "descubr", "scientist", "cientific", "experiment"),
    "general_tech": ("tech", "tecnolog", "software", "app", "startup", "gadget", "device",
                     "dispositivo", "chip", "robot"),
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def pick_lens(network: str, lenses: list[dict], seed_str: str) -> dict:
    """Elige una lente por azar ponderado, sembrado por (fecha, network).

    Devuelve el dict de la lente elegida. Si `lenses` está vacío, devuelve {}.
    """
    if not lenses:
        return {}
    rng = _random.Random(seed_str + "|" + network)
    total = sum(l.get("weight", 1) for l in lenses)
    target = rng.uniform(0, total)
    acc = 0.0
    for l in lenses:
        acc += l.get("weight", 1)
        if target <= acc:
            return l
    return lenses[-1]


def is_relevant(title: str, topic: str) -> bool:
    """True si el texto contiene alguna palabra clave del tema (ES/EN, sin tildes)."""
    kws = TOPIC_KEYWORDS.get(topic)
    if not kws:
        return True
    low = _strip_accents(title.lower())
    for k in kws:
        if len(k) <= 3:
            if re.search(rf"\b{re.escape(k)}\b", low):
                return True
        elif k in low:
            return True
    return False


def classify_topics(title: str, summary: str, topics: list[str]) -> list[str]:
    """Temas (de los pedidos) con los que el ítem es relevante (para medios RSS)."""
    text = f"{title}. {summary}"
    return [t for t in topics if is_relevant(text, t)]


# --- helpers de fecha y HTTP ------------------------------------------------
def _from_struct(st) -> Optional[datetime]:
    """time.struct_time (UTC, como da feedparser) -> datetime aware."""
    if not st:
        return None
    return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return dateparser.parse(s, fuzzy=True)
    except (ValueError, OverflowError):
        return None


def _get(url: str, params: dict | None = None, timeout: int = 25,
         headers: dict | None = None) -> requests.Response:
    if headers:
        r = requests.get(url, params=params, timeout=timeout,
                         headers={**dict(_session.headers), **headers})
    else:
        r = _session.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r


# --- resolución de URLs de Google News --------------------------------------
_GNEWS_TOKEN_RE = re.compile(r"/(?:articles|read)/([A-Za-z0-9_\-]+)")
_URL_IN_BYTES_RE = re.compile(rb"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")


def resolve_google_news_url(url: str) -> str:
    """Best-effort: saca la URL real del medio del enlace-redirección de Google News.

    El token tras /articles/ es base64 de un protobuf que (en el formato CBMi…)
    lleva la URL del medio como texto. Si no se puede decodificar, se devuelve la
    URL de Google original (que igualmente funciona al hacer clic).
    """
    if "news.google.com" not in url:
        return url
    m = _GNEWS_TOKEN_RE.search(url)
    if not m:
        return url
    token = m.group(1)
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except Exception:  # noqa: BLE001
        return url
    found = _URL_IN_BYTES_RE.search(raw)
    if not found:
        return url
    cand = found.group(0).decode("latin-1").split("\\")[0].rstrip("\x01\x02\x03 ")
    if cand.startswith("http") and "google.com" not in cand:
        return cand
    return url


# --- fetchers concretos -----------------------------------------------------
def fetch_arxiv(source: Source, category: str, limit: int) -> list[NewsItem]:
    url = "https://export.arxiv.org/api/query"
    params = {
        "search_query": f"cat:{category}",
        "start": 0,
        "max_results": limit,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    feed = feedparser.parse(_get(url, params=params).content)
    items = []
    for e in feed.entries[:limit]:
        items.append(NewsItem(
            title=e.get("title", ""),
            url=e.get("link", ""),
            source_id=source.id,
            source_name=source.name,
            tier=source.tier,
            published=_from_struct(e.get("published_parsed")),
            summary=e.get("summary", ""),
            authors=[a.get("name", "") for a in e.get("authors", [])],
            extra={"kind": "preprint", "category": category},
        ))
    return items


def fetch_pubmed(source: Source, query: str, limit: int) -> list[NewsItem]:
    import os
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    common = {"db": "pubmed"}
    if os.getenv("NCBI_API_KEY"):
        common["api_key"] = os.environ["NCBI_API_KEY"]
    es = _get(f"{base}/esearch.fcgi", params={
        **common, "term": query, "retmax": limit, "sort": "date", "retmode": "json",
    }).json()
    ids = es.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    summ = _get(f"{base}/esummary.fcgi", params={
        **common, "id": ",".join(ids), "retmode": "json",
    }).json().get("result", {})
    items = []
    for pmid in summ.get("uids", []):
        d = summ.get(pmid, {})
        journal = d.get("fulljournalname") or d.get("source", "")
        items.append(NewsItem(
            title=d.get("title", ""),
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            source_id=source.id,
            source_name=f"{source.name} › {journal}".strip(" ›"),
            tier=source.tier,
            published=_parse_date(d.get("sortpubdate") or d.get("pubdate", "")),
            summary="",
            extra={"pmid": pmid, "journal": journal, "kind": "paper"},
        ))
    return items


def fetch_hackernews(source: Source, query: str, limit: int, min_points: int = 15) -> list[NewsItem]:
    r = _get("https://hn.algolia.com/api/v1/search_by_date", params={
        "query": query, "tags": "story", "hitsPerPage": limit,
        "numericFilters": f"points>{min_points}",
    }).json()
    items = []
    for h in r.get("hits", []):
        oid = h.get("objectID")
        hn_url = f"https://news.ycombinator.com/item?id={oid}"
        items.append(NewsItem(
            title=h.get("title") or h.get("story_title") or "",
            url=h.get("url") or hn_url,
            source_id=source.id,
            source_name=source.name,
            tier=source.tier,
            published=_parse_date(h.get("created_at")),
            summary="",
            extra={"points": h.get("points"), "num_comments": h.get("num_comments"), "hn_url": hn_url},
        ))
    return items


def fetch_googlenews(source: Source, query: str, limit: int,
                     lang: str = "en-US", country: str = "US") -> list[NewsItem]:
    ceid = f"{country}:{lang.split('-')[0]}"
    r = _get("https://news.google.com/rss/search", params={
        "q": query, "hl": lang, "gl": country, "ceid": ceid,
    })
    feed = feedparser.parse(r.content)
    items = []
    for e in feed.entries[:limit]:
        publisher = ""
        if e.get("source"):
            publisher = e.source.get("title", "")
        items.append(NewsItem(
            title=e.get("title", ""),
            url=resolve_google_news_url(e.get("link", "")),
            source_id=source.id,
            source_name=f"{source.name} › {publisher}".strip(" ›"),
            tier=source.tier,
            published=_from_struct(e.get("published_parsed")),
            summary="",  # la descripción de Google News solo repite el título; no aporta
            extra={"publisher": publisher, "gnews_url": e.get("link", "")},
        ))
    return items


def fetch_generic_rss(source: Source, limit: int) -> list[NewsItem]:
    """RSS/Atom genérico para medios que se añadan más adelante."""
    if not source.url:
        return []
    feed = feedparser.parse(_get(source.url).content)
    items = []
    for pos, e in enumerate(feed.entries[:limit]):
        items.append(NewsItem(
            title=e.get("title", ""),
            url=e.get("link", ""),
            source_id=source.id,
            source_name=source.name,
            tier=source.tier,
            published=_from_struct(e.get("published_parsed") or e.get("updated_parsed")),
            summary=e.get("summary", ""),
            # `feed_pos` = orden en el feed (0 = titular del medio): señal de
            # prominencia editorial que aprovecha el score de la sección Nacional.
            extra={"feed_pos": pos},
        ))
    return items


# --- Mastodon / Fediverso (gratis, sin auth en instancias públicas) ---------
def fetch_mastodon(source: Source, lens: dict, limit: int) -> list[NewsItem]:
    """Mastodon: trends o hashtag según la lente. Sin auth en instancias públicas."""
    import os
    instance = os.getenv("MASTODON_INSTANCE", "mastodon.social")
    base = f"https://{instance}/api/v1"
    try:
        if lens.get("trend"):
            url = f"{base}/trends/statuses"
            params = {"limit": limit}
        else:
            tag = str(lens.get("mastodon_tag", "")).strip().lower().lstrip("#")
            if not tag:
                log.warning("  mastodon: lente sin mastodon_tag y sin trend; se omite")
                return []
            url = f"{base}/timelines/tag/{tag}"
            params = {"limit": limit}
        r = _get(url, params=params)
        data = r.json()
        posts = data if isinstance(data, list) else data.get("data", [])
    except Exception as ex:
        log.warning("  mastodon: fallo al consultar %s: %s", instance, ex)
        return []
    items = []
    for p in posts[:limit]:
        acc = p.get("account", {}) or {}
        username = acc.get("acct") or acc.get("username", "")
        author_url = acc.get("url", "")
        pid = p.get("id", "")
        post_url = p.get("url") or p.get("uri") or (f"{author_url}/{pid}" if author_url else "")
        text = _clean_text(p.get("content") or "")
        title = text[:90] + ("…" if len(text) > 90 else "") or f"post {pid}"
        created = p.get("created_at", "")
        is_repost = bool(p.get("reblog"))
        items.append(NewsItem(
            title=title,
            url=post_url,
            source_id=source.id,
            source_name=f"{source.name} › @{username}" if username else source.name,
            tier=source.tier,
            published=_parse_date(created),
            summary=text,
            extra={"kind": "post", "network": "mastodon",
                   "likes": p.get("favourites_count", 0),
                   "reposts": p.get("reblogs_count", 0),
                   "author": username, "is_repost": is_repost},
        ))
    log.info("  %-16s [lente:%s] -> %d posts", source.id,
             lens.get("name", "?"), len(items))
    return items


# --- Bluesky / AT Protocol (gratis, requiere app password) ------------------
_bluesky_jwt: str | None = None


def _bluesky_auth() -> str | None:
    """Autentica en Bluesky y devuelve el accessJwt. Cacheado por corrida."""
    global _bluesky_jwt
    if _bluesky_jwt:
        return _bluesky_jwt
    import os
    identifier = os.getenv("BLUESKY_IDENTIFIER")
    password = os.getenv("BLUESKY_APP_PASSWORD")
    if not (identifier and password):
        log.warning("  bluesky: falta BLUESKY_IDENTIFIER o BLUESKY_APP_PASSWORD en .env")
        return None
    try:
        r = requests.post(
            "https://bsky.social/xrpc/com.atproto.server.createSession",
            json={"identifier": identifier, "password": password},
            timeout=25,
        )
        r.raise_for_status()
        _bluesky_jwt = r.json().get("accessJwt")
        return _bluesky_jwt
    except Exception as ex:
        log.warning("  bluesky: fallo createSession: %s", ex)
        return None


def fetch_bluesky(source: Source, lens: dict, limit: int) -> list[NewsItem]:
    """Bluesky: feed What's Hot o searchPosts según la lente. Requiere auth."""
    jwt = _bluesky_auth()
    if not jwt:
        return []
    base_public = "https://public.api.bsky.app/xrpc"
    headers = {"Authorization": f"Bearer {jwt}"}
    try:
        if lens.get("trend"):
            # Feed "What's Hot": público, sin auth. DID verificado en vivo 2026-06-23.
            url = f"{base_public}/app.bsky.feed.getFeed"
            params = {
                "feed": "at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/whats-hot",
                "limit": limit,
            }
        else:
            query = str(lens.get("bluesky_query", "")).strip()
            if not query:
                log.warning("  bluesky: lente sin bluesky_query y sin trend; se omite")
                return []
            # searchPosts requiere el appview autenticado (api.bsky.app, no el público).
            url = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
            params = {"q": query, "sort": "top", "limit": limit}
        r = _get(url, params=params, timeout=25, headers=headers)
        data = r.json()
        raw_posts = data.get("feed", data.get("posts", [])) if isinstance(data, dict) else []
    except Exception as ex:
        log.warning("  bluesky: fallo API: %s", ex)
        return []
    items = []
    for entry in raw_posts[:limit]:
        post = entry.get("post", entry) if isinstance(entry, dict) else entry
        author = post.get("author", {}) or {}
        handle = author.get("handle", "")
        rkey_raw = str((post.get("uri") or "").split("/")[-1]) if post.get("uri") else ""
        tid = post.get("cid", "")
        post_url = f"https://bsky.app/profile/{handle}/post/{rkey_raw}" if handle and rkey_raw else ""
        text = (post.get("record", {}).get("text") if isinstance(post.get("record"), dict) else "") or ""
        title = text[:90] + ("…" if len(text) > 90 else "") or f"post {tid}"
        created = post.get("record", {}).get("createdAt", "") if isinstance(post.get("record"), dict) else ""
        items.append(NewsItem(
            title=title,
            url=post_url,
            source_id=source.id,
            source_name=f"{source.name} › @{handle}" if handle else source.name,
            tier=source.tier,
            published=_parse_date(created),
            summary=text,
            extra={"kind": "post", "network": "bluesky",
                   "likes": post.get("likeCount", 0),
                   "reposts": post.get("repostCount", 0),
                   "author": handle, "is_repost": False},
        ))
    log.info("  %-16s [lente:%s] -> %d posts", source.id,
             lens.get("name", "?"), len(items))
    return items


# --- Reddit (gratis, OAuth app-only) -----------------------------------------
_reddit_token: str | None = None


def _reddit_auth() -> str | None:
    """OAuth client_credentials → bearer token. Cacheado por corrida."""
    global _reddit_token
    if _reddit_token:
        return _reddit_token
    import os
    client_id = os.getenv("REDDIT_CLIENT_ID")
    secret = os.getenv("REDDIT_CLIENT_SECRET")
    if not (client_id and secret):
        log.warning("  reddit: falta REDDIT_CLIENT_ID o REDDIT_CLIENT_SECRET en .env")
        return None
    reddit_ua = os.getenv("REDDIT_USER_AGENT", UA)
    try:
        r = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=(client_id, secret),
            headers={"User-Agent": reddit_ua},
            timeout=25,
        )
        r.raise_for_status()
        _reddit_token = r.json().get("access_token")
        return _reddit_token
    except Exception as ex:
        log.warning("  reddit: fallo OAuth: %s", ex)
        return None


def fetch_reddit(source: Source, lens: dict, limit: int) -> list[NewsItem]:
    """Reddit: /r/all/hot o búsqueda en subs según la lente. OAuth app-only."""
    import os
    token = _reddit_auth()
    if not token:
        return []
    reddit_ua = os.getenv("REDDIT_USER_AGENT", UA)
    headers = {"Authorization": f"Bearer {token}", "User-Agent": reddit_ua}
    base = "https://oauth.reddit.com"
    try:
        if lens.get("trend"):
            url = f"{base}/r/all/hot"
            params = {"limit": limit}
            r = _get(url, params=params, timeout=25, headers=headers)
            data = r.json()
            raw_posts = data.get("data", {}).get("children", [])
        else:
            subs = lens.get("reddit_subs", [])
            if not subs:
                log.warning("  reddit: lente sin reddit_subs y sin trend; se omite")
                return []
            subreddit = "+".join(str(s).strip() for s in subs)
            query = str(lens.get("bluesky_query", "")).strip()
            url = f"{base}/r/{subreddit}/search"
            params = {"q": query, "sort": "hot", "limit": limit, "restrict_sr": "true"}
            r = _get(url, params=params, timeout=25, headers=headers)
            data = r.json()
            raw_posts = data.get("data", {}).get("children", [])
    except Exception as ex:
        log.warning("  reddit: fallo API: %s", ex)
        return []
    items = []
    for child in raw_posts[:limit]:
        d = child.get("data", {}) if isinstance(child, dict) else {}
        author = d.get("author", "")
        pid = d.get("id", "")
        permalink = d.get("permalink", "")
        post_url = f"https://www.reddit.com{permalink}" if permalink else ""
        title = d.get("title", "")
        text = d.get("selftext", "") or ""
        summary = text if text else title
        created = d.get("created_utc")
        items.append(NewsItem(
            title=title or f"post {pid}",
            url=post_url,
            source_id=source.id,
            source_name=f"{source.name} › u/{author}" if author else source.name,
            tier=source.tier,
            published=datetime.fromtimestamp(created, tz=timezone.utc) if created else None,
            summary=summary,
            extra={"kind": "post", "network": "reddit",
                   "likes": d.get("score", 0),
                   "reposts": 0,
                   "author": author, "is_repost": False,
                   "num_comments": d.get("num_comments", 0)},
        ))
    log.info("  %-16s [lente:%s] -> %d posts", source.id,
             lens.get("name", "?"), len(items))
    return items


# --- House posts: cuentas propias de Sibylla ---------------------------------
def _fetch_house_mastodon(handle: str) -> list[NewsItem]:
    """Feed de una cuenta Mastodon (incluye reposts/blogs)."""
    import os
    instance = os.getenv("MASTODON_INSTANCE", "mastodon.social")
    handle_clean = handle.lstrip("@").strip()
    base = f"https://{instance}/api/v1"
    # Resolver account id
    try:
        r = _get(f"{base}/accounts/lookup", params={"acct": handle_clean})
        acc = r.json()
        acc_id = acc.get("id")
        if not acc_id:
            return []
    except Exception as ex:
        log.warning("  house/mastodon: lookup falló para %s: %s", handle, ex)
        return []
    try:
        r = _get(f"{base}/accounts/{acc_id}/statuses",
                 params={"limit": 10, "exclude_replies": "true"})
        posts = r.json()
    except Exception as ex:
        log.warning("  house/mastodon: statuses falló para %s: %s", handle, ex)
        return []
    items = []
    for p in posts[:10]:
        username = acc.get("acct") or acc.get("username", "")
        pid = p.get("id", "")
        post_url = p.get("url") or p.get("uri") or ""
        text = _clean_text(p.get("content") or "")
        title = text[:90] + ("…" if len(text) > 90 else "") or f"post {pid}"
        created = p.get("created_at", "")
        is_repost = bool(p.get("reblog"))
        items.append(NewsItem(
            title=title,
            url=post_url,
            source_id="mastodon",
            source_name=f"Mastodon › @{username}",
            tier=3,
            published=_parse_date(created),
            summary=text,
            extra={"kind": "post", "network": "mastodon", "house": True,
                   "likes": p.get("favourites_count", 0),
                   "reposts": p.get("reblogs_count", 0),
                   "author": username, "is_repost": is_repost},
        ))
    return items


def _fetch_house_bluesky(handle: str) -> list[NewsItem]:
    """Feed de una cuenta Bluesky (incluye reposts nativos)."""
    jwt = _bluesky_auth()
    if not jwt:
        return []
    try:
        r = _get(
            "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed",
            params={"actor": handle, "limit": 10},
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=25,
        )
        data = r.json()
        feed = data.get("feed", [])
    except Exception as ex:
        log.warning("  house/bluesky: falló para %s: %s", handle, ex)
        return []
    items = []
    for entry in feed[:10]:
        post = entry.get("post", entry) if isinstance(entry, dict) else entry
        author = post.get("author", {}) or {}
        h = author.get("handle", "")
        rkey_raw = str((post.get("uri") or "").split("/")[-1]) if post.get("uri") else ""
        post_url = f"https://bsky.app/profile/{h}/post/{rkey_raw}" if h and rkey_raw else ""
        text = (post.get("record", {}).get("text") if isinstance(post.get("record"), dict) else "") or ""
        title = text[:90] + ("…" if len(text) > 90 else "") or "post"
        created = post.get("record", {}).get("createdAt", "") if isinstance(post.get("record"), dict) else ""
        items.append(NewsItem(
            title=title,
            url=post_url,
            source_id="bluesky",
            source_name=f"Bluesky › @{h}",
            tier=3,
            published=_parse_date(created),
            summary=text,
            extra={"kind": "post", "network": "bluesky", "house": True,
                   "likes": post.get("likeCount", 0),
                   "reposts": post.get("repostCount", 0),
                   "author": h, "is_repost": False},
        ))
    return items


def _fetch_house_reddit(handle: str) -> list[NewsItem]:
    """Historial de posts de una cuenta Reddit."""
    import os
    token = _reddit_auth()
    if not token:
        return []
    reddit_ua = os.getenv("REDDIT_USER_AGENT", UA)
    headers = {"Authorization": f"Bearer {token}", "User-Agent": reddit_ua}
    name = handle.lstrip("u/").strip().lstrip("/")
    try:
        r = _get(
            f"https://oauth.reddit.com/user/{name}/submitted",
            params={"sort": "new", "limit": 10},
            headers=headers,
            timeout=25,
        )
        data = r.json()
        raw_posts = data.get("data", {}).get("children", [])
    except Exception as ex:
        log.warning("  house/reddit: falló para %s: %s", handle, ex)
        return []
    items = []
    for child in raw_posts[:10]:
        d = child.get("data", {}) if isinstance(child, dict) else {}
        author = d.get("author", "")
        pid = d.get("id", "")
        permalink = d.get("permalink", "")
        post_url = f"https://www.reddit.com{permalink}" if permalink else ""
        title = d.get("title", "")
        text = d.get("selftext", "") or ""
        summary = text if text else title
        created = d.get("created_utc")
        items.append(NewsItem(
            title=title or f"post {pid}",
            url=post_url,
            source_id="reddit",
            source_name=f"Reddit › u/{author}",
            tier=3,
            published=datetime.fromtimestamp(created, tz=timezone.utc) if created else None,
            summary=summary,
            extra={"kind": "post", "network": "reddit", "house": True,
                   "likes": d.get("score", 0),
                   "reposts": 0,
                   "author": author, "is_repost": False,
                   "num_comments": d.get("num_comments", 0)},
        ))
    return items


def fetch_house_posts(accounts: list[dict]) -> list[NewsItem]:
    """Consulta el feed de cada cuenta propia, incluyendo reposts.

    `accounts`: [{"network": "bluesky", "handle": "sibylla.cl"}, ...]
    Devuelve ítems con `extra["house"]=True`. Cada cuenta que falla se aísla.
    """
    items: list[NewsItem] = []
    for acc in accounts:
        net = str(acc.get("network", "")).strip().lower()
        handle = str(acc.get("handle", "")).strip()
        if not net or not handle:
            continue
        try:
            if net == "mastodon":
                items.extend(_fetch_house_mastodon(handle))
            elif net == "bluesky":
                items.extend(_fetch_house_bluesky(handle))
            elif net == "reddit":
                items.extend(_fetch_house_reddit(handle))
            elif net == "x" or net == "x_twitter":
                # X house solo con --with-x (la fuente en sí no se fetchea sin él);
                # si se quiere, se implementa aparte. Por ahora, omitir sin error.
                pass
            else:
                log.warning("  house: red desconocida '%s' para %s; se omite", net, handle)
        except Exception as ex:  # noqa: BLE001
            log.warning("  house/%s: %s FALLÓ: %s", net, handle, ex)
    return items


# --- X / Twitter (DE PAGO: recent search con tope de presupuesto) -----------
def _x_usage_path():
    from .config import ROOT
    return ROOT / "data" / "x_usage.json"


def _x_load_usage() -> tuple[str, int]:
    import json
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        d = json.loads(_x_usage_path().read_text(encoding="utf-8"))
        if d.get("month") == month:
            return month, int(d.get("reads", 0))
    except Exception:  # noqa: BLE001
        pass
    return month, 0


def _x_save_usage(month: str, reads: int) -> None:
    import json
    p = _x_usage_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"month": month, "reads": reads}), encoding="utf-8")


def x_usage_reads() -> int:
    """Devuelve las lecturas acumuladas de X en el mes actual (0 si no hay archivo)."""
    _, reads = _x_load_usage()
    return reads


def fetch_x(source: Source, query: str, limit: int, monthly_budget: int,
            curated_handles: frozenset[str] = frozenset(),
            freshness_hours: int = 48) -> list[NewsItem]:
    """Recent search de X. DE PAGO (~$0.005/post leído). Tope mensual DURO.

    Solo lee si queda presupuesto del mes (persistido en data/x_usage.json).
    `curated_handles` (en minúsculas, sin @) marca los posts de cuentas de alta
    señal con `extra["curated"]=True` para que el ranking social los anteponga.
    `freshness_hours` acota la ventana (recent search llega hasta 7 días).
    """
    import os
    bearer = os.getenv("X_BEARER_TOKEN")
    if not bearer:
        log.warning("  x_twitter: falta X_BEARER_TOKEN en .env; se omite")
        return []
    month, used = _x_load_usage()
    remaining = monthly_budget - used
    if remaining < 10:  # recent search exige max_results >= 10
        log.warning("  x_twitter: presupuesto del mes agotado (%d/%d lecturas); se omite",
                    used, monthly_budget)
        return []
    max_results = max(10, min(limit, remaining, 25))
    params = {
        "query": f"({query}) -is:retweet -is:reply -job -hiring -\"job alert\" lang:en",
        "max_results": max_results,
        "tweet.fields": "created_at,public_metrics,lang,author_id",
        "expansions": "author_id",
        "user.fields": "username",
    }
    # Ventana de frescura: solo posts recientes (más "buzz" actual, mismo costo).
    if freshness_hours and freshness_hours > 0:
        start = datetime.now(timezone.utc) - timedelta(hours=freshness_hours)
        params["start_time"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get(
        "https://api.twitter.com/2/tweets/search/recent",
        params=params, headers={"Authorization": f"Bearer {bearer}"}, timeout=25,
    )
    if r.status_code >= 400:
        log.warning("  x_twitter: HTTP %s %s", r.status_code, r.text[:200])
        return []
    payload = r.json()
    tweets = payload.get("data", []) or []
    # author_id -> username (las expansiones no cuestan lecturas extra).
    users = {u.get("id"): (u.get("username") or "")
             for u in payload.get("includes", {}).get("users", []) or []}
    _x_save_usage(month, used + len(tweets))  # se cobra por post leído
    items = []
    for tw in tweets:
        text = tw.get("text", "")
        tid = tw.get("id")
        m = tw.get("public_metrics", {}) or {}
        username = users.get(tw.get("author_id"), "")
        curated = username.lower() in curated_handles
        post_url = (f"https://x.com/{username}/status/{tid}" if username
                    else f"https://x.com/i/web/status/{tid}")
        items.append(NewsItem(
            title=(text[:90] + ("…" if len(text) > 90 else "")) or f"post {tid}",
            url=post_url,
            source_id=source.id,
            source_name=(f"{source.name} › @{username}" if username else source.name),
            tier=source.tier,
            published=_parse_date(tw.get("created_at")),
            summary=text,
            extra={"kind": "post", "network": "x",
                   "likes": m.get("like_count"),
                   "reposts": m.get("retweet_count"),
                   "author": username, "curated": curated},
        ))
    n_cur = sum(1 for it in items if it.extra.get("curated"))
    log.info("  %-16s -> %d posts (%d curados; lecturas del mes: %d/%d)",
             source.id, len(tweets), n_cur, used + len(tweets), monthly_budget)
    return items


def _build_nacional_gnews_query(source: Source) -> str:
    """Consulta única de Google News para los medios CL sin RSS nativo.

    Une los dominios de `sites` con `site:a OR site:b …` y acota la ventana con
    `when:Nd` (N = `freshness_days`). Devuelve "" si no hay dominios."""
    sites = [s.strip() for s in (source.raw.get("sites") or []) if s.strip()]
    if not sites:
        return ""
    days = int(source.raw.get("freshness_days", 1) or 1)
    return "(" + " OR ".join(f"site:{s}" for s in sites) + f") when:{days}d"


QUERY_SOURCES = {"arxiv_api", "pubmed_eutils", "hacker_news", "google_news_rss"}

# Fuentes que alimentan "Voces de la red" (cada una se consulta con UNA lente,
# no por tema). Los ítems van sin `topics` y se seleccionan en `_select_social`.
SOCIAL_API_SOURCES = {"mastodon", "bluesky", "reddit"}


def fetch_source(source: Source, topic_cfgs: list[tuple[str, dict]], limit: int) -> list[NewsItem]:
    """Dispatcher: enruta a cada fetcher y etiqueta los ítems por tema.

    - Fuentes por consulta (arXiv/PubMed/HN/Google News): una búsqueda por tema.
    - Medios por RSS: se baja el feed una vez y se clasifica cada ítem por relevancia.
    Cada fuente que falla solo registra un warning; nunca rompe la corrida.
    """
    topics = [t for t, _ in topic_cfgs]
    items: list[NewsItem] = []

    if source.id == "x_twitter":
        # X NO enriquece los temas: va solo a "Voces de la red". Una SOLA consulta
        # por corrida. La lente (elegida al azar por día) determina el tema de
        # las palabras clave; las cuentas curadas siempre entran.
        budget = int(source.raw.get("monthly_read_budget", 300) or 300)
        freshness = int(source.raw.get("social_freshness_hours", 48) or 48)
        handles = [h.strip().lstrip("@")
                   for h in (source.raw.get("curated_accounts") or []) if h.strip()]
        curated_set = frozenset(h.lower() for h in handles)
        # Elegir lente para X (misma semilla que las otras redes)
        sc = load_social_config()
        lenses = sc.get("lenses", [])
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lens = pick_lens("x_twitter", lenses, today) if lenses else {}
        x_topic = lens.get("x_topic", "")
        cfg = TOPIC_CONFIG.get(x_topic, {})
        keywords = cfg.get("news", "") if cfg else ""
        if not keywords:
            keywords = (source.raw.get("social_query") or "").strip()
        # Armar query: cuentas curadas OR keywords de la lente
        parts: list[str] = []
        if handles:
            parts.append("(" + " OR ".join(f"from:{h}" for h in handles) + ")")
        if keywords:
            parts.append(f"({keywords})")
        query = " OR ".join(parts) if parts else keywords
        try:
            items.extend(fetch_x(source, query, limit, budget,
                                 curated_handles=curated_set, freshness_hours=freshness))
        except Exception as ex:  # noqa: BLE001
            log.warning("  x_twitter FALLÓ: %s", ex)
        return items

    if source.id in SOCIAL_API_SOURCES:
        # Mastodon, Bluesky, Reddit: una lente al azar por red (estable en el día),
        # una sola consulta. Ítems SIN topic (van solo a "Voces de la red").
        try:
            sc = load_social_config()
            lenses = sc.get("lenses", [])
            if not lenses:
                log.warning("  %-16s: sin lentes configurados; se omite", source.id)
                return []
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            lens = pick_lens(source.id, lenses, today)
            if source.id == "mastodon":
                items.extend(fetch_mastodon(source, lens, limit))
            elif source.id == "bluesky":
                items.extend(fetch_bluesky(source, lens, limit))
            elif source.id == "reddit":
                items.extend(fetch_reddit(source, lens, limit))
        except Exception as ex:  # noqa: BLE001
            log.warning("  %-16s FALLÓ: %s", source.id, ex)
        return items

    if source.id == "google_news_nacional":
        # Una sola consulta `site:` combinada para los regionales CL sin RSS.
        # Independiente de las keywords de tema: van todos al tema 'nacional'.
        try:
            query = _build_nacional_gnews_query(source)
            if query:
                lang = source.raw.get("lang") or "es-419"
                country = source.raw.get("country") or "CL"
                got = fetch_googlenews(source, query, max(limit, 20), lang=lang, country=country)
                scope = source.raw.get("scope", "")
                for it in got:
                    it.topics = ["nacional"]
                    it.extra["scope"] = scope
                items.extend(got)
                log.info("  %-16s [nacional] -> %d ítems (site: GN)", source.id, len(got))
        except Exception as ex:  # noqa: BLE001
            log.warning("  %-16s [nacional] FALLÓ: %s", source.id, ex)
        return items

    if source.id in QUERY_SOURCES:
        for topic, cfg in topic_cfgs:
            try:
                if source.id == "arxiv_api" and cfg.get("arxiv"):
                    got = fetch_arxiv(source, cfg["arxiv"], limit)
                elif source.id == "pubmed_eutils" and cfg.get("pubmed") and cfg.get("news"):
                    got = fetch_pubmed(source, cfg["news"], limit)
                elif source.id == "hacker_news" and cfg.get("hn"):
                    got = fetch_hackernews(source, cfg["hn"], limit)
                elif source.id == "google_news_rss" and cfg.get("news"):
                    got = [it for it in fetch_googlenews(source, cfg["news"], limit)
                           if is_relevant(it.title, topic)]
                else:
                    continue  # esta fuente no sirve este tema (p. ej. 'nacional')
                for it in got:
                    it.topics = [topic]
                items.extend(got)
                log.info("  %-16s [%s] -> %d ítems", source.id, topic, len(got))
            except Exception as ex:  # noqa: BLE001  (aislamos cada fuente)
                log.warning("  %-16s [%s] FALLÓ: %s", source.id, topic, ex)

    elif source.type in ("rss", "atom") and source.url:
        try:
            # Acota a los temas que la fuente declara servir (`topics:` en
            # sources.yaml); si no declara ninguno, sirve todos los pedidos. Evita
            # que un medio nacional se cuele en temas de ciencia y viceversa
            # (clave porque 'nacional' no tiene keywords → casaría con todo).
            allowed = source.topics or topics
            topics_for_source = [t for t in topics if t in allowed]
            scope = source.raw.get("scope", "")
            raw = fetch_generic_rss(source, max(limit, 25))
            for it in raw:
                matched = classify_topics(it.title, it.summary, topics_for_source)
                if matched:
                    it.topics = matched[:1]
                    if scope:
                        it.extra["scope"] = scope
                    items.append(it)
            log.info("  %-16s [rss] -> %d/%d relevantes", source.id, len(items), len(raw))
        except Exception as ex:  # noqa: BLE001
            log.warning("  %-16s [rss] FALLÓ: %s", source.id, ex)

    return items
