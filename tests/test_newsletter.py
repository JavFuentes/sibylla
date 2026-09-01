from datetime import date
from email import message_from_bytes

import pytest

from sibylla import newsletter as nl
from sibylla import suscriptores as su
from sibylla.suscriptores import Suscriptor


def _card(n, **extra):
    out = {
        "id": f"n-{n}", "url": f"https://fuente.cl/{n}", "title": f"Título {n}",
        "source_name": "Fuente", "date": "25 jul 2026", "snippet": "A & B",
        "resumen": f"Resumen completo en español de la noticia {n}.",
        "seal_roman": "I", "is_video": False,
    }
    out.update(extra)
    return out


def _edicion():
    return {
        "schema": nl.EDICION_SCHEMA, "fecha": "2026-07-25", "generado": "25 jul · 11:08",
        "site_url": "https://sibylla.cl",
        "secciones": [
            {"id": tema, "label": tema, "cards": [_card(f"{tema}-{i}") for i in range(3)]}
            for tema in nl.TEMAS_VALIDOS
        ],
    }


def test_repartir_no_deja_tema_en_cero_y_respeta_tope():
    got = nl.repartir(_edicion()["secciones"], nl.TEMAS_VALIDOS)
    assert {s["id"] for s in got} == set(nl.TEMAS_VALIDOS)
    assert sum(len(s["cards"]) for s in got) == 12


def test_pendientes_excluye_enviados():
    subs = [Suscriptor("u1", "a@example.com", ("ai",)), Suscriptor("u2", "b@example.com", ("ai",))]
    assert [s.uid for s in nl.pendientes(subs, {"enviados": ["u1"]})] == ["u2"]


def test_render_texto_no_escapa_y_html_es_compatible():
    ed = _edicion()
    secciones = nl.repartir(ed["secciones"], ("ai",))
    composicion = nl.componer_boletin(
        secciones, ed["secciones"], fecha=ed["fecha"], uid="u1")
    html, texto = nl.render_correo(ed, composicion, site_url="https://sibylla.cl",
                                   baja_url="https://sibylla.cl/?boletin=baja",
                                   baja_mailto="mailto:baja@sibylla.cl?subject=baja")
    assert "&amp;" not in texto
    assert all(x not in html.lower() for x in ("<script", "<style", "<img", "class="))
    assert 'width="600"' in html
    assert composicion["destacada"]["resumen"] in html
    assert "Sibylla observa" not in html and "Sibylla observa" not in texto


def test_mensaje_tiene_un_to_y_sin_one_click():
    msg = nl.construir_mensaje(
        "a@example.com", "Sibylla · 25 jul", "<b>hola</b>", "hola",
        remitente="Sibylla <noticias@sibylla.cl>",
        baja_url="https://sibylla.cl/?boletin=baja",
        baja_mailto="mailto:baja@sibylla.cl?subject=baja",
    )
    assert msg.get_all("To") == ["a@example.com"]
    assert msg["List-Unsubscribe-Post"] is None
    assert msg["List-Id"] == "Sibylla <boletin.sibylla.cl>"
    assert msg["Auto-Submitted"] is None
    assert b"=?utf-8?" not in next(
        line for line in msg.as_bytes().splitlines() if line.startswith(b"List-Id:")
    ).lower()
    assert msg.is_multipart()


def test_edicion_excluye_social_y_poda_campos():
    card = _card(1, image="no debe quedar", otro="x")
    ctx = {
        "grupos": [{"id": "ai", "label": "IA", "cards": [card]}],
        "social_cards": [card], "astro_cards": [], "divulgacion_cards": [],
        "sibylla_cards": [], "t": {}, "total": 1, "n_fuentes": 1,
        "site_url": "https://sibylla.cl", "generado": "hoy",
    }
    ed = nl.edicion_desde_contexto(ctx, fecha=date(2026, 7, 25))
    assert [s["id"] for s in ed["secciones"]] == ["ai"]
    assert set(ed["secciones"][0]["cards"][0]) == set(nl.CAMPOS_TARJETA)


def test_enmascarar_no_deja_correo_legible():
    masked = nl._enmascarar("persona@gmail.com")
    assert "persona" not in masked and "gmail" not in masked and "@" in masked


def test_acuse_data_extrae_queue_id_sin_respuesta_completa():
    class BaseSMTP:
        def data(self, _msg):
            return 250, b"2.0.0 Ok: queued as HSTG-123_abc"

    class SMTP(nl._CapturaRespuestaData, BaseSMTP):
        pass

    smtp = SMTP()
    assert smtp.data(b"mensaje") == (250, b"2.0.0 Ok: queued as HSTG-123_abc")
    assert nl._acuse_data(smtp) == (250, "HSTG-123_abc")


