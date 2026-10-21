class ProfilingError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


PROFILING_ERRORS = {
    "solicitud_invalida": 400,
    "consentimiento_ausente": 403,
    "consentimiento_no_vigente": 403,
    "perfil_vencido": 422,
    "perfil_no_encontrado": 404,
    "conflicto_version": 409,
    "perfilamiento_no_disponible": 503,
}
