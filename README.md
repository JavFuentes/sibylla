# Sibylla

> Investigador periódico de noticias: lee fuentes confiables, las filtra y rankea por confiabilidad, y produce un **resumen con enlaces a la fuente original** para profundizar.

Sibylla revisa cada cierto tiempo temas que te interesan (empezando por **ciencia y tecnología**) y te entrega un resumen ordenado. No republica el contenido: **detecta la noticia y enlaza a una fuente fiable**. El diseño es agnóstico de tema (se escala por configuración) y agnóstico de proveedor de IA (conectas la API que quieras, o ninguna).

> **Estado:** prototipo funcional. La ingesta, el filtrado/ranking y el resumen (con o sin IA) funcionan. La automatización periódica y la entrega (email/web) están en el roadmap.

---

## Características

- **Fuentes por confiabilidad (tiers), no por idioma.** Ingesta multilingüe; el resumen se entrega en tu idioma.
- **15 fuentes por defecto:** APIs científicas (arXiv, PubMed), agregadores (Google News, Hacker News) y 11 medios por RSS directo (Nature, BBC, MIT Tech Review, Phys.org, ScienceDaily, The Conversation, TechCrunch, Scientific American, Quanta, IEEE Spectrum, Agencia SINC en español).
- **Filtro de relevancia bilingüe** (ES/EN, sin tildes) y **deduplicación** por URL canónica / título.
- **Ranking** por `tier × frescura` y **diversidad** (una sola fuente no tapa al resto).
- **Resumen con IA opcional y multi-proveedor:** Anthropic (Claude), OpenAI, OpenRouter, cualquier endpoint compatible o **Ollama** (local). Sin LLM, genera una lista determinista.
- **X / Twitter opcional** con **tope de presupuesto mensual duro** (es de pago por uso).

## Arquitectura

```
 FUENTES                INGESTA               PROCESO                SALIDA
┌──────────────┐   ┌────────────────┐   ┌──────────────────┐   ┌──────────────┐
│ APIs (arXiv, │   │ fetchers.py    │   │ pipeline.py      │   │ digest.py /  │
│ PubMed)      │──▶│ normaliza a    │──▶│ dedupe + rank +  │──▶│ summarize.py │
│ Google News  │   │ NewsItem;      │   │ diversify;       │   │ -> Markdown  │
│ Hacker News  │   │ relevancia     │   │ por tema         │   │ con enlaces  │
│ Medios RSS   │   │ por tema       │   │                  │   │ (output/)    │
│ X (opcional) │   └────────────────┘   └──────────────────┘   └──────────────┘
└──────────────┘                                              IA opcional (llm.py)
```

Cada ítem conserva su **URL de origen** y su **tier de confianza**. Ver [`config/README.md`](config/README.md) para el registro de fuentes y los tiers.

## Instalación

Requiere **Python 3.10+** (probado en 3.12).

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate     |  Linux/Mac:  source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # opcional: rellena claves (IA, X, etc.)
```

## Uso

```bash
# Resumen de IA y medicina (lista determinista si no hay LLM configurado)
python -m sibylla.cli --topics ai,medicine --max-per-source 8

# Otros temas y fuentes concretas
python -m sibylla.cli --topics space --sources google_news_rss,arxiv_api

# Forzar solo lista (sin IA), o incluir X (DE PAGO, con tope de presupuesto)
python -m sibylla.cli --topics ai --summarize off
python -m sibylla.cli --topics ai --with-x
```

El resumen se escribe en `output/digest-AAAAMMDD-HHMM.md`.

Temas disponibles: `ai, computing, space, physics, biotech, medicine, neuroscience, climate, energy, general_science, general_tech`.

## Configuración

Toda la configuración sensible vive en `.env` (que **no** se sube al repo). Copia `.env.example` y rellena lo que necesites:

- **IA (opcional):** `LLM_PROVIDER` (`anthropic` / `openai` / `openrouter` / `openai_compatible` / `ollama`), `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`.
- **X / Twitter (opcional, de pago):** `X_BEARER_TOKEN` (+ claves). El tope mensual de lecturas vive en `config/sources.yaml` (`x_twitter.monthly_read_budget`) y el uso se cuenta en `data/x_usage.json`.
- **Otras (opcionales):** `NCBI_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY`, `GUARDIAN_API_KEY`, `REDDIT_*`, `BLUESKY_*`.

Las fuentes se definen en [`config/sources.yaml`](config/sources.yaml) (registro curado por tiers).

## Roadmap

- [x] Ingestor (fetchers + normalización + dedupe + ranking)
- [x] Resumen con IA multi-proveedor (con fallback determinista)
- [x] Calidad: relevancia bilingüe, diversidad, URLs limpias de medios
- [x] Más fuentes (medios RSS + español + X con presupuesto)
- [ ] **Automatización periódica + entrega (email / web en sibylla.cl)**
- [ ] Resolver URLs de Google News (formato opaco actual) — mitigado con medios directos

## Notas

- **Seguridad:** nunca subas `.env` (tiene claves reales). Ver [AGENTS.md](AGENTS.md).
- **Licencia:** por definir (sugerido MIT).
- Para contribuir o trabajar con agentes de IA, lee [AGENTS.md](AGENTS.md).
