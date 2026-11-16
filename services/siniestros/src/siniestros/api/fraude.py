"""Rutas internas del motor de fraude (HU29); la autorizacion la aplica ``create_app``."""

from typing import Any
from uuid import UUID

from flask import Flask, Response, g, jsonify, request
from siniestros.domain import Problem
from siniestros.domain.fraude import LimiteRevision, ReglaFraude, SolicitudDeteccion
from siniestros.infrastructure.fraude import MotorFraude


def _cuerpo() -> Any:
    if request.content_length is None or request.content_length > 16 * 1024:
        raise Problem(413, "limite_tamano")
    return request.get_json()


def registrar_rutas_fraude(app: Flask, motor: MotorFraude) -> None:
    @app.post("/internal/v1/reglas-fraude")
    def registrar_regla_fraude() -> tuple[Response, int]:
        registro, status = motor.registrar_regla(ReglaFraude.model_validate(_cuerpo()))
        return jsonify(registro), status

    @app.post("/internal/v1/siniestros/<uuid:siniestro_id>/deteccion-fraude")
    def detectar_fraude(siniestro_id: UUID) -> Response:
        entrada = SolicitudDeteccion.model_validate(_cuerpo())
        return jsonify(motor.detectar(siniestro_id, "manual", g.correlation_id, entrada.senales))

    @app.get("/internal/v1/siniestros/<uuid:siniestro_id>/deteccion-fraude")
    def consultar_deteccion_fraude(siniestro_id: UUID) -> Response:
        if request.content_length not in (None, 0):
            raise Problem(400, "solicitud_invalida")
        return jsonify(motor.consultar(siniestro_id))

    @app.post("/internal/v1/revision-fraude/pendientes")
    def listar_revision_fraude() -> Response:
        entrada = LimiteRevision.model_validate(_cuerpo())
        return jsonify(motor.pendientes(entrada.limite))
