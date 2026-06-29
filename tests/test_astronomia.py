# -*- coding: utf-8 -*-
"""Tests del selector _select_astronomia (sección Astronomía)."""
from datetime import datetime, timedelta, timezone

import pytest

from sibylla.models import NewsItem
from sibylla.web import (
    ASTRO_AGENCY_IDS,
    ASTRO_MAX_TOTAL,
    ASTRO_PRIORITY_IDS,
    _is_astro,
    _select_astronomia,
)

_NOW = datetime.now(timezone.utc)
_SEED = "2026-06-28"


def _item(source_id: str, title: str = "", hours_ago: float = 1) -> NewsItem:
    return NewsItem(
        title=title or f"Nota de {source_id}",
        url=f"https://example.com/{source_id}/{hours_ago}",
        source_id=source_id,
        source_name=source_id.upper(),
        published=_NOW - timedelta(hours=hours_ago),
        summary="",
        topics=["astronomia"],
        tier=1,
    )


class TestIsAstro:
    def test_priority_sources(self):
        for sid in ASTRO_PRIORITY_IDS:
            assert _is_astro(_item(sid))

    def test_agency_sources(self):
        for sid in ASTRO_AGENCY_IDS:
            assert _is_astro(_item(sid))

    def test_non_astro(self):
        assert not _is_astro(_item("techcrunch"))