def test_whitelist_python_es_una_sola_lista_logica():
    assert nl.TEMAS_VALIDOS == su.TEMAS_VALIDOS


def test_cobertura_excluye_videos_y_publicaciones_propias():
    secciones = [
        {"id": "ai", "cards": [_card("a"), _card("b", resumen=None)]},
        {"id": "divulgacion", "cards": [_card("v", is_video=True)]},
        {"id": "sibylla", "cards": [_card("s")]},
    ]
    assert nl.cobertura_resumenes(secciones) == (1, 2)


def test_destacada_prioriza_tier_y_no_el_orden_de_temas():
    secciones = [
        {"id": "nacional", "label": "Chile", "cards": [_card("n", seal_roman="III")]},
        {"id": "ai", "label": "IA", "cards": [_card("a", seal_roman="I")]},
    ]
    got = nl.componer_boletin(secciones, secciones, fecha="2026-07-25", uid="u1")
    assert got["destacada"]["id"] == "n-a"


def test_rotacion_destacada_es_determinista_y_varia():
    secciones = [
        {"id": tema, "label": tema, "cards": [_card(tema)]}
        for tema in ("nacional", "ai", "medicine", "astronomia")
    ]
    primera = nl.componer_boletin(secciones, secciones, fecha="2026-07-25", uid="u1")
    repetida = nl.componer_boletin(secciones, secciones, fecha="2026-07-25", uid="u1")
    assert primera["destacada"]["id"] == repetida["destacada"]["id"]
    elegidas = {
        nl.componer_boletin(secciones, secciones, fecha=f"2026-07-{dia:02d}", uid="u1")
        ["destacada"]["section_id"]
        for dia in range(20, 30)
    }
    assert len(elegidas) > 1


def test_video_y_sibylla_no_pueden_ser_destacada():
    secciones = [
        {"id": "divulgacion", "label": "Vídeos",
         "cards": [_card("v", is_video=True)]},
        {"id": "sibylla", "label": "SIBYLLA",
         "cards": [_card("s", resumen="Cuerpo Markdown completo")]}]
    assert nl.componer_boletin(secciones, secciones, fecha="2026-07-25", uid="u1") is None


def test_respaldo_editorial_global_y_breves_personalizadas():
    personales = [{"id": "divulgacion", "label": "Vídeos",
                   "cards": [_card("v", is_video=True, resumen=None)]}]
    globales = personales + [{"id": "ai", "label": "IA", "cards": [_card("a")]}]
    got = nl.componer_boletin(personales, globales, fecha="2026-07-25", uid="u1")
    assert got["respaldo_editorial"] is True
    assert got["destacada"]["section_id"] == "ai"
    assert [c["id"] for c in got["breves"]] == ["n-v"]


def test_breves_arrancan_despues_de_destacada_sin_repetir_y_maximo_cuatro():
    temas = ("nacional", "ai", "medicine", "astronomia", "divulgacion")
    secciones = [{"id": tema, "label": tema, "cards": [_card(tema)]} for tema in temas]
    got = nl.componer_boletin(secciones, secciones, fecha="2026-07-25", uid="u1")
    sid = got["destacada"]["section_id"]
    indice = temas.index(sid)
    esperadas = list(temas[indice + 1:] + temas[:indice])[:4]
    assert [c["section_id"] for c in got["breves"]] == esperadas
    assert len(got["breves"]) == 4
    assert got["destacada"]["id"] not in {c["id"] for c in got["breves"]}


def test_sibylla_entra_despues_de_primera_ronda_temática():
    secciones = [
        {"id": "ai", "label": "IA", "cards": [_card("a0"), _card("a1")]},
        {"id": "medicine", "label": "Medicina", "cards": [_card("m0"), _card("m1")]},
        {"id": "sibylla", "label": "SIBYLLA", "cards": [_card("s0")]},
    ]
    got = nl.componer_boletin(secciones, secciones, fecha="2026-07-25", uid="u1")
    ids = [c["section_id"] for c in got["breves"]]
    assert "sibylla" in ids
    assert ids.index("sibylla") >= 1


def test_resumen_anomalo_se_corta_en_frase():
    largo = ("Primera frase suficientemente extensa. " * 60) + "Final"
    got = nl._limitar_resumen(largo, 180)
    assert len(got) <= 180 and got.endswith(".")


def test_asunto_se_acota_y_elimina_crlf():
    asunto = nl.construir_asunto("Titular válido\r\nBcc: atacante@example.com " + "muy " * 40)
    assert "\r" not in asunto and "\n" not in asunto and len(asunto) <= nl.MAX_ASUNTO
    assert "Bcc:" in asunto  # queda como texto, nunca como una cabecera nueva


