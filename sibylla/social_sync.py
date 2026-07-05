"""Sincronización build-time de conteos sociales desde Firestore.

El sitio sigue siendo estático: esta lectura solo hornea una foto de los
conteos en el HTML. Si Firestore no responde, se devuelve `{}` y el orden
editorial queda intacto.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

log = logging.getLogger("sibylla")

# Config pública de la app web Firebase. No son secretos; la seguridad vive en
# reglas de Firestore, dominios autorizados y App Check.
FIREBASE_API_KEY = "AIzaSyCVa11offl3SebRwlK5ZP7viP9vHPiMp0U"
FIREBASE_PROJECT_ID = "sibylla-a81d2"


def _int_value(raw: dict[str, Any] | None) -> int:
    """Convierte un valor REST de Firestore a int, tolerando campos ausentes."""
    if not isinstance(raw, dict):
        return 0
    for key in ("integerValue", "doubleValue"):
        if key in raw:
            try:
                return int(raw[key])
            except (TypeError, ValueError):
                return 0
    return 0


def _parse_conteos_doc(payload: dict[str, Any]) -> dict[str, dict[str, int]]:
    fields = payload.get("fields") or {}
    if not isinstance(fields, dict):
        return {}
    out: dict[str, dict[str, int]] = {}
    for card_id, raw in fields.items():
        card_fields = ((raw or {}).get("mapValue") or {}).get("fields") or {}
        if not isinstance(card_fields, dict):
            continue
        out[card_id] = {
            "l": max(0, _int_value(card_fields.get("l"))),
            "d": max(0, _int_value(card_fields.get("d"))),
            "c": max(0, _int_value(card_fields.get("c"))),
        }
    return out


def fetch_conteos(api_key: str = FIREBASE_API_KEY,
                  project_id: str = FIREBASE_PROJECT_ID) -> dict[str, dict[str, int]]:
    """Lee `agregados/conteos` por REST y devuelve `{cardId: {l,d,c}}`.

    Fallo aislado por diseño: timeout, 404, JSON malformado o cualquier error
    HTTP se registran como warning y devuelven `{}`.
    """
    url = ("https://firestore.googleapis.com/v1/projects/"
           f"{project_id}/databases/(default)/documents/agregados/conteos")
    try:
        resp = requests.get(url, params={"key": api_key}, timeout=10)
        if resp.status_code == 404:
            log.warning("conteos sociales: doc agregados/conteos no existe")
            return {}
        resp.raise_for_status()
        return _parse_conteos_doc(resp.json())
    except Exception as ex:  # noqa: BLE001 - fallo aislado del build estático
        log.warning("conteos sociales: no se pudieron leer (%s)", ex)
        return {}
