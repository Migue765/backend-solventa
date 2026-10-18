from cotizacion.application.ports import ProveedorPerfil
from cotizacion.domain.errors import ErrorConsultaPerfil
from cotizacion.domain.solicitud import SolicitudCotizacion
from cotizacion.domain.tarifa import PerfilRiesgo


def validar_perfil(solicitud: SolicitudCotizacion, proveedor: ProveedorPerfil) -> PerfilRiesgo:
    instantanea = solicitud.cotizacion.instantanea
    if instantanea is None or not instantanea.contenido["perfil"].get("evidencia"):
        raise ErrorConsultaPerfil("perfil_no_encontrado", 404)
    perfil = proveedor.obtener(instantanea.perfil_id, solicitud.cotizacion.cliente_id)
    if perfil is None or perfil.evidencia is None:
        raise ErrorConsultaPerfil("perfil_no_encontrado", 404)
    if (
        perfil.evidencia["referenciaId"] != str(solicitud.cotizacion.cotizacion_id)
        or perfil.evidencia != instantanea.contenido["perfil"]["evidencia"]
    ):
        raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)
    return perfil
