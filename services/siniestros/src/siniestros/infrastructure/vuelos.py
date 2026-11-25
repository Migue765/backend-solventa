"""Adaptador Open Data simulado sobre contratos emitidos por la compra normal."""

import json
from datetime import UTC, datetime, time, timedelta
from http.client import HTTPException
from urllib.request import Request, build_opener
from uuid import UUID, uuid5

from pydantic import AwareDatetime, BaseModel, Field, StrictBool
from siniestros.domain import Config, Problem
from siniestros.domain.parametricos import EventoParametrico, ReglaParametrica
from siniestros.policies import NoRedirect

FUENTE_VUELOS = "opendata-vuelos-simulado"
TIPO_VUELO = "vuelo_cancelado_clima"
NAMESPACE_VUELOS = UUID("f0c48204-cf0d-475d-8ecd-fc4b90b38ffd")


class Contrato(BaseModel):
    polizaId: UUID
    clienteId: UUID
    emitidaEn: AwareDatetime
    airline: str
    flightNumber: str
    flightDate: str
    coverageType: str
    montoIndemnizacion: str = Field(pattern=r"^[0-9]+\.[0-9]{2}$")
    moneda: str = Field(pattern=r"^[A-Z]{3}$")
    contratadaEn: AwareDatetime
    cancelacionClima: StrictBool


class Contratos(BaseModel):
    contratos: list[Contrato]


def preparar_reglas(
    config: Config, fuente: str, evento: EventoParametrico
) -> list[ReglaParametrica]:
    if evento.vuelo is None:
        if evento.tipoEvento == TIPO_VUELO:
            raise Problem(422, "vuelo_requerido")
        return []
    vuelo = evento.vuelo
    if (
        fuente != FUENTE_VUELOS
        or evento.tipoEvento != TIPO_VUELO
        or evento.metrica.nombre != "cancelacion"
        or evento.metrica.unidad != "boolean"
        or evento.metrica.valor != 1
        or evento.ocurridoEn.date() != vuelo.flightDate
        or evento.ocurridoEn > datetime.now(UTC)
    ):
        raise Problem(422, "evento_vuelo_invalido")
    peticion = Request(
        f"{config.policies_url}/v1/contratos-vuelo/{vuelo.flightDate.isoformat()}",
        headers={"Authorization": f"Bearer {config.internal_token}"},
    )
    try:
        with build_opener(NoRedirect).open(peticion, timeout=5) as respuesta:
            raw = respuesta.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("Limite de contratos excedido.")
            contratos = Contratos.model_validate(json.loads(raw)).contratos
    except (OSError, HTTPException, ValueError) as error:
        raise Problem(503, "contratos_vuelo_no_disponibles") from error
    reglas: list[ReglaParametrica] = []
    for contrato in contratos:
        if (
            contrato.airline.strip().casefold() != vuelo.airline.strip().casefold()
            or contrato.flightNumber != vuelo.flightNumber
            or contrato.flightDate != vuelo.flightDate.isoformat()
            or not contrato.cancelacionClima
            or contrato.coverageType == "missed-connection"
            or contrato.contratadaEn >= evento.ocurridoEn
            or contrato.emitidaEn >= evento.ocurridoEn
        ):
            continue
        inicio = datetime.combine(vuelo.flightDate, time.min, tzinfo=UTC)
        reglas.append(
            ReglaParametrica.model_validate(
                {
                    "reglaId": uuid5(NAMESPACE_VUELOS, f"{evento.eventoId}:{contrato.polizaId}"),
                    "version": 1,
                    "fuente": fuente,
                    "tipoEvento": TIPO_VUELO,
                    "metrica": "cancelacion",
                    "operador": "eq",
                    "umbral": 1,
                    "unidad": "boolean",
                    "ubicacionCodigo": str(evento.eventoId),
                    "clienteId": contrato.clienteId,
                    "polizaId": contrato.polizaId,
                    "beneficiarioId": contrato.clienteId,
                    "montoIndemnizacion": contrato.montoIndemnizacion,
                    "moneda": contrato.moneda,
                    "vigenteDesde": inicio.isoformat(),
                    "vigenteHasta": (inicio + timedelta(days=1)).isoformat(),
                }
            )
        )
    return reglas
