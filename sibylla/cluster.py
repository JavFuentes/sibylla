"""Agrupa ítems que cuentan la MISMA historia aunque vengan de medios distintos.

El `dedupe` del pipeline solo fusiona URLs canónicas idénticas. Pero una misma
noticia cubierta por Nature + BBC + Phys.org tiene URLs distintas, así que
sobrevive como varias tarjetas. Esta etapa la detecta por similitud de título,
conserva un representante (la fuente más fiable) y cuelga el resto en
`NewsItem.related` para mostrarlos como "También en: …".

Es una función PURA (sin red): se inserta entre `dedupe` y `rank` en el pipeline.
Filosofía conservadora: es preferible sub-agrupar (mostrar dos tarjetas distintas)
a fusionar de más (mezclar dos historias). Por eso el umbral es exigente y solo
se agrupan ítems de FUENTES distintas.
"""
from __future__ import annotations

import re

from .fetchers import _strip_accents
from .models import NewsItem, clean_text, normalize_title

# --- parámetros (tunables) ---------------------------------------------------
# Similitud de Jaccard mínima entre los conjuntos de tokens significativos.
SIM_THRESHOLD = 0.5
# Tokens compartidos mínimos: evita que una sola palabra en común dispare un match.
MIN_SHARED = 2
# Entidades (nombres propios/acrónimos) compartidas mínimas para considerar la
# MISMA historia aunque el Jaccard de título no llegue al umbral. Es la señal de
# "corroboración" que importa en la sección Nacional: dos medios que reescriben
# el titular pero coinciden en 2+ nombres propios cubren la misma noticia.
MIN_SHARED_ENTITIES = 2
# Ventana de fechas: dos coberturas de la misma historia salen con pocos días de
# diferencia. Solo se aplica si AMBOS ítems tienen fecha.
DATE_WINDOW_DAYS = 14

# Stopwords bilingües (ES/EN), ya sin tildes y de 3+ caracteres (las cortas las
# descarta el filtro de longitud). Quitarlas evita falsos positivos por palabras
# de relleno comunes a titulares no relacionados.
_STOPWORDS = frozenset({
    # español
    "los", "las", "una", "unos", "unas", "del", "con", "por", "para", "que",
    "como", "mas", "este", "esta", "estos", "estas", "nuevo", "nueva", "nuevos",
    "nuevas", "estudio", "segun", "sobre", "entre", "son", "fue", "han", "hay",
    "dos", "tras", "ante", "desde", "muy", "pero", "sus", "este",
    # inglés
    "the", "and", "for", "that", "are", "was", "were", "new", "study", "how",
    "why", "what", "from", "its", "this", "these", "has", "have", "will", "can",
    "into", "than", "then", "says", "said", "after", "over", "amid", "with",
    "could", "would", "more", "out", "about",
})


def _tokens(title: str) -> frozenset[str]:
    """Conjunto de tokens significativos del título: minúsculas, sin puntuación,
    sin tildes, sin stopwords ni palabras de ≤2 caracteres."""
    norm = _strip_accents(normalize_title(title))
    return frozenset(w for w in norm.split() if len(w) > 2 and w not in _STOPWORDS)


_WORD_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+")


def _entities(title: str) -> frozenset[str]:
    """Pseudo-entidades del título: nombres propios y acrónimos (sin NLP).

    Heurística dependency-free sobre el título ORIGINAL (con mayúsculas):
      - acrónimos en MAYÚSCULAS de 2+ letras (SQM, TVN, CIPER);
      - palabras Capitalizadas de 4+ letras que no sean stopwords (nombres,
        lugares: 'Boric', 'Contraloria', 'Antofagasta').
    Se conserva la primera palabra (en titulares en español suele ser el sujeto:
    'Boric anuncia…'); las comunes que la inician suelen ser stopwords/cortas y
    se filtran igual. Si el título está en Title Case (casi todo capitalizado),
    se descarta la señal de entidades (devolvería ruido) y solo cuenta el Jaccard."""
    words = _WORD_RE.findall(clean_text(title))
    if len(words) < 3:
        return frozenset()  # títulos muy cortos: la señal de entidades es ruidosa
    caps = [w for w in words if w[:1].isupper()]
    # Title Case: si la mayoría va capitalizada, no son entidades distintivas.
    if len(caps) / len(words) > 0.6:
        return frozenset()
    ents: set[str] = set()
    for w in words:
        low = _strip_accents(w.lower())
        if w.isupper() and len(w) >= 2:
            ents.add(low)
        elif w[:1].isupper() and len(w) >= 4 and low not in _STOPWORDS:
            ents.add(low)
    return frozenset(ents)


