"""Sincronización build-time de conteos sociales desde Firestore.

El sitio sigue siendo estático: esta lectura solo hornea una foto de los
conteos en el HTML. Si Firestore no responde, se devuelve ``{}`` y el orden
editorial queda intacto.

Autenticación:
- Si hay credenciales de *service account* (env ``SIBYLLA_FIREBASE_SA_JSON``
  con el JSON inline, o ``GOOGLE_APPLICATION_CREDENTIALS`` con la ruta al
  JSON), se lee ``agregados/conteos`` con un token OAuth de la SA (header
  ``Authorization: Bearer``). Es el camino que sobrevive al *Enforce* de
  App Check.
- Sin credenciales, se usa el REST anónimo con la API key pública (camino
  histórico). Tras activar Enforce este devuelve ``{}`` (lectura anónima
  bloqueada): la degradación «sitio sin números» ya está diseñada y no rompe
  el build.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

log = logging.getLogger("sibylla")

# Config pública de la app web Firebase. No son secretos; la seguridad vive en
# reglas de Firestore, dominios autorizados y App Check.
FIREBASE_API_KEY = "AIzaSyCVa11offl3SebRwlK5ZP7viP9vHPiMp0U"
FIREBASE_PROJECT_ID = "sibylla-a81d2"

_DATASTORE_SCOPE = "https://www.googleapis.com/auth/datastore"


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


def _conteos_url(project_id: str) -> str:
    return ("https://firestore.googleapis.com/v1/projects/"
            f"{project_id}/databases/(default)/documents/agregados/conteos")


def _load_sa_credentials():
    """Devuelve credenciales de service account si hay, o ``None`` (anónimo)."""
    inline = os.getenv("SIBYLLA_FIREBASE_SA_JSON")
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    info: dict[str, Any] | None = None
    try:
        if inline:
            info = json.loads(inline)
        elif path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                info = json.load(fh)
    except Exception as ex:  # noqa: BLE001
        log.warning("conteos sociales: credenciales SA ilegibles (%s); uso anónimo", ex)
        return None
    if not info:
        return None
    try:
        from google.oauth2 import service_account
    except Exception as ex:  # noqa: BLE001 - google-auth es dependencia opcional en runtime
        log.warning("conteos sociales: google-auth no disponible (%s); uso anónimo", ex)
        return None
    try:
        return service_account.Credentials.from_service_account_info(
            info, scopes=[_DATASTORE_SCOPE])
    except Exception as ex:  # noqa: BLE001
        log.warning("conteos sociales: no se pudo crear credencial SA (%s); uso anónimo", ex)
        return None


def _fetch_conteos_authed(creds, project_id: str) -> dict[str, dict[str, int]]:
    """Lee con token OAuth del service account (sin API key). Lanza en error."""
    from google.auth.transport import requests as grequests
    creds.refresh(grequests.Request())  # obtiene/refresca el access token
    resp = requests.get(_conteos_url(project_id),
                        headers={"Authorization": f"Bearer {creds.token}"}, timeout=10)
    if resp.status_code == 404:
        log.warning("conteos sociales: doc agregados/conteos no existe")
        return {}
    resp.raise_for_status()
    return _parse_conteos_doc(resp.json())


def _fetch_conteos_anon(api_key: str, project_id: str) -> dict[str, dict[str, int]]:
    """Lee de forma anónima con la API key pública. Fallo aislado → ``{}``."""
    try:
        resp = requests.get(_conteos_url(project_id), params={"key": api_key}, timeout=10)
        if resp.status_code == 404:
            log.warning("conteos sociales: doc agregados/conteos no existe")
            return {}
        resp.raise_for_status()
        return _parse_conteos_doc(resp.json())
    except Exception as ex:  # noqa: BLE001 - fallo aislado del build estático
        log.warning("conteos sociales: no se pudieron leer (%s)", ex)
        return {}


def fetch_conteos(api_key: str = FIREBASE_API_KEY,
                  project_id: str = FIREBASE_PROJECT_ID) -> dict[str, dict[str, int]]:
    """Lee ``agregados/conteos`` y devuelve ``{cardId: {l,d,c}}``.

    Usa *service account* si hay credenciales (sobrevive al Enforce de App
    Check); si no, o si la lectura autenticada falla, cae al REST anónimo. Todo
    fallo se registra como warning y devuelve ``{}``.
    """
    creds = _load_sa_credentials()
    if creds is not None:
        try:
            return _fetch_conteos_authed(creds, project_id)
        except Exception as ex:  # noqa: BLE001
            log.warning("conteos sociales: fallo la lectura con SA (%s); intento anónimo", ex)
    return _fetch_conteos_anon(api_key, project_id)
