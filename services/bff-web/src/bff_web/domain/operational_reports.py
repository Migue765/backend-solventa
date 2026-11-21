"""Consolidacion de reportes operativos (HU36 / SOL-322).

Los indicadores se calculan sobre registros sin datos personales entregados por
las fuentes propietarias (polizas, cotizacion y siniestros) para un mismo corte.
La huella del reporte permite comprobar que una descarga corresponde al
resultado confirmado para ese corte.
"""

import csv
import hashlib
import io
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

PRODUCTS = (
    "automotive",
    "travel",
    "life",
    "device-protection",
    "parametric",
    "parametric-weather",
    "payment-protection",
    "mortgage-life",
)
POLICY_STATES = ("programada", "vigente", "vencida", "cancelada")
CLAIM_STATES = (
    "reportado",
    "en_evaluacion",
    "en_revision",
    "en_inspeccion",
    "aprobado",
    "rechazado",
    "pagado",
    "cerrado",
)
CLAIM_TYPES = ("asistido", "parametrico", "sin_tipo")
PAYMENT_STATES = ("pendiente", "confirmado", "rechazado")
FILTERS = frozenset({"desde", "hasta", "producto", "estado", "corte"})
MAX_PERIOD_DAYS = 366
DEFAULT_CUT_OFF_DELAY = timedelta(seconds=60)
CENT = Decimal("0.01")

Product = Literal[
    "automotive",
    "travel",
    "life",
    "device-protection",
    "parametric",
    "parametric-weather",
    "payment-protection",
    "mortgage-life",
]
Money = Field(pattern=r"^\d{1,16}\.\d{2}$")
Currency = Field(pattern=r"^[A-Z]{3}$")


class ReportSourceUnavailableError(Exception):
    def __init__(self, source: str) -> None:
        super().__init__(source)
        self.source = source


class InvalidReportFilterError(ValueError):
    pass


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PolicyRecord(_Record):
    id: UUID
    tipo: Product
    pagoId: UUID
    emitidaEn: AwareDatetime
    fechaInicio: date
    fechaFin: date
    estado: Literal["programada", "vigente", "vencida", "cancelada"]


class PremiumRecord(_Record):
    pagoId: UUID
    producto: Product
    monto: str = Money
    moneda: str = Currency
    confirmadaEn: AwareDatetime


class ClaimRecord(_Record):
    id: UUID
    polizaId: UUID
    tipo: Literal["asistido", "parametrico"] | None
    estado: Literal[
        "reportado",
        "en_evaluacion",
        "en_revision",
        "en_inspeccion",
        "aprobado",
        "rechazado",
        "pagado",
        "cerrado",
    ]
    reportadoEn: AwareDatetime


class PaymentRecord(_Record):
    id: UUID
    siniestroId: UUID
    estado: Literal["pendiente", "confirmado", "rechazado"]
    monto: str = Money
    moneda: str = Currency
    creadoEn: AwareDatetime


@dataclass(frozen=True)
class SourceTrace:
    service: str
    records: int
    fingerprint: str


@dataclass(frozen=True)
class ReportSnapshot:
    cut_off: datetime
    policies: tuple[PolicyRecord, ...]
    premiums: tuple[PremiumRecord, ...]
    claims: tuple[ClaimRecord, ...]
    payments: tuple[PaymentRecord, ...]
    sources: tuple[SourceTrace, ...]


@dataclass(frozen=True)
class ReportFilters:
    start: date
    end: date
    cut_off: datetime
    product: str | None = None
    state: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "desde": self.start.isoformat(),
            "hasta": self.end.isoformat(),
            "producto": self.product,
            "estado": self.state,
        }


def instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def fingerprint(content: object) -> str:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def parse_filters(
    args: Mapping[str, Sequence[str]],
    now: datetime,
    *,
    extra: frozenset[str] = frozenset(),
) -> ReportFilters:
    if not set(args) <= FILTERS | extra:
        raise InvalidReportFilterError("La consulta incluye filtros no permitidos.")
    if any(len(values) != 1 for values in args.values()):
        raise InvalidReportFilterError("Cada filtro admite un unico valor.")
    values = {name: items[0].strip() for name, items in args.items()}
    cut_off = _cut_off(values.get("corte"), now)
    start = _day(values.get("desde"), "desde")
    end = _day(values.get("hasta"), "hasta")
    if start > end:
        raise InvalidReportFilterError("La fecha inicial no puede ser posterior a la final.")
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        raise InvalidReportFilterError(f"El periodo no puede superar {MAX_PERIOD_DAYS} dias.")
    if end > cut_off.date():
        raise InvalidReportFilterError("El periodo no puede terminar despues del corte.")
    product = values.get("producto") or None
    if product is not None and product not in PRODUCTS:
        raise InvalidReportFilterError("El producto no esta permitido.")
    state = values.get("estado") or None
    if state is not None and state not in POLICY_STATES:
        raise InvalidReportFilterError("El estado no esta permitido.")
    return ReportFilters(start, end, cut_off, product, state)