def _similar(a: frozenset[str], b: frozenset[str],
             threshold: float, min_shared: int) -> bool:
    """True si dos conjuntos de tokens describen la misma historia.

    Exige `min_shared` tokens en común (descarta coincidencias triviales) y un
    Jaccard ≥ `threshold`. Con títulos muy cortos (< min_shared tokens) nunca
    casa: la intersección no puede alcanzar el mínimo."""
    inter = len(a & b)
    if inter < min_shared:
        return False
    union = len(a | b)
    return union > 0 and inter / union >= threshold


def _same_story(a_toks: frozenset[str], a_ents: frozenset[str],
                b_toks: frozenset[str], b_ents: frozenset[str],
                threshold: float, min_shared: int, min_shared_entities: int) -> bool:
    """True si dos ítems cuentan la misma historia, por CUALQUIERA de dos vías:
    similitud de Jaccard de tokens (reescrituras del mismo titular) o coincidencia
    de ≥`min_shared_entities` nombres propios/acrónimos (corroboración)."""
    if _similar(a_toks, b_toks, threshold, min_shared):
        return True
    return len(a_ents & b_ents) >= min_shared_entities


def _better_rep(a: NewsItem, b: NewsItem) -> bool:
    """True si `a` es mejor representante que `b`: menor tier (más fiable) y,
    a igualdad de tier, más reciente."""
    if a.tier != b.tier:
        return a.tier < b.tier
    return a.age_hours < b.age_hours


def _within_window(a: NewsItem, b: NewsItem, days: int) -> bool:
    """True si las fechas están dentro de la ventana, o si a alguno le falta."""
    if a.published is None or b.published is None:
        return True
    return abs((a.published - b.published).total_seconds()) <= days * 86400


def _attach(rep: NewsItem, others: list[NewsItem]) -> None:
    """Cuelga del representante los otros medios (en `related`) y une los temas.

    `related` se ordena por tier (más fiable primero) y nunca repite fuente. Los
    temas conservan el orden del representante y solo se añaden los nuevos, para
    no alterar el tema primario (`topics[0]`) que decide la columna en la web."""
    seen_sources = {rep.source_id}
    rel: list[dict] = []
    for o in sorted(others, key=lambda m: (m.tier, m.age_hours)):
        if o.source_id in seen_sources:
            continue
        seen_sources.add(o.source_id)
        rel.append({"source_name": o.source_name, "url": o.url, "tier": o.tier})
    rep.related = rel
    for o in others:
        for tp in o.topics:
            if tp not in rep.topics:
                rep.topics.append(tp)


def cluster_stories(items: list[NewsItem], *,
                    threshold: float = SIM_THRESHOLD,
                    min_shared: int = MIN_SHARED,
                    min_shared_entities: int = MIN_SHARED_ENTITIES,
                    date_window_days: int = DATE_WINDOW_DAYS) -> list[NewsItem]:
    """Agrupa la misma historia entre medios distintos. Devuelve los
    representantes (uno por historia) con sus satélites en `related`.

    Greedy single-link: cada ítem se compara contra el TÍTULO del representante
    de cada cluster ya abierto; si supera el umbral de similitud O comparte
    suficientes entidades (nombres propios) y aporta una fuente nueva, se une;
    si no, abre su propio cluster. Conserva el orden de entrada (luego `rank`
    reordena)."""
    clusters: list[dict] = []  # {"rep", "rep_tokens", "rep_ents", "members", "sources"}
    for it in items:
        toks = _tokens(it.title)
        ents = _entities(it.title)
        placed = False
        for cl in clusters:
            if it.source_id in cl["sources"]:
                continue  # mismo medio = historia distinta (los dups ya los quitó dedupe)
            if not _within_window(it, cl["rep"], date_window_days):
                continue
            if _same_story(toks, ents, cl["rep_tokens"], cl["rep_ents"],
                           threshold, min_shared, min_shared_entities):
                cl["members"].append(it)
                cl["sources"].add(it.source_id)
                if _better_rep(it, cl["rep"]):
                    cl["rep"] = it
                    cl["rep_tokens"] = toks
                    cl["rep_ents"] = ents
                placed = True
                break
        if not placed:
            clusters.append({
                "rep": it, "rep_tokens": toks, "rep_ents": ents,
                "members": [it], "sources": {it.source_id},
            })

    out: list[NewsItem] = []
    for cl in clusters:
        rep = cl["rep"]
        others = [m for m in cl["members"] if m is not rep]
        if others:
            _attach(rep, others)
        out.append(rep)
    return out