def test_enviar_un_mensaje_por_destinatario_y_flushea(monkeypatch, tmp_path):
    enviados = []
    class SMTP:
        def login(self, *_): pass
        def send_message(self, msg): enviados.append(msg)
        def quit(self): pass
    monkeypatch.setattr(nl, "_abrir_smtp", lambda _cfg: SMTP())
    estado = nl._estado_nuevo("2026-07-25", 2)
    cfg = nl.SmtpConfig("smtp", 465, "u", "p", "Sibylla <noticias@sibylla.cl>", throttle_s=0)
    subs = [Suscriptor("u1", "a@example.com", ("ai",)), Suscriptor("u2", "b@example.com", ("medicine",))]
    nl.enviar(_edicion(), subs, estado=estado, cfg=cfg, site_url="https://sibylla.cl",
              tope=10, estado_path=tmp_path / "state.json")
    assert [m["To"] for m in enviados] == ["a@example.com", "b@example.com"]
    assert set(estado["enviados"]) == {"u1", "u2"}


def test_destinatario_que_falla_no_aborta(monkeypatch, tmp_path):
    enviados = []
    class SMTP:
        def send_message(self, msg):
            if msg["To"] == "a@example.com":
                import smtplib
                raise smtplib.SMTPRecipientsRefused({})
            enviados.append(msg["To"])
        def quit(self): pass
    monkeypatch.setattr(nl, "_abrir_smtp", lambda _cfg: SMTP())
    estado = nl._estado_nuevo("2026-07-25", 2)
    cfg = nl.SmtpConfig("smtp", 465, "u", "p", "Sibylla <noticias@sibylla.cl>", throttle_s=0)
    subs = [Suscriptor("u1", "a@example.com", ("ai",)), Suscriptor("u2", "b@example.com", ("medicine",))]
    nl.enviar(_edicion(), subs, estado=estado, cfg=cfg, site_url="https://sibylla.cl",
              tope=10, estado_path=tmp_path / "state.json")
    assert enviados == ["b@example.com"] and estado["fallidos"][0]["uid"] == "u1"


def test_sin_resumen_no_abre_smtp_y_deja_uid_pendiente(monkeypatch, tmp_path):
    edicion = _edicion()
    for seccion in edicion["secciones"]:
        for card in seccion["cards"]:
            card["resumen"] = None
    monkeypatch.setattr(nl, "_abrir_smtp", lambda _cfg: pytest.fail("no debe abrir SMTP"))
    estado = nl._estado_nuevo("2026-07-25", 1)
    cfg = nl.SmtpConfig("smtp", 465, "u", "p", "noticias@sibylla.cl", throttle_s=0)
    sub = Suscriptor("u1", "a@example.com", ("ai",))
    nl.enviar(edicion, [sub], estado=estado, cfg=cfg, site_url="https://sibylla.cl",
              tope=1, estado_path=tmp_path / "state.json")
    assert estado["enviados"] == [] and estado["omitidos"] == ["u1"]
    assert [s.uid for s in nl.pendientes([sub], estado)] == ["u1"]
    assert estado["terminado"] is False


def test_dry_run_sin_resumen_no_modifica_estado(monkeypatch, tmp_path):
    edicion = _edicion()
    for seccion in edicion["secciones"]:
        for card in seccion["cards"]:
            card["resumen"] = None
    monkeypatch.setattr(nl, "_abrir_smtp", lambda _cfg: pytest.fail("no debe abrir SMTP"))
    estado = nl._estado_nuevo("2026-07-25", 1)
    original = {**estado, "enviados": [], "omitidos": [], "fallidos": []}
    cfg = nl.SmtpConfig("dry", 465, "u", "p", "noticias@sibylla.cl", throttle_s=0)
    nl.enviar(edicion, [Suscriptor("u1", "a@example.com", ("ai",))],
              estado=estado, cfg=cfg, site_url="https://sibylla.cl", tope=1,
              estado_path=tmp_path / "state.json", dry_run=True)
    assert estado == original and not (tmp_path / "state.json").exists()


def test_dry_run_no_avanza_estado(tmp_path):
    estado = nl._estado_nuevo("2026-07-25", 1)
    original = dict(estado)
    cfg = nl.SmtpConfig("dry", 465, "u", "p", "Sibylla <noticias@sibylla.cl>", throttle_s=0)
    nl.enviar(_edicion(), [Suscriptor("u1", "a@example.com", ("ai",))],
              estado=estado, cfg=cfg, site_url="https://sibylla.cl", tope=1,
              estado_path=tmp_path / "state.json", dry_run=True)
    assert estado == original and not (tmp_path / "state.json").exists()