def _cut_off(value: str | None, now: datetime) -> datetime:
    if value is None:
        return (now - DEFAULT_CUT_OFF_DELAY).astimezone(UTC).replace(microsecond=0)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidReportFilterError("El corte debe ser una fecha y hora ISO 8601.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidReportFilterError("El corte debe incluir zona horaria.")
    parsed = parsed.astimezone(UTC)
    if parsed > now:
        raise InvalidReportFilterError("El corte no puede estar en el futuro.")
    return parsed


def _day(value: str | None, name: str) -> date:
    if not value:
        raise InvalidReportFilterError(f"El filtro {name} es obligatorio.")
    try:
        if len(value) != 10:
            raise ValueError(value)
        return date.fromisoformat(value)
    except ValueError as error:
        raise InvalidReportFilterError(
            f"El filtro {name} debe tener formato YYYY-MM-DD."
        ) from error


def build_report(
    filters: ReportFilters, snapshot: ReportSnapshot, generated_at: datetime
) -> dict[str, Any]:
    if snapshot.cut_off != filters.cut_off:
        raise ReportSourceUnavailableError("consolidacion")
    period = _Period(filters.start, filters.end)
    known_policies = {policy.id: policy for policy in snapshot.policies}
    universe = {
        policy.id: policy
        for policy in snapshot.policies
        if filters.product in (None, policy.tipo) and filters.state in (None, policy.estado)
    }
    restricted = filters.product is not None or filters.state is not None
    payment_ids = {policy.pagoId for policy in universe.values()}
    premiums = [
        premium
        for premium in snapshot.premiums
        if period.contains(premium.confirmadaEn)
        and filters.product in (None, premium.producto)
        and (filters.state is None or premium.pagoId in payment_ids)
    ]
    eligible_claims = {
        claim.id: claim
        for claim in snapshot.claims
        if claim.polizaId in universe or (not restricted and claim.polizaId not in known_policies)
    }
    claims = [claim for claim in eligible_claims.values() if period.contains(claim.reportadoEn)]
    payments = [
        payment
        for payment in snapshot.payments
        if payment.siniestroId in eligible_claims and period.contains(payment.creadoEn)
    ]
    products = PRODUCTS if filters.product is None else (filters.product,)
    issued = [policy for policy in universe.values() if period.contains(policy.emitidaEn)]
    premium_by_payment = {premium.pagoId: premium for premium in snapshot.premiums}
    claim_ids = {claim.id for claim in snapshot.claims}
    claim_products = {
        claim_id: known_policies[claim.polizaId].tipo if claim.polizaId in known_policies else None
        for claim_id, claim in eligible_claims.items()
    }
    report: dict[str, Any] = {
        "corte": instant(filters.cut_off),
        "filtros": filters.as_json(),
        "indicadores": {
            "polizas": {
                "emitidasEnPeriodo": len(issued),
                "cartera": len(universe),
                "porEstado": _count(POLICY_STATES, (p.estado for p in universe.values())),
            },
            "primas": {
                "confirmadas": len(premiums),
                "totales": _totals((p.moneda, p.monto) for p in premiums),
            },
            "siniestros": {
                "reportados": len(claims),
                "porEstado": _count(CLAIM_STATES, (c.estado for c in claims)),
                "porTipo": _count(CLAIM_TYPES, (c.tipo or "sin_tipo" for c in claims)),
            },
            "pagos": {
                "registrados": len(payments),
                "porEstado": _count(PAYMENT_STATES, (p.estado for p in payments)),
                "totales": [
                    {"estado": state, "moneda": currency, "monto": amount}
                    for state in PAYMENT_STATES
                    for currency, amount in _total_pairs(
                        (p.moneda, p.monto) for p in payments if p.estado == state
                    )
                ],
            },
        },
        "porProducto": [
            _by_product(product, issued, universe, premiums, claims, payments, claim_products)
            for product in products
        ],
        "inconsistencias": {
            "siniestrosSinPoliza": sum(c.polizaId not in known_policies for c in claims),
            "polizasSinPrima": sum(p.pagoId not in premium_by_payment for p in universe.values()),
            "primasConProductoDistinto": sum(
                premium_by_payment[p.pagoId].producto != p.tipo
                for p in universe.values()
                if p.pagoId in premium_by_payment
            ),
            "pagosSinSiniestro": sum(p.siniestroId not in claim_ids for p in snapshot.payments),
        },
        "fuentes": [
            {"servicio": source.service, "registros": source.records, "huella": source.fingerprint}
            for source in snapshot.sources
        ],
    }
    report["huella"] = fingerprint(report)
    report["generadoEn"] = instant(generated_at)
    return report


def _by_product(
    product: str,
    issued: Sequence[PolicyRecord],
    universe: Mapping[UUID, PolicyRecord],
    premiums: Sequence[PremiumRecord],
    claims: Sequence[ClaimRecord],
    payments: Sequence[PaymentRecord],
    claim_products: Mapping[UUID, str | None],
) -> dict[str, object]:
    selected_premiums = [premium for premium in premiums if premium.producto == product]
    return {
        "producto": product,
        "polizasEmitidas": sum(policy.tipo == product for policy in issued),
        "cartera": sum(policy.tipo == product for policy in universe.values()),
        "primasConfirmadas": len(selected_premiums),
        "primas": _totals((p.moneda, p.monto) for p in selected_premiums),
        "siniestrosReportados": sum(claim_products.get(c.id) == product for c in claims),
        "pagosRegistrados": sum(claim_products.get(p.siniestroId) == product for p in payments),
    }


def report_csv(report: Mapping[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("ambito", "indicador", "dimension", "moneda", "valor"))
    filters = report["filtros"]
    writer.writerow(("reporte", "corte", "", "", report["corte"]))
    writer.writerow(("reporte", "huella", "", "", report["huella"]))
    for name in ("desde", "hasta", "producto", "estado"):
        writer.writerow(("filtro", name, "", "", filters[name] or "todos"))
    indicators = report["indicadores"]
    policies = indicators["polizas"]
    writer.writerow(("polizas", "emitidasEnPeriodo", "", "", policies["emitidasEnPeriodo"]))
    writer.writerow(("polizas", "cartera", "", "", policies["cartera"]))
    _rows(writer, "polizas", "porEstado", policies["porEstado"])
    premiums = indicators["primas"]
    writer.writerow(("primas", "confirmadas", "", "", premiums["confirmadas"]))
    for total in premiums["totales"]:
        writer.writerow(("primas", "total", "", total["moneda"], total["monto"]))
    claims = indicators["siniestros"]
    writer.writerow(("siniestros", "reportados", "", "", claims["reportados"]))
    _rows(writer, "siniestros", "porEstado", claims["porEstado"])
    _rows(writer, "siniestros", "porTipo", claims["porTipo"])
    payments = indicators["pagos"]
    writer.writerow(("pagos", "registrados", "", "", payments["registrados"]))
    _rows(writer, "pagos", "porEstado", payments["porEstado"])
    for total in payments["totales"]:
        writer.writerow(("pagos", "total", total["estado"], total["moneda"], total["monto"]))
    for item in report["porProducto"]:
        product = item["producto"]
        for name in (
            "polizasEmitidas",
            "cartera",
            "primasConfirmadas",
            "siniestrosReportados",
            "pagosRegistrados",
        ):
            writer.writerow(("producto", name, product, "", item[name]))
        for total in item["primas"]:
            writer.writerow(("producto", "primas", product, total["moneda"], total["monto"]))
    _rows(writer, "inconsistencias", "", report["inconsistencias"])
    for source in report["fuentes"]:
        writer.writerow(("fuente", source["servicio"], "registros", "", source["registros"]))
        writer.writerow(("fuente", source["servicio"], "huella", "", source["huella"]))
    return output.getvalue()


def _rows(writer: Any, scope: str, indicator: str, values: Mapping[str, object]) -> None:
    for dimension, value in values.items():
        if indicator:
            writer.writerow((scope, indicator, dimension, "", value))
        else:
            writer.writerow((scope, dimension, "", "", value))


@dataclass(frozen=True)
class _Period:
    start: date
    end: date

    def contains(self, value: datetime) -> bool:
        return self.start <= value.astimezone(UTC).date() <= self.end


def _count(keys: Sequence[str], values: Iterable[str]) -> dict[str, int]:
    counter = Counter(values)
    return {key: counter[key] for key in keys}


def _total_pairs(items: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    totals: dict[str, Decimal] = {}
    for currency, amount in items:
        totals[currency] = totals.get(currency, Decimal("0")) + Decimal(amount)
    return [(currency, str(totals[currency].quantize(CENT))) for currency in sorted(totals)]


def _totals(items: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"moneda": currency, "monto": amount} for currency, amount in _total_pairs(items)]
