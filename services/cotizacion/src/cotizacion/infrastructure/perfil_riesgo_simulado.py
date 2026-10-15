from decimal import Decimal

from cotizacion.domain.perfil_riesgo import NivelRiesgo, PerfilRiesgo

_PERFILES = (
    PerfilRiesgo(NivelRiesgo.A, Decimal("1.00")),
    PerfilRiesgo(NivelRiesgo.B, Decimal("1.10")),
    PerfilRiesgo(NivelRiesgo.C, Decimal("1.25")),
)


class PerfilRiesgoSimulado:
    """Genera escenarios repetibles sin representar una consulta a un buró real."""

    def consultar(self, tipo_documento: str, identificacion: str) -> PerfilRiesgo:
        semilla = f"{tipo_documento}{identificacion}".encode()
        return _PERFILES[sum(semilla) % len(_PERFILES)]
