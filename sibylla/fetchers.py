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
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import requests
from dateutil import parser as dateparser

from .config import Source
from .models import NewsItem

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


def _get(url: str, params: dict | None = None, timeout: int = 25) -> requests.Response:
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
    for e in feed.entries[:limit]:
        items.append(NewsItem(
            title=e.get("title", ""),
            url=e.get("link", ""),
            source_id=source.id,
            source_name=source.name,
            tier=source.tier,
            published=_from_struct(e.get("published_parsed") or e.get("updated_parsed")),
            summary=e.get("summary", ""),
            extra={},
        ))
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
            extra={"kind": "post", "likes": m.get("like_count"),
                   "reposts": m.get("retweet_count"),
                   "author": username, "curated": curated},
        ))
    n_cur = sum(1 for it in items if it.extra.get("curated"))
    log.info("  %-16s -> %d posts (%d curados; lecturas del mes: %d/%d)",
             source.id, len(tweets), n_cur, used + len(tweets), monthly_budget)
    return items


def _build_x_social_query(source: Source, handles: list[str],
                          topic_cfgs: list[tuple[str, dict]]) -> str:
    """Arma la consulta híbrida de X para 'Voces de la red' en UNA sola llamada.

    Combina dos señales con OR (la preferencia por lo curado se resuelve después,
    en `_social_score`):
      - cuentas curadas: `(from:a OR from:b OR …)`  → base de alta señal.
      - palabras clave:  `social_query` de sources.yaml, o si falta, la unión de
        las consultas `news` de los temas pedidos → relleno (búsqueda abierta).
    """
    parts: list[str] = []
    if handles:
        parts.append("(" + " OR ".join(f"from:{h}" for h in handles) + ")")
    keywords = (source.raw.get("social_query") or "").strip()
    if not keywords:
        keywords = " OR ".join(cfg["news"] for _, cfg in topic_cfgs if cfg.get("news"))
    if keywords:
        parts.append(f"({keywords})")
    return " OR ".join(parts) if parts else keywords


QUERY_SOURCES = {"arxiv_api", "pubmed_eutils", "hacker_news", "google_news_rss"}


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
        # híbrida combinada (cuentas curadas + palabras clave) para no duplicar
        # lecturas (~10/día). Los ítems quedan SIN topic (no son tarjetas de tema).
        budget = int(source.raw.get("monthly_read_budget", 300) or 300)
        freshness = int(source.raw.get("social_freshness_hours", 48) or 48)
        handles = [h.strip().lstrip("@")
                   for h in (source.raw.get("curated_accounts") or []) if h.strip()]
        curated_set = frozenset(h.lower() for h in handles)
        query = _build_x_social_query(source, handles, topic_cfgs)
        try:
            items.extend(fetch_x(source, query, limit, budget,
                                 curated_handles=curated_set, freshness_hours=freshness))
        except Exception as ex:  # noqa: BLE001
            log.warning("  x_twitter FALLÓ: %s", ex)
        return items

    if source.id in QUERY_SOURCES:
        for topic, cfg in topic_cfgs:
            try:
                if source.id == "arxiv_api" and cfg.get("arxiv"):
                    got = fetch_arxiv(source, cfg["arxiv"], limit)
                elif source.id == "pubmed_eutils" and cfg.get("pubmed"):
                    got = fetch_pubmed(source, cfg["news"], limit)
                elif source.id == "hacker_news":
                    got = fetch_hackernews(source, cfg["hn"], limit)
                elif source.id == "google_news_rss":
                    got = [it for it in fetch_googlenews(source, cfg["news"], limit)
                           if is_relevant(it.title, topic)]
                else:
                    continue
                for it in got:
                    it.topics = [topic]
                items.extend(got)
                log.info("  %-16s [%s] -> %d ítems", source.id, topic, len(got))
            except Exception as ex:  # noqa: BLE001  (aislamos cada fuente)
                log.warning("  %-16s [%s] FALLÓ: %s", source.id, topic, ex)

    elif source.type in ("rss", "atom") and source.url:
        try:
            raw = fetch_generic_rss(source, max(limit, 25))
            for it in raw:
                matched = classify_topics(it.title, it.summary, topics)
                if matched:
                    it.topics = matched[:1]
                    items.append(it)
            log.info("  %-16s [rss] -> %d/%d relevantes", source.id, len(items), len(raw))
        except Exception as ex:  # noqa: BLE001
            log.warning("  %-16s [rss] FALLÓ: %s", source.id, ex)

    return items
