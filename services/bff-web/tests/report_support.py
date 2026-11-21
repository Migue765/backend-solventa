"""Datos de fuentes sin PII para las pruebas de reportes operativos (SOL-322)."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from bff_web.domain.operational_reports import (
    ClaimRecord,
    PaymentRecord,
    PolicyRecord,
    PremiumRecord,
    ReportSnapshot,
    ReportSourceUnavailableError,
    SourceTrace,
)

CUT_OFF = datetime(2026, 10, 14, 11, 0, tzinfo=UTC)
CUT_OFF_TEXT = "2026-10-14T11:00:00Z"
P1 = "11111111-1111-4111-8111-111111111111"
P2 = "22222222-2222-4222-8222-222222222222"
P3 = "33333333-3333-4333-8333-333333333333"
A1 = "a1a1a1a1-a1a1-41a1-81a1-a1a1a1a1a1a1"
A2 = "a2a2a2a2-a2a2-42a2-82a2-a2a2a2a2a2a2"
A3 = "a3a3a3a3-a3a3-43a3-83a3-a3a3a3a3a3a3"
A4 = "a4a4a4a4-a4a4-44a4-84a4-a4a4a4a4a4a4"
C1 = "c1c1c1c1-c1c1-41c1-81c1-c1c1c1c1c1c1"
C2 = "c2c2c2c2-c2c2-42c2-82c2-c2c2c2c2c2c2"
C3 = "c3c3c3c3-c3c3-43c3-83c3-c3c3c3c3c3c3"
UNKNOWN_POLICY = "99999999-9999-4999-8999-999999999999"
G1 = "d1d1d1d1-d1d1-41d1-81d1-d1d1d1d1d1d1"
G2 = "d2d2d2d2-d2d2-42d2-82d2-d2d2d2d2d2d2"


def digest(content: object) -> str:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def policies() -> list[dict[str, Any]]:
    return [
        {
            "id": P1,
            "tipo": "automotive",
            "pagoId": A1,
            "emitidaEn": "2026-10-02T10:00:00Z",
            "fechaInicio": "2026-10-02",
            "fechaFin": "2026-11-02",
            "estado": "vigente",
        },
        {
            "id": P2,
            "tipo": "travel",
            "pagoId": A2,
            "emitidaEn": "2026-09-20T08:00:00Z",
            "fechaInicio": "2026-09-25",
            "fechaFin": "2026-10-30",
            "estado": "vigente",
        },
        {
            "id": P3,
            "tipo": "life",
            "pagoId": A3,
            "emitidaEn": "2026-10-05T15:30:00Z",
            "fechaInicio": "2026-10-20",
            "fechaFin": "2026-11-20",
            "estado": "programada",
        },
    ]


def premiums() -> list[dict[str, Any]]:
    return [
        {
            "pagoId": A1,
            "producto": "automotive",
            "monto": "1200.50",
            "moneda": "COP",
            "confirmadaEn": "2026-10-02T09:59:00Z",
        },
        {
            "pagoId": A2,
            "producto": "travel",
            "monto": "300.00",
            "moneda": "COP",
            "confirmadaEn": "2026-09-20T07:59:00Z",
        },
        {
            "pagoId": A4,
            "producto": "automotive",
            "monto": "100.00",
            "moneda": "USD",
            "confirmadaEn": "2026-10-03T12:00:00Z",
        },
    ]


def claims() -> list[dict[str, Any]]:
    return [
        {
            "id": C1,
            "polizaId": P1,
            "tipo": None,
            "estado": "en_evaluacion",
            "reportadoEn": "2026-10-06T10:00:00Z",
        },
        {
            "id": C2,
            "polizaId": P2,
            "tipo": "parametrico",
            "estado": "aprobado",
            "reportadoEn": "2026-10-07T10:00:00Z",
        },
        {
            "id": C3,
            "polizaId": UNKNOWN_POLICY,
            "tipo": "asistido",
            "estado": "reportado",
            "reportadoEn": "2026-10-08T10:00:00Z",
        },
    ]


def payments() -> list[dict[str, Any]]:
    return [
        {
            "id": G1,
            "siniestroId": C2,
            "estado": "confirmado",
            "monto": "250000.00",
            "moneda": "COP",
            "creadoEn": "2026-10-07T10:05:00Z",
        },
        {
            "id": G2,
            "siniestroId": C2,
            "estado": "pendiente",
            "monto": "10.00",
            "moneda": "COP",
            "creadoEn": "2026-10-08T10:05:00Z",
        },
    ]


def policies_payload(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    items = policies() if records is None else records
    return {
        "corte": CUT_OFF_TEXT,
        "fuente": "polizas",
        "registros": len(items),
        "huella": digest(items),
        "polizas": items,
    }


def premiums_payload(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    items = premiums() if records is None else records
    return {
        "corte": CUT_OFF_TEXT,
        "fuente": "primas",
        "registros": len(items),
        "huella": digest(items),
        "primas": items,
    }


def claims_payload(
    claim_records: list[dict[str, Any]] | None = None,
    payment_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    claim_items = claims() if claim_records is None else claim_records
    payment_items = payments() if payment_records is None else payment_records
    return {
        "corte": CUT_OFF_TEXT,
        "fuente": "siniestros",
        "registros": len(claim_items) + len(payment_items),
        "huella": digest({"siniestros": claim_items, "pagos": payment_items}),
        "siniestros": claim_items,
        "pagos": payment_items,
    }


def snapshot(cut_off: datetime = CUT_OFF) -> ReportSnapshot:
    policy_items = policies()
    premium_items = premiums()
    claim_items = claims()
    payment_items = payments()
    return ReportSnapshot(
        cut_off,
        tuple(PolicyRecord.model_validate(item) for item in policy_items),
        tuple(PremiumRecord.model_validate(item) for item in premium_items),
        tuple(ClaimRecord.model_validate(item) for item in claim_items),
        tuple(PaymentRecord.model_validate(item) for item in payment_items),
        (
            SourceTrace("polizas", len(policy_items), digest(policy_items)),
            SourceTrace("primas", len(premium_items), digest(premium_items)),
            SourceTrace(
                "siniestros",
                len(claim_items) + len(payment_items),
                digest({"siniestros": claim_items, "pagos": payment_items}),
            ),
        ),
    )


class FakeReportSources:
    def __init__(self, failing_source: str | None = None) -> None:
        self.failing_source = failing_source
        self.calls: list[tuple[datetime, str]] = []

    def fetch(self, cut_off: datetime, correlation_id: str) -> ReportSnapshot:
        self.calls.append((cut_off, correlation_id))
        if self.failing_source is not None:
            raise ReportSourceUnavailableError(self.failing_source)
        return snapshot(cut_off)
