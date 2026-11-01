import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from perfilamiento.domain import (
    AplicacionInformacion,
    Consentimiento,
    ConsultaInformacionCliente,
    Degradacion,
    ErrorPerfilamiento,
    EstadoAplicacionInformacion,
    EstadoConsultaInformacion,
    EvidenciaFuente,
    Fuente,
    OperacionInformacion,
)
from perfilamiento.domain import ConsultarInformacionComando as ConsultarInformacionComando
from perfilamiento.exceptions import AbrirExcepcionOpenFinance, construir_excepcion
from perfilamiento.fuentes import ConsultarFuentes
from perfilamiento.persistence import RepositorioPerfiles
from perfilamiento.ports import AutorizacionesSocio, Consentimientos, FuentePerfilamiento
from perfilamiento.resilience import EjecutorAcotado, PoliticaConsultas, Presupuesto

PRODUCTOS_AUTORIZADOS = frozenset(
    {
        "automotive",
        "travel",
        "life",
        "device-protection",
        "parametric",
        "parametric-weather",
        "payment-protection",
        "mortgage-life",
    }
)


@dataclass(frozen=True)
class ReanudacionInformacion:
    operacion: OperacionInformacion
    actor_ejecutor: UUID
    presupuesto: Presupuesto
    transaccion: Session


