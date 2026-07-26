# TEST.md — Tests de Sibylla

Tests de la lógica de dominio pura del ingestor. Sin dependencias externas, sin red, ejecución en < 1 segundo.

## Requisitos

```bash
pip install -r requirements-dev.txt
```

## Ejecución

```bash
# todos los tests
python -m pytest tests/ -v

# solo un módulo
python -m pytest tests/test_models.py -v
python -m pytest tests/test_relevance.py -v
python -m pytest tests/test_newsletter.py tests/test_suscriptores.py tests/test_locales.py tests/test_web.py -q

# con cobertura (opcional, instalar pytest-cov)
python -m pytest tests/ -v --cov=sibylla --cov-report=term-missing
```

## Estructura

```
tests/
  __init__.py
  test_models.py       # canonicalize_url, clean_text, normalize_title, NewsItem
  test_relevance.py    # _strip_accents, is_relevant, classify_topics
```

## Qué se testea y por qué

### `test_models.py` (35 tests) — Fundación de datos

| Función | ¿Qué valida? | Nº tests |
|---------|-------------|----------|
| `canonicalize_url` | Eliminación de tracking params (utm_*, gclid, fbclid, ref, etc.), www→raíz, http→https, trailing slash, lowercase, fragment, esquema no-http, URLs malformadas | 14 |
| `clean_text` | HTML tags, entidades (&amp;, &lt;), colapso de espacios, stripping de extremos, saltos de línea | 8 |
| `normalize_title` | Puntuación eliminada, lowercase, entidades HTML → limpieza previa | 4 |
| `NewsItem.__post_init__` | Limpieza automática de title/summary, datetime naive→UTC aware, aware preservado, None preservado | 4 |
| `NewsItem.dedup_key` | URL canónica como clave de dedup, fallback a título normalizado sin URL, equivalencia entre URLs distintas | 4 |
| `NewsItem.age_hours` | Cálculo con fecha, valor centinela 1e9 sin fecha | 2 |
| `NewsItem.canonical_url` | Atajo de propiedad | 1 |

**Por qué es #1:** Todo el pipeline (deduplicación, ranking, diversidad) descansa sobre `canonicalize_url` y `dedup_key`. Un error aquí corrompe todo el output sin señales visibles.

### `test_relevance.py` (34 tests) — Filtro bilingüe

| Función | ¿Qué valida? | Nº tests |
|---------|-------------|----------|
| `_strip_accents` | Normalización NFD + filtro categoría Mn (tildes, diéresis) en español | 6 |
| `is_relevant` (keyword corta ≤3) | Word boundary `\b`: `ai` casa con `AI` pero NO con `airport`; `esa` NO casa con `mesa` | 9 |
| `is_relevant` (keyword larga >3) | Substring: `generativ` ⊆ `generative`, `generativ` ⊄ `generacion`; stems bilingües; tildes y mayúsculas | 10 |
| `is_relevant` (edge cases) | Título vacío, tema sin keywords, early return | 3 |
| `classify_topics` | Múltiples temas, cero matches, respeta solo los topics pedidos, título+summary combinados | 4 |

**Por qué es #2:** Es la lógica más compleja del código (stems bilingües ES/EN, stripping de tildes, \b para keywords cortas, substring para largas). Si falla, entran noticias ruidosas o se pierden señales importantes. Es la función más difícil de verificar a ojo.

## Lo que NO se testea aún (y por qué)

| Módulo | Razón |
|--------|-------|
| `pipeline.py` (dedupe, rank, diversify) | Lógica importante pero más simple y verificable por inspección. Dependen de `dedup_key` ya testeado. Próxima fase natural. |
| `digest.py` (render_digest) | Salida determinista → snapshot testing ideal. Próxima fase. |
| `i18n.py` (t, resolve_lang) | Acceso a JSON con fallback. Próxima fase. |
| `fetchers.py` (fetch_*) | Llamadas HTTP externas. Mejor con VCR/replay o tests de integración. |
| `llm.py` (proveedores) | Llamadas HTTP + entorno. Tests de integración con mock. |
| `cli.py` (main) | Orquestación. Test de integración/e2e. |

## Convenciones

### Boletín y reglas de Firestore

Los tests del boletín usan SMTP, LLM y REST falsos: no abren red. Además de
pytest, validar `firestore.rules` con `firebase deploy --only firestore:rules
--dry-run` y en el Playground de reglas estos casos: correo distinto del token;
email sin verificar; uid ajeno; tema fuera de lista; lista vacía o de 9 temas
(todos denegados); delete del dueño (permitido). Los tests del emulador requieren
Java, pero su ausencia no bloquea estas dos validaciones.

La regresión obligatoria del estado es: primera corrida con cero suscriptores,
alta posterior y segunda corrida el mismo día. La segunda debe releer Firestore
y enviar al uid nuevo aunque el estado anterior diga `terminado: true`. Un fallo
REST debe ser distinto de una colección vacía y no puede modificar el estado.
Los mocks SMTP validan la captura segura del código DATA/queue-id, pero no
sustituyen una prueba de entrega real con Hostinger.

- **Idioma:** docstrings y descripciones en español, coherente con el resto del repo.
- **Sin mocks ni fixtures:** todos los casos son datos inline (funciones puras).
- **Parametrize:** se usa `@pytest.mark.parametrize` para agrupar casos similares con un 3er campo `_desc` (solo documentación, no se aserta).
- **Un assert por test** salvo en tests de integridad (p. ej. `test_canonicalize_url_determinismo`).

## Roadmap de tests

- [x] Fase 1 — `models.py` + `fetchers.py` (relevancia): **69 tests, esta entrega**
- [ ] Fase 2 — `pipeline.py` (dedupe, rank, diversify)
- [ ] Fase 3 — `digest.py` + `i18n.py` (render e internacionalización)
- [ ] Fase 4 — `fetchers.py` (HTTP con VCR/replay)
- [ ] Fase 5 — `llm.py` (proveedores con mock HTTP)
- [ ] Fase 6 — `web.py` (generación HTML, snapshot)