def test_envio_devuelve_el_resultado_actualizado(monkeypatch, tmp_path):
    class SMTP:
        def send_message(self, _msg): pass
        def quit(self): pass
    monkeypatch.setattr(nl, "_abrir_smtp", lambda _cfg: SMTP())
    estado = nl._estado_nuevo("2026-07-25", 1)
    cfg = nl.SmtpConfig("smtp", 465, "u", "p", "noticias@sibylla.cl", throttle_s=0)
    resultado = nl.enviar(
        _edicion(), [Suscriptor("u1", "a@example.com", ("ai",))],
        estado=estado, cfg=cfg, site_url="https://sibylla.cl", tope=1,
        estado_path=tmp_path / "state.json",
    )
    assert resultado is estado
    assert resultado["enviados"] == ["u1"]


def test_tope_deja_estado_reanudable(monkeypatch, tmp_path):
    class SMTP:
        def send_message(self, _msg): pass
        def quit(self): pass
    monkeypatch.setattr(nl, "_abrir_smtp", lambda _cfg: SMTP())
    estado = nl._estado_nuevo("2026-07-25", 2)
    cfg = nl.SmtpConfig("smtp", 465, "u", "p", "noticias@sibylla.cl", throttle_s=0)
    subs = [Suscriptor("u1", "a@example.com", ("ai",)), Suscriptor("u2", "b@example.com", ("ai",))]
    nl.enviar(_edicion(), subs, estado=estado, cfg=cfg, site_url="https://sibylla.cl",
              tope=1, estado_path=tmp_path / "state.json")
    assert estado["terminado"] is False and estado["enviados"] == ["u1"]


def test_cero_suscriptores_alta_posterior_mismo_dia_se_envia(monkeypatch):
    """Regresión del lanzamiento: `terminado` no impide releer Firestore."""
    estado = nl._estado_nuevo("2026-07-25", 0)
    estado["terminado"] = True
    nuevo = Suscriptor("u-nuevo", "nuevo@example.com", ("ai",))
    lectura = su.LecturaSuscriptores(ok=True, suscriptores=(nuevo,), examinados=1)
    capturados = []

    monkeypatch.setattr(nl, "load_env", lambda: None)
    monkeypatch.setattr(nl, "cargar_edicion", _edicion)
    monkeypatch.setattr(nl, "_hoy", lambda: "2026-07-25")
    monkeypatch.setattr(nl, "cargar_estado", lambda: estado)
    monkeypatch.setattr(su, "fetch_suscriptores", lambda: lectura)
    monkeypatch.setattr(
        nl, "smtp_config_desde_entorno",
        lambda: nl.SmtpConfig("smtp", 465, "u", "p", "noticias@sibylla.cl"),
    )
    monkeypatch.delenv("SIBYLLA_NEWSLETTER_TEST_TO", raising=False)
    monkeypatch.setenv("SIBYLLA_NEWSLETTER_DRYRUN", "0")

    def fake_enviar(_edicion_arg, lista, *, estado, **_kwargs):
        capturados.extend(lista)
        estado["enviados"].extend(s.uid for s in lista)
        estado["terminado"] = True
        return estado

    monkeypatch.setattr(nl, "enviar", fake_enviar)
    assert nl.enviar_boletin_cli() == 0
    assert [s.uid for s in capturados] == ["u-nuevo"]


def test_fallo_firestore_no_intenta_enviar_ni_modifica_estado(monkeypatch):
    estado = nl._estado_nuevo("2026-07-25", 2)
    original = {**estado, "enviados": list(estado["enviados"])}
    monkeypatch.setattr(nl, "load_env", lambda: None)
    monkeypatch.setattr(nl, "cargar_edicion", _edicion)
    monkeypatch.setattr(nl, "_hoy", lambda: "2026-07-25")
    monkeypatch.setattr(nl, "cargar_estado", lambda: estado)
    monkeypatch.setattr(
        su, "fetch_suscriptores",
        lambda: su.LecturaSuscriptores(ok=False, error="Timeout"),
    )
    monkeypatch.setattr(
        nl, "smtp_config_desde_entorno",
        lambda: nl.SmtpConfig("smtp", 465, "u", "p", "noticias@sibylla.cl"),
    )
    monkeypatch.setattr(nl, "enviar", lambda *_a, **_k: pytest.fail("no debe enviar"))
    monkeypatch.delenv("SIBYLLA_NEWSLETTER_TEST_TO", raising=False)
    monkeypatch.setenv("SIBYLLA_NEWSLETTER_DRYRUN", "0")
    assert nl.enviar_boletin_cli() == 0
    assert estado == original


def test_fecha_editorial_usa_la_misma_fuente_en_build_y_envio(monkeypatch):
    monkeypatch.setattr(nl, "_hoy", lambda: "2026-07-25")
    ctx = {
        "grupos": [], "astro_cards": [], "divulgacion_cards": [],
        "sibylla_cards": [], "t": {},
    }
    assert nl.edicion_desde_contexto(ctx)["fecha"] == nl._hoy()