class ConsultarInformacionCliente:
    def __init__(
        self,
        repositorio: RepositorioPerfiles,
        consentimientos: Consentimientos,
        fuentes: tuple[FuentePerfilamiento, ...],
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
        politica: PoliticaConsultas | None = None,
        autorizaciones: AutorizacionesSocio | None = None,
    ) -> None:
        self._repositorio = repositorio
        self._consentimientos = consentimientos
        self._reloj = reloj
        self._politica = politica or PoliticaConsultas()
        self._consultas_fuentes = ConsultarFuentes(
            repositorio, fuentes, lambda: self._reloj(), self._politica
        )
        self._fuentes = {fuente.nombre: fuente for fuente in fuentes}
        self._ejecutor = EjecutorAcotado()
        self._autorizaciones = autorizaciones

    def cerrar(self) -> None:
        self._ejecutor.cerrar()

    def _autorizar(
        self,
        cliente_id: UUID,
        finalidad: str,
        correlation_id: str,
        presupuesto: Presupuesto,
        esperado: Consentimiento | None = None,
    ) -> Consentimiento:
        consentimiento = presupuesto.ejecutar(
            "identidad", lambda: self._consentimientos.obtener(cliente_id, correlation_id)
        )
        consentimiento.validar_consulta(cliente_id, finalidad, self._reloj())
        self._repositorio.validar_version_consentimiento(consentimiento)
        if esperado is not None and consentimiento != esperado:
            raise ErrorPerfilamiento("consentimiento_no_vigente", 403)
        return consentimiento

    def ejecutar(
        self,
        comando: ConsultarInformacionComando,
        correlation_id: str,
        *,
        _reanudacion: ReanudacionInformacion | None = None,
    ) -> ConsultaInformacionCliente:
        presupuesto = (
            _reanudacion.presupuesto
            if _reanudacion is not None
            else Presupuesto(self._politica, self._ejecutor)
        )
        transaccion = _reanudacion.transaccion if _reanudacion is not None else None
        estados: list[dict[str, object]] = []
        degradaciones: list[Degradacion] = []
        nombre = "identidad"
        consentimiento: Consentimiento | None = (
            _reanudacion.operacion.consentimiento if _reanudacion is not None else None
        )
        try:
            fingerprint = (
                _reanudacion.operacion.request_fingerprint
                if _reanudacion is not None
                else _fingerprint(comando)
            )
            if comando.producto_id not in PRODUCTOS_AUTORIZADOS:
                raise ErrorPerfilamiento("producto_no_autorizado", 403)
            if comando.finalidad != "cotizacion":
                raise ErrorPerfilamiento("finalidad_no_autorizada", 403)

            def autorizar() -> Consentimiento:
                if _reanudacion is not None:
                    self._autorizar_actor(
                        comando.actor_id, comando.socio_id, "socio", correlation_id, presupuesto
                    )
                    self._autorizar_actor(
                        _reanudacion.actor_ejecutor,
                        comando.socio_id,
                        "asesor",
                        correlation_id,
                        presupuesto,
                    )
                return self._autorizar(
                    comando.cliente_id,
                    comando.finalidad,
                    correlation_id,
                    presupuesto,
                    consentimiento,
                )

            consentimiento = autorizar()
            existente = self._repositorio.buscar_consulta_informacion(
                comando.socio_id, comando.operation_id
            )
            if existente is not None:
                consulta, fingerprint_existente = existente
                if fingerprint_existente != fingerprint:
                    raise ErrorPerfilamiento("conflicto_idempotencia", 409)
                self._validar_consentimiento_consulta(consulta, autorizar())
                return consulta
            if _reanudacion is None:
                bloqueada = self._repositorio.buscar_operacion_informacion(
                    comando.socio_id, comando.operation_id
                )
                if bloqueada is not None:
                    if bloqueada.request_fingerprint != fingerprint:
                        raise ErrorPerfilamiento("conflicto_idempotencia", 409)
                    self._autorizar(
                        comando.cliente_id,
                        comando.finalidad,
                        correlation_id,
                        presupuesto,
                        bloqueada.consentimiento,
                    )
                    estado_actual = self._estado(bloqueada)
                    raise ErrorPerfilamiento(
                        "operacion_rechazada"
                        if estado_actual.estado_decision == "rechazada"
                        else "consulta_bloqueada",
                        409 if estado_actual.estado_decision == "rechazada" else 503,
                        operacion=estado_actual,
                    )
            permitidos = set(comando.datos_solicitados) & set(consentimiento.datos)
            fuentes: list[Fuente] = []
            frescas: list[Fuente] = []
            if "indicador_riesgo" in permitidos:
                for nombre in consentimiento.fuentes:
                    estado: dict[str, object] = {
                        "fuente": nombre,
                        "estado": "fallida",
                        "intentos": 0,
                    }
                    estados.append(estado)
                    adaptador = self._fuentes.get(nombre)
                    if adaptador is None:
                        estado["motivo"] = "fuente_no_disponible"
                        raise ErrorPerfilamiento("fuente_no_disponible", 503)
                    try:
                        fuente = self._consultas_fuentes.consultar(
                            adaptador, consentimiento, autorizar, presupuesto, estado, degradaciones
                        )
                    except ErrorPerfilamiento as error:
                        if (
                            _reanudacion is None
                            and nombre == "open_finance"
                            and error.codigo == "fuente_no_integra"
                            and "motivo" in estado
                        ):
                            error.operacion = self._registrar_bloqueo(
                                comando, autorizar(), fingerprint, estado, correlation_id
                            )
                        raise
                    if fuente is None:
                        operacion = (
                            self._registrar_bloqueo(
                                comando, autorizar(), fingerprint, estado, correlation_id
                            )
                            if _reanudacion is None and nombre == "open_finance"
                            else None
                        )
                        raise ErrorPerfilamiento(str(estado["motivo"]), 503, operacion=operacion)
                    fuentes.append(fuente)
                    if estado["estado"] == "disponible":
                        frescas.append(fuente)

            autorizar()
            ahora = self._reloj()
            for fuente in fuentes:
                if not fuente.consultada_en <= ahora < fuente.vigente_hasta:
                    nombre = fuente.nombre
                    raise ErrorPerfilamiento("perfil_vencido", 503)
            datos: dict[str, str] = {}
            if fuentes:
                datos["indicadorRiesgo"] = str(
                    max((fuente.indicador_riesgo for fuente in fuentes), default=Decimal("0"))
                )
            consulta = ConsultaInformacionCliente(
                operation_id=comando.operation_id,
                socio_id=comando.socio_id,
                actor_id=comando.actor_id,
                cliente_id=comando.cliente_id,
                producto_id=comando.producto_id,
                finalidad="cotizacion",
                consentimiento_id=consentimiento.consentimiento_id,
                consentimiento_version=consentimiento.version,
                datos=datos,
                evidencia_fuentes=tuple(
                    EvidenciaFuente(
                        fuente=fuente.nombre,
                        version=fuente.version,
                        consultada_en=fuente.consultada_en,
                        simulada=fuente.simulada,
                    )
                    for fuente in fuentes
                ),
                consultada_en=ahora,
            )
            auditorias = tuple(
                self._auditoria(
                    comando,
                    str(estado["fuente"]),
                    "consultada" if estado["estado"] == "disponible" else "cache",
                    correlation_id,
                    presupuesto,
                    estados,
                    degradaciones,
                )
                for estado in estados
            )
            self._repositorio.guardar_consulta_informacion(
                consulta, fingerprint, tuple(frescas), auditorias, transaccion=transaccion
            )
            return consulta
        except ErrorPerfilamiento as error:
            if error.codigo == "consulta_bloqueada" and error.operacion is None:
                bloqueada = self._repositorio.buscar_operacion_informacion(
                    comando.socio_id, comando.operation_id
                )
                if bloqueada is not None:
                    error.operacion = self._estado(bloqueada)
            for estado in estados or [{"fuente": nombre, "estado": "fallida"}]:
                resultado = (
                    error.codigo
                    if estado["fuente"] == nombre
                    else "cache"
                    if estado["estado"] == "cache"
                    else "consultada"
                )
                auditoria = self._auditoria(
                    comando,
                    str(estado["fuente"]),
                    resultado,
                    correlation_id,
                    presupuesto,
                    estados,
                    degradaciones,
                )
                auditoria["resultadoConsulta"] = error.codigo
                self._repositorio.auditar(auditoria, transaccion=transaccion)
            raise

    def autorizar_asesor(self, actor_id: UUID, socio_id: str, correlation_id: str) -> None:
        self._autorizar_actor(
            actor_id,
            socio_id,
            "asesor",
            correlation_id,
            Presupuesto(self._politica, self._ejecutor),
        )

    def _autorizar_actor(
        self,
        actor_id: UUID,
        socio_id: str,
        rol: Literal["asesor", "socio"],
        correlation_id: str,
        presupuesto: Presupuesto,
    ) -> None:
        autorizaciones = self._autorizaciones
        if autorizaciones is None:
            raise ErrorPerfilamiento("identidad_no_disponible", 503)
        presupuesto.ejecutar(
            "identidad", lambda: autorizaciones.validar(actor_id, socio_id, rol, correlation_id)
        )

    def estado(
        self, socio_id: str, operation_id: UUID, actor_id: UUID, correlation_id: str
    ) -> EstadoConsultaInformacion:
        presupuesto = Presupuesto(self._politica, self._ejecutor)
        self._autorizar_actor(actor_id, socio_id, "socio", correlation_id, presupuesto)
        operacion = self._repositorio.buscar_operacion_informacion(socio_id, operation_id)
        if operacion is None:
            raise ErrorPerfilamiento("consulta_no_encontrada", 404)
        self._autorizar(
            operacion.comando.cliente_id,
            operacion.comando.finalidad,
            correlation_id,
            presupuesto,
            operacion.consentimiento,
        )
        return self._estado(operacion)

    def _estado(self, operacion: OperacionInformacion) -> EstadoConsultaInformacion:
        caso = self._repositorio.obtener_excepcion_open_finance(
            operacion.excepcion_id, operacion.comando.socio_id
        )
        return EstadoConsultaInformacion.model_validate(
            {
                "operationId": operacion.comando.operation_id,
                "socioId": operacion.comando.socio_id,
                "clienteId": operacion.comando.cliente_id,
                "productoId": operacion.comando.producto_id,
                "excepcionId": operacion.excepcion_id,
                "estadoDecision": caso["estado"],
                "aplicacion": operacion.aplicacion,
            }
        )

    def _registrar_bloqueo(
        self,
        comando: ConsultarInformacionComando,
        consentimiento: Consentimiento,
        fingerprint: str,
        estado: dict[str, object],
        correlation_id: str,
    ) -> EstadoConsultaInformacion:
        ahora = self._reloj()
        consentimiento.validar(comando.cliente_id, ahora, ("open_finance",))
        excepcion, evento = construir_excepcion(
            AbrirExcepcionOpenFinance(
                operation_id=comando.operation_id,
                socio_id=comando.socio_id,
                cliente_id=comando.cliente_id,
                consentimiento_id=consentimiento.consentimiento_id,
                consentimiento_version=consentimiento.version,
                codigo="fuente_vencida"
                if estado["motivo"] == "perfil_vencido"
                else "fuente_no_disponible",
                motivo_bloqueo=(
                    "HU44: open_finance sin respuesta ni cache integra, vigente y autorizada."
                ),
                bloquea_procesamiento=True,
            ),
            consentimiento,
            correlation_id,
            ahora,
        )
        operacion = OperacionInformacion(
            excepcion_id=excepcion.excepcion_id,
            comando=replace(
                comando,
                datos_solicitados=tuple(
                    dato for dato in comando.datos_solicitados if dato in consentimiento.datos
                ),
            ),
            request_fingerprint=fingerprint,
            consentimiento=consentimiento,
            aplicacion=AplicacionInformacion(
                estado="esperando_decision", intentos=0, actualizada_en=ahora
            ),
        )
        self._repositorio.registrar_excepcion_open_finance(
            excepcion.model_dump(
                mode="json", by_alias=True, exclude={"idempotente", "proceso", "aplicacion"}
            ),
            evento,
            operacion,
        )
        persistida = self._repositorio.buscar_operacion_informacion(
            comando.socio_id, comando.operation_id
        )
        assert persistida is not None
        return self._estado(persistida)

    def aplicar_decision(
        self,
        excepcion_id: UUID,
        socio_id: str,
        actor_id: UUID,
        correlation_id: str,
        impedimento: Literal["producto_no_autorizado", "catalogo_no_disponible"] | None = None,
    ) -> None:
        caso = self._repositorio.obtener_excepcion_open_finance(excepcion_id, socio_id)
        operacion = self._repositorio.buscar_operacion_informacion(
            socio_id, UUID(str(caso["operationId"]))
        )
        if operacion is None:
            raise ErrorPerfilamiento("excepcion_sin_operacion", 409)
        presupuesto = Presupuesto(self._politica, self._ejecutor)
        self._autorizar_actor(actor_id, socio_id, "asesor", correlation_id, presupuesto)
        # La guarda SQL abarca el reintento acotado y su resultado/acuse atomicos.
        with self._repositorio.bloquear_operacion_informacion(excepcion_id, socio_id) as (
            transaccion,
            operacion,
            caso,
        ):
            if caso["estado"] == "pendiente":
                raise ErrorPerfilamiento("decision_pendiente", 409)
            if operacion.aplicacion.acuse_id is not None:
                return
            if operacion.aplicacion.estado != "pendiente":
                raise ErrorPerfilamiento("operacion_no_integra", 503)
            estado: EstadoAplicacionInformacion
            if caso["estado"] == "rechazada":
                estado, codigo, intentos = "detenida", "operacion_rechazada", 0
            else:
                intentos = 1
                try:
                    if impedimento is not None:
                        self._repositorio.auditar(
                            self._auditoria(
                                operacion.comando,
                                "catalogo",
                                impedimento,
                                correlation_id,
                                presupuesto,
                                [],
                                [],
                            ),
                            transaccion=transaccion,
                        )
                        raise ErrorPerfilamiento(
                            impedimento, 403 if impedimento == "producto_no_autorizado" else 503
                        )
                    self.ejecutar(
                        operacion.comando,
                        correlation_id,
                        _reanudacion=ReanudacionInformacion(
                            operacion, actor_id, presupuesto, transaccion
                        ),
                    )
                except ErrorPerfilamiento as error:
                    estado, codigo = "bloqueada", error.codigo
                else:
                    estado, codigo = "completada", "consulta_disponible"
            ahora = self._reloj()
            acuse_id = uuid4()
            aplicacion = AplicacionInformacion(
                estado=estado,
                codigo=codigo,
                intentos=intentos,
                actualizada_en=ahora,
                acuse_id=acuse_id,
            )
            self._repositorio.guardar_operacion_informacion(
                transaccion,
                operacion.model_copy(update={"aplicacion": aplicacion}),
                {
                    "eventoId": str(acuse_id),
                    "excepcionId": str(excepcion_id),
                    "operationId": str(operacion.comando.operation_id),
                    "socioId": socio_id,
                    "actorId": str(actor_id),
                    "actorSolicitanteId": str(operacion.comando.actor_id),
                    "fecha": ahora.isoformat(),
                    "tipo": "AplicacionExcepcionOpenFinanceRegistrada",
                    "estadoAnterior": "pendiente",
                    "estadoActual": estado,
                    "codigo": codigo,
                    "correlationId": correlation_id,
                },
            )

    def recuperar(
        self, socio_id: str, operation_id: UUID, correlation_id: str
    ) -> ConsultaInformacionCliente:
        existente = self._repositorio.buscar_consulta_informacion(socio_id, operation_id)
        if existente is None:
            raise ErrorPerfilamiento("consulta_no_encontrada", 404)
        consulta, _ = existente
        presupuesto = Presupuesto(self._politica, self._ejecutor)
        consentimiento = self._autorizar(
            consulta.cliente_id, consulta.finalidad, correlation_id, presupuesto
        )
        self._validar_consentimiento_consulta(consulta, consentimiento)
        return consulta

    @staticmethod
    def _validar_consentimiento_consulta(
        consulta: ConsultaInformacionCliente, consentimiento: Consentimiento
    ) -> None:
        if (
            consulta.consentimiento_id != consentimiento.consentimiento_id
            or consulta.consentimiento_version != consentimiento.version
        ):
            raise ErrorPerfilamiento("consentimiento_no_vigente", 403)

    def _auditoria(
        self,
        comando: ConsultarInformacionComando,
        fuente: str,
        resultado: str,
        correlation_id: str,
        presupuesto: Presupuesto,
        estados: list[dict[str, object]],
        degradaciones: list[Degradacion],
    ) -> dict[str, object]:
        return {
            "operationId": str(comando.operation_id),
            "socioId": comando.socio_id,
            "actorId": str(comando.actor_id),
            "clienteId": str(comando.cliente_id),
            "productoId": comando.producto_id,
            "finalidad": comando.finalidad,
            "fuente": fuente,
            "fecha": self._reloj().isoformat(),
            "resultado": resultado,
            "correlationId": correlation_id,
            "estadoFuentes": estados,
            "degradaciones": [
                item.model_dump(mode="json", by_alias=True) for item in degradaciones
            ],
            "dependencias": presupuesto.dependencias,
            "duracionMs": round((monotonic() - presupuesto.inicio) * 1000, 2),
            "presupuestoMs": presupuesto.politica.journey_segundos * 1000,
        }


def _fingerprint(comando: ConsultarInformacionComando) -> str:
    serialized = json.dumps(asdict(comando), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()
