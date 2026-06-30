# -*- coding: utf-8 -*-
"""Tests del selector _select_divulgacion (sección Divulgación)."""
from datetime import datetime, timedelta, timezone

from sibylla.models import NewsItem
from sibylla.web import DIVULGACION_FRESH_DAYS, DIVULGACION_MAX_TOTAL, _is_divulgacion, _select_divulgacion

_NOW = datetime.now(timezone.utc)


def _item(source_id: str, hours_ago: float = 1, title: str = "") -> NewsItem:
    return NewsItem(
        title=title or f"Video de {source_id} ({hours_ago}h)",
        url=f"https://www.youtube.com/watch?v={source_id}-{hours_ago}",
        source_id=source_id,
        source_name=source_id.upper(),
        published=_NOW - timedelta(hours=hours_ago),
        summary="",
        topics=["divulgacion"],
        tier=3,
    )


def test_is_divulgacion_exige_topic_y_fuente_youtube():
    assert _is_divulgacion(_item("yt_quantumfracture"))


def test_is_divulgacion_rechaza_fuente_no_youtube():
    assert not _is_divulgacion(_item("techcrunch"))


def test_mas_de_seis_canales_devuelve_seis_distintos_y_recientes():
    items = [_item(f"yt_canal{i}", hours_ago=i + 1) for i in range(8)]
    sel = _select_divulgacion(items)
    assert [it.source_id for it in sel] == [f"yt_canal{i}" for i in range(DIVULGACION_MAX_TOTAL)]


def test_un_canal_con_varios_videos_aporta_el_mas_reciente():
    items = [_item("yt_a", hours_ago=20), _item("yt_a", hours_ago=2), _item("yt_b", hours_ago=3)]
    sel = _select_divulgacion(items)
    assert [(it.source_id, it.published) for it in sel] == [("yt_a", items[1].published), ("yt_b", items[2].published)]


def test_menos_de_seis_canales_devuelve_los_disponibles():
    sel = _select_divulgacion([_item("yt_a"), _item("yt_b"), _item("yt_c")])
    assert len(sel) == 3


def test_video_fuera_de_ventana_queda_excluido():
    stale_hours = (DIVULGACION_FRESH_DAYS + 1) * 24
    sel = _select_divulgacion([_item("yt_viejo", hours_ago=stale_hours), _item("yt_nuevo", hours_ago=1)])
    assert [it.source_id for it in sel] == ["yt_nuevo"]


def test_lista_vacia():
    assert _select_divulgacion([]) == []


def test_sin_fecha_no_rompe_y_queda_excluido_por_frescura():
    it = _item("yt_sin_fecha")
    it.published = None
    assert _select_divulgacion([it]) == []
