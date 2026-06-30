# -*- coding: utf-8 -*-
"""Tests del selector _select_curado (Frontera Digital, Medicina): 1 tarjeta
por fuente distinta, ventana de frescura de 2 días, portada por score puro."""
from datetime import datetime, timedelta, timezone

from sibylla.models import NewsItem
from sibylla.web import CURATED_FRESH_HOURS, _select_curado

_NOW = datetime.now(timezone.utc)


def _item(source_id: str, hours_ago: float = 1, tier: int = 2, title: str = "") -> NewsItem:
    return NewsItem(
        title=title or f"Nota de {source_id} ({hours_ago}h)",
        url=f"https://example.com/{source_id}/{hours_ago}",
        source_id=source_id,
        source_name=source_id.upper(),
        published=_NOW - timedelta(hours=hours_ago),
        summary="",
        topics=["ai"],
        tier=tier,
    )


class TestDiversidadDeFuentes:
    def test_seis_fuentes_distintas_da_seis_distintas(self):
        items = [_item(f"fuente{i}", hours_ago=i + 1) for i in range(8)]
        sel = _select_curado(items, max_n=6)
        assert len(sel) == 6
        assert len({it.source_id for it in sel}) == 6

    def test_una_sola_tarjeta_por_fuente_si_hay_pool_suficiente(self):
        # 2 fuentes muy frescas/prolíficas (como techcrunch/arxiv) + 4 con 1 nota.
        items = (
            [_item("techcrunch", hours_ago=h) for h in (1, 2, 3, 4, 5)]
            + [_item("arxiv_api", hours_ago=h, tier=1) for h in (1, 2, 3, 4, 5)]
            + [_item("krebs", hours_ago=2)]
            + [_item("bleepingcomputer", hours_ago=3)]
            + [_item("xataka_ia", hours_ago=4)]
            + [_item("mit_tech_review", hours_ago=5)]
        )
        sel = _select_curado(items, max_n=6)
        counts = {}
        for it in sel:
            counts[it.source_id] = counts.get(it.source_id, 0) + 1
        assert max(counts.values()) == 1, f"alguna fuente repitió: {counts}"
        assert set(counts) == {"techcrunch", "arxiv_api", "krebs",
                                "bleepingcomputer", "xataka_ia", "mit_tech_review"}


class TestVentanaDeFrescura:
    def test_frescos_se_prefieren_sobre_viejos_aunque_repitan_fuente(self):
        """Con solo 2 fuentes frescas (<=48h) y un hueco de 6, se repiten esas
        2 antes de traer una 3ra fuente vieja (relleno: frescura primero)."""
        items = (
            [_item("techcrunch", hours_ago=h) for h in (1, 5, 10, 20)]
            + [_item("arxiv_api", hours_ago=h, tier=1) for h in (2, 8)]
            + [_item("krebs", hours_ago=h) for h in (100, 200)]  # viejas (>48h)
        )
        sel = _select_curado(items, max_n=6)
        assert len(sel) == 6
        fuentes = [it.source_id for it in sel]
        # Las 6 frescas (4 techcrunch + 2 arxiv) deben entrar antes que krebs.
        assert fuentes.count("krebs") == 0

    def test_repite_fuente_sin_limite_si_todo_es_viejo(self):
        """Si todas las fuentes están a >48h, se permite repetir una misma
        fuente las veces que haga falta para llenar las 6."""
        items = [_item("techcrunch", hours_ago=72 + i) for i in range(6)]
        sel = _select_curado(items, max_n=6)
        assert len(sel) == 6
        assert all(it.source_id == "techcrunch" for it in sel)

    def test_umbral_de_frescura_es_48h(self):
        justo_fresco = _item("a", hours_ago=CURATED_FRESH_HOURS - 0.01)
        justo_viejo = _item("b", hours_ago=CURATED_FRESH_HOURS + 0.01)
        sel = _select_curado([justo_fresco, justo_viejo], max_n=1)
        assert sel[0].source_id == "a"


class TestPortada:
    def test_orden_final_por_score_puro(self):
        """Tras elegir las 6, el orden de tarjetas es el score (tier x
        frescura), no el orden de rondas: un tier-1 muy fresco puede ir 1°."""
        items = [
            _item("techcrunch", hours_ago=1, tier=2),
            _item("arxiv_api", hours_ago=1, tier=1),  # mismo frescor, tier mejor -> score mayor
        ]
        sel = _select_curado(items, max_n=2)
        assert sel[0].source_id == "arxiv_api"

    def test_evita_que_las_2_primeras_sean_de_la_misma_fuente_si_es_posible(self):
        # techcrunch domina el pool fresco; krebs aporta 1 nota algo más vieja
        # pero igual dentro de la ventana fresca.
        items = (
            [_item("techcrunch", hours_ago=h, tier=2) for h in (1, 1.5, 2)]
            + [_item("krebs", hours_ago=10, tier=2)]
        )
        sel = _select_curado(items, max_n=4)
        assert sel[0].source_id != sel[1].source_id

    def test_permite_repetir_portada_si_es_imposible_evitarlo(self):
        items = [_item("techcrunch", hours_ago=h) for h in (1, 2, 3)]
        sel = _select_curado(items, max_n=3)
        assert all(it.source_id == "techcrunch" for it in sel)
        assert len(sel) == 3


class TestCasosBorde:
    def test_lista_vacia(self):
        assert _select_curado([], max_n=6) == []

    def test_menos_items_que_max_n(self):
        items = [_item("techcrunch", hours_ago=1), _item("krebs", hours_ago=2)]
        sel = _select_curado(items, max_n=6)
        assert len(sel) == 2