class TestSelectAstronomia:
    def test_full_pool_returns_6(self):
        """Con 3 chilenas + 6 agencias frescas, devuelve exactamente 6."""
        items = [
            _item("alma", hours_ago=2),
            _item("cata", hours_ago=4),
            _item("sochias", hours_ago=6),
            _item("nasa", hours_ago=1),
            _item("esa", hours_ago=3),
            _item("jaxa", hours_ago=5),
            _item("cnes", hours_ago=7),
            _item("asi", hours_ago=9),
            _item("uksa", hours_ago=11),
        ]
        sel = _select_astronomia(items, _SEED)
        assert len(sel) == ASTRO_MAX_TOTAL

    def test_slot1_is_chilena_slot2_is_agency(self):
        """Tarjeta 1 = chilena más reciente, tarjeta 2 = agencia más reciente."""
        items = [
            _item("alma", hours_ago=10),
            _item("cata", hours_ago=2),
            _item("sochias", hours_ago=20),
            _item("nasa", hours_ago=1),
            _item("esa", hours_ago=5),
            _item("jaxa", hours_ago=8),
        ]
        sel = _select_astronomia(items, _SEED)
        assert sel[0].source_id == "cata", "Slot 1 debe ser la chilena más reciente"
        assert sel[1].source_id == "nasa", "Slot 2 debe ser la agencia más reciente"

    def test_max_one_per_agency(self):
        """Cada agencia aporta máximo 1 cuando hay suficientes agencias distintas."""
        items = [
            _item("alma", hours_ago=2),
            _item("cata", hours_ago=4),
            _item("sochias", hours_ago=6),
            _item("nasa", "Nota 1 NASA", hours_ago=1),
            _item("nasa", "Nota 2 NASA", hours_ago=3),
            _item("esa", hours_ago=5),
            _item("jaxa", hours_ago=7),
        ]
        sel = _select_astronomia(items, _SEED)
        nasa_count = sum(1 for it in sel if it.source_id == "nasa")
        assert nasa_count == 1

    def test_agency_repeats_when_unavoidable(self):
        """Si no hay bastantes agencias distintas, se repite como último recurso."""
        items = [
            _item("alma", hours_ago=2),
            _item("cata", hours_ago=4),
            _item("sochias", hours_ago=6),
            _item("nasa", "Nota 1 NASA", hours_ago=1),
            _item("nasa", "Nota 2 NASA", hours_ago=3),
            _item("nasa", "Nota 3 NASA", hours_ago=5),
        ]
        sel = _select_astronomia(items, _SEED)
        assert len(sel) == ASTRO_MAX_TOTAL

    def test_stale_chilena_cedes_reserved_slot(self, _desc="fuente >30d no ocupa slot reservado"):
        """Si una chilena no tiene nada de ≤30 días, no ocupa su slot reservado
        en la fase principal; puede entrar como relleno si no hay alternativa."""
        items = [
            _item("alma", hours_ago=2),
            _item("cata", hours_ago=4),
            _item("sochias", hours_ago=31 * 24),  # >30 días
            _item("nasa", hours_ago=1),
            _item("esa", hours_ago=3),
            _item("jaxa", hours_ago=5),
            _item("cnes", hours_ago=7),
        ]
        sel = _select_astronomia(items, _SEED)
        assert len(sel) == ASTRO_MAX_TOTAL
        sids = [it.source_id for it in sel]
        assert "sochias" not in sids, "con suficientes agencias, sochias vieja (>30d) no entra"
        # Slot 1 sigue siendo chilena (alma o cata, las frescas)
        assert sel[0].source_id in ASTRO_PRIORITY_IDS

    def test_chilenas_within_30_days_appear(self, _desc="escenario real: chilenas a 13-24 días aparecen"):
        """Regresión: con la ventana de 30 días, fuentes chilenas de 1-3 semanas
        SÍ ocupan su slot reservado aunque las agencias tengan algo más fresco.
        (Antes, con ventana de 7 días, las chilenas desaparecían por completo.)"""
        items = [
            _item("alma", hours_ago=24 * 24),     # 24 días
            _item("cata", hours_ago=13 * 24),     # 13 días
            _item("sochias", hours_ago=18 * 24),  # 18 días
            _item("nasa", hours_ago=2),
            _item("esa", hours_ago=3),
            _item("jaxa", hours_ago=4),
            _item("asi", hours_ago=5),
            _item("uksa", hours_ago=6),
        ]
        sel = _select_astronomia(items, _SEED)
        sids = [it.source_id for it in sel]
        chilenas = [s for s in sids if s in ASTRO_PRIORITY_IDS]
        assert len(chilenas) == 3, "las 3 chilenas (≤30d) ocupan sus slots reservados"
        assert sel[0].source_id in ASTRO_PRIORITY_IDS, "slot 1 = chilena más reciente"
        assert sel[0].source_id == "cata", "cata (13d) es la chilena más reciente"

    def test_crossfill_agencies_to_chilenas(self):
        """Si no hay suficientes chilenas frescas, las agencias rellenan."""
        items = [
            _item("alma", hours_ago=2),
            _item("nasa", hours_ago=1),
            _item("esa", hours_ago=3),
            _item("jaxa", hours_ago=5),
            _item("cnes", hours_ago=7),
            _item("asi", hours_ago=9),
        ]
        sel = _select_astronomia(items, _SEED)
        assert len(sel) == ASTRO_MAX_TOTAL

    def test_crossfill_chilenas_to_agencies(self):
        """Si no hay suficientes agencias, las chilenas rellenan."""
        items = [
            _item("alma", hours_ago=1),
            _item("alma", "ALMA 2", hours_ago=3),
            _item("cata", hours_ago=2),
            _item("cata", "CATA 2", hours_ago=4),
            _item("sochias", hours_ago=5),
            _item("nasa", hours_ago=6),
        ]
        sel = _select_astronomia(items, _SEED)
        assert len(sel) == ASTRO_MAX_TOTAL

    def test_no_items_returns_empty(self):
        assert _select_astronomia([], _SEED) == []

    def test_only_chilenas(self):
        """Solo fuentes chilenas: todas las tarjetas son chilenas."""
        items = [
            _item("alma", hours_ago=1),
            _item("cata", hours_ago=2),
            _item("sochias", hours_ago=3),
            _item("alma", "ALMA 2", hours_ago=5),
            _item("cata", "CATA 2", hours_ago=7),
            _item("sochias", "SOCHIAS 2", hours_ago=9),
        ]
        sel = _select_astronomia(items, _SEED)
        assert len(sel) == ASTRO_MAX_TOTAL
        assert all(it.source_id in ASTRO_PRIORITY_IDS for it in sel)

    def test_deterministic_with_same_seed(self):
        """Misma semilla produce mismo orden (posiciones 3-6 son shuffle)."""
        items = [
            _item("alma", hours_ago=2),
            _item("cata", hours_ago=4),
            _item("sochias", hours_ago=6),
            _item("nasa", hours_ago=1),
            _item("esa", hours_ago=3),
            _item("jaxa", hours_ago=5),
        ]
        a = _select_astronomia(items, _SEED)
        b = _select_astronomia(items, _SEED)
        assert [it.url for it in a] == [it.url for it in b]

    def test_different_seed_may_shuffle(self):
        """Semilla distinta puede dar orden distinto en posiciones 3-6."""
        items = [
            _item("alma", hours_ago=2),
            _item("cata", hours_ago=4),
            _item("sochias", hours_ago=6),
            _item("nasa", hours_ago=1),
            _item("esa", hours_ago=3),
            _item("jaxa", hours_ago=5),
        ]
        a = _select_astronomia(items, "2026-06-28")
        b = _select_astronomia(items, "2026-06-29")
        # Al menos las 2 primeras son iguales (slot 1 y 2 fijos)
        assert a[0].source_id == b[0].source_id
        assert a[1].source_id == b[1].source_id
