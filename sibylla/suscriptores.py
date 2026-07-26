"""Lectura build-time de suscripciones al boletín desde Firestore.

Solo existe el camino autenticado con service account. Las reglas de Firestore
impiden listar la colección desde el cliente y cualquier fallo devuelve una
lista vacía para que el correo nunca rompa la publicación del sitio.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import requests

from .social_sync import FIREBASE_PROJECT_ID, load_sa_credentials

log = logging.getLogger("sibylla")

# Debe coincidir con TEMAS_VALIDOS de newsletter.py, temasBoletin() de
# firestore.rules y TEMAS_BOLETIN de static/social.js.
TEMAS_VALIDOS = ("nacional", "ai", "medicine", "astronomia", "divulgacion")


@dataclass(frozen=True)
class Suscriptor:
    uid: str
    email: str
    temas: tuple[str, ...]


@dataclass(frozen=True)
class LecturaSuscriptores:
    """Resultado inequívoco de la lectura REST de Firestore."""

    ok: bool
    suscriptores: tuple[Suscriptor, ...] = ()
    examinados: int = 0
    error: str | None = None


def _valor(raw: Any) -> Any:
    """Convierte el formato tipado de la API REST de Firestore."""
    if not isinstance(raw, dict):
        return None
    if "stringValue" in raw:
        return raw["stringValue"]
    if "booleanValue" in raw:
        return raw["booleanValue"]
    if "integerValue" in raw:
        try:
            return int(raw["integerValue"])
        except (TypeError, ValueError):
            return None
    if "arrayValue" in raw:
        return [_valor(v) for v in (raw["arrayValue"].get("values") or [])]
    return None


def _parse_doc(doc: dict[str, Any]) -> Suscriptor | None:
    fields = doc.get("fields") or {}
    if not isinstance(fields, dict):
        return None
    v = _valor(fields.get("v"))
    activa = _valor(fields.get("activa"))
    email = _valor(fields.get("email"))
    uid = _valor(fields.get("uid"))
    temas_raw = _valor(fields.get("temas")) or []
    if v != 1 or activa is not True:
        return None
    if not isinstance(uid, str) or not uid.strip():
        return None
    if not isinstance(email, str) or "@" not in email or len(email) > 254:
        return None
    temas = tuple(t for t in temas_raw if isinstance(t, str) and t in TEMAS_VALIDOS)
    if not temas:
        return None
    return Suscriptor(uid=uid.strip(), email=email.strip(), temas=temas)


def fetch_suscriptores(project_id: str = FIREBASE_PROJECT_ID, *,
                       page_size: int = 300, max_docs: int = 5000) -> LecturaSuscriptores:
    """Lista ``suscripciones`` paginando con ``pageToken``.

    Distingue una colección correctamente leída y vacía de un fallo de
    autenticación, red o formato. Nunca intenta una lectura anónima.
    """
    try:
        creds = load_sa_credentials()
        if creds is None:
            log.warning("boletín: no hay credenciales de service account; no se leen suscriptores")
            return LecturaSuscriptores(ok=False, error="credenciales_ausentes")
        from google.auth.transport import requests as grequests
        creds.refresh(grequests.Request())
        url = ("https://firestore.googleapis.com/v1/projects/"
               f"{project_id}/databases/(default)/documents/suscripciones")
        out: list[Suscriptor] = []
        scanned = 0
        token: str | None = None
        while scanned < max_docs:
            params: dict[str, Any] = {"pageSize": min(max(1, page_size), 1000)}
            if token:
                params["pageToken"] = token
            resp = requests.get(
                url, params=params,
                headers={"Authorization": f"Bearer {creds.token}"}, timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
            docs = payload.get("documents") or []
            if not isinstance(docs, list):
                raise ValueError("respuesta Firestore sin documents")
            for doc in docs:
                scanned += 1
                parsed = _parse_doc(doc)
                if parsed is not None:
                    out.append(parsed)
                    if scanned >= max_docs:
                        break
            token = payload.get("nextPageToken")
            if not token:
                break
        return LecturaSuscriptores(
            ok=True, suscriptores=tuple(out), examinados=scanned,
        )
    except Exception as ex:  # noqa: BLE001 - fallo aislado del envío
        log.warning("boletín: no se pudieron leer suscriptores (%s)", ex)
        return LecturaSuscriptores(ok=False, error=type(ex).__name__)
