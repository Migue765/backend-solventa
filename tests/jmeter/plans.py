"""Generate native JMeter HTTP samplers and assertions (no third-party plugins)."""

import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

from PIL import Image

REQUESTS = "/v1/solicitudes-cotizacion"
BASE = REQUESTS + "/${requestId}"
CONSENT = "/v1/consentimientos/perfilamiento"
DASHBOARD = "/v1/clientes/me/dashboard"
CLAIMS = "/v1/siniestros"
CLAIM = CLAIMS + "/${claimId}"
UPLOAD = CLAIM + "/cargas/${uploadId}"
CLAIM_STATUS = "/v1/clientes/me/siniestros/${claimId}"
# Synthetic PNG without EXIF/location, written next to each plan (JMeter resolves it there).
EVIDENCE = "evidencia-sintetica.png"
PRODUCTS = (
    "automotive",
    "life",
    "device-protection",
    "payment-protection",
    "mortgage-life",
    "travel",
    "parametric",
    "parametric-weather",
)


def _consent_payload(authorized: bool, version: int) -> dict[str, object]:
    body: dict[str, object] = {"autorizado": authorized, "version": version}
    if authorized:
        body["solicitud"] = {
            "finalidad": "cotizacion",
            "alcances": ["open_finance", "open_data"],
            "fuentes": ["open_finance", "open_data"],
            "datos": ["indicador_riesgo"],
            "versionTerminos": "1.0",
            "vigenteHasta": "${consentValidUntil}",
        }
    return body


def product_data() -> dict[str, dict[str, str]]:
    start = date.today() + timedelta(days=30)
    end = start + timedelta(days=7)
    return {
        "automotive": {"vehiclePlate": "ABC123"},
        "life": {"birthDate": "1990-01-01"},
        "device-protection": {
            "deviceType": "phone",
            "brandModel": "Test",
            "purchaseValue": "3000000",
        },
        "payment-protection": {"creditType": "libre", "monthlyPayment": "500000"},
        "mortgage-life": {"birthDate": "1990-01-01", "mortgageBalance": "100000000"},
        "travel": {
            "destinationCountryCode": "ES",
            "departureDate": start.isoformat(),
            "returnDate": end.isoformat(),
        },
        "parametric": {
            "airline": "Test",
            "flightNumber": "AV123",
            "flightDate": start.isoformat(),
        },
        "parametric-weather": {
            "location": "Medellin",
            "weatherEvent": "heavy-rain",
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "insuredAmount": "10000000",
        },
    }


def evidence_file(directory: Path) -> tuple[int, str]:
    path = directory / EVIDENCE
    Image.new("RGB", (32, 32), (24, 96, 160)).save(path, format="PNG")
    content = path.read_bytes()
    return len(content), hashlib.sha256(content).hexdigest()


def prop(parent: ET.Element, name: str, value: str | int | bool) -> ET.Element:
    kind = "boolProp" if isinstance(value, bool) else "stringProp"
    node = ET.SubElement(parent, kind, name=name)
    node.text = str(value).lower() if isinstance(value, bool) else str(value)
    return node


def component(parent: ET.Element, kind: str, name: str) -> tuple[ET.Element, ET.Element]:
    gui = {
        "TestPlan": "TestPlanGui",
        "ThreadGroup": "ThreadGroupGui",
        "HTTPSamplerProxy": "HttpTestSampleGui",
        "HeaderManager": "HeaderPanel",
        "TransactionController": "TransactionControllerGui",
        "JSR223Sampler": "TestBeanGUI",
        "JSR223Assertion": "TestBeanGUI",
    }[kind]
    node = ET.SubElement(parent, kind, guiclass=gui, testclass=kind, testname=name, enabled="true")
    return node, ET.SubElement(parent, "hashTree")


class Plan:
    def __init__(self, threads: int = 1, loops: int = 1) -> None:
        self.root = ET.Element("jmeterTestPlan", version="1.2", properties="5.0", jmeter="5.6.3")
        tree = ET.SubElement(self.root, "hashTree")
        _, tree = component(tree, "TestPlan", "Solventa real HTTP")
        group, self.tree = component(tree, "ThreadGroup", "Usuarios")
        prop(group, "ThreadGroup.num_threads", threads)
        prop(group, "ThreadGroup.ramp_time", max(1, threads))
        prop(group, "ThreadGroup.on_sample_error", "stopthread")
        loop = ET.SubElement(
            group, "elementProp", name="ThreadGroup.main_controller", elementType="LoopController"
        )
        prop(loop, "LoopController.loops", loops)
        prop(loop, "LoopController.continue_forever", False)
        self.script(
            "initialize",
            "vars.put('email', props.get('runId') + '-' + ctx.getThreadNum() + '-' + "
            "vars.getIteration() + '@example.com'); "
            "vars.put('otherEmail', 'other-' + vars.get('email')); "
            "vars.put('document', UUID.randomUUID().toString().replace('-', '').take(16)"
            ".toUpperCase(java.util.Locale.ROOT)); "
            "vars.put('key', UUID.randomUUID().toString()); "
            "vars.put('consentValidUntil', "
            "java.time.Instant.now().plusSeconds(2505600).toString())",
        )

    def script(self, label: str, script: str) -> None:
        node, _ = component(self.tree, "JSR223Sampler", label)
        prop(node, "scriptLanguage", "groovy")
        prop(node, "cacheKey", "true")
        prop(node, "script", script)

    def request(
        self,
        label: str,
        method: str,
        path: str,
        status: int = 200,
        body: dict[str, object] | None = None,
        check: str = "",
        token: str | None = "token",
        key: str | None = None,
        service: str = "web",
        file: str | None = None,
        extra_headers: dict[str, str] | None = None,
        binary: bool = False,
    ) -> None:
        sampler, children = component(self.tree, "HTTPSamplerProxy", label)
        for name, value in {
            "domain": "127.0.0.1",
            "port": "${__P(" + service + "Port)}",
            "protocol": "http",
            "path": path,
            "method": method,
            "implementation": "HttpClient4",
            "connect_timeout": "2000",
            "response_timeout": "10000",
            "contentEncoding": "UTF-8",
        }.items():
            prop(sampler, "HTTPSampler." + name, value)
        prop(sampler, "HTTPSampler.follow_redirects", False)
        prop(sampler, "HTTPSampler.use_keepalive", True)
        prop(sampler, "HTTPSampler.postBodyRaw", file is None)
        if file is not None:
            # A single file without parameter name is sent as the raw request body.
            files = ET.SubElement(
                sampler, "elementProp", name="HTTPsampler.Files", elementType="HTTPFileArgs"
            )
            collection = ET.SubElement(files, "collectionProp", name="HTTPFileArgs.files")
            upload = ET.SubElement(collection, "elementProp", name=file, elementType="HTTPFileArg")
            prop(upload, "File.path", file)
            prop(upload, "File.paramname", "")
            prop(upload, "File.mimetype", "application/octet-stream")
        arguments = ET.SubElement(
            sampler, "elementProp", name="HTTPsampler.Arguments", elementType="Arguments"
        )
        collection = ET.SubElement(arguments, "collectionProp", name="Arguments.arguments")
        if body is not None:
            argument = ET.SubElement(collection, "elementProp", name="", elementType="HTTPArgument")
            prop(argument, "HTTPArgument.always_encode", False)
            prop(argument, "Argument.value", json.dumps(body, separators=(",", ":")))
            prop(argument, "Argument.metadata", "=")
        headers, _ = component(children, "HeaderManager", "headers")
        collection = ET.SubElement(headers, "collectionProp", name="HeaderManager.headers")
        values = {"Content-Type": "application/json", "Accept": "application/json"}
        if token is not None:
            values["Authorization"] = "Bearer ${" + token + "}"
        if key is not None:
            values["Idempotency-Key"] = "${" + key + "}"
        values.update(extra_headers or {})
        for name, value in values.items():
            header = ET.SubElement(collection, "elementProp", name=name, elementType="Header")
            prop(header, "Header.name", name)
            prop(header, "Header.value", value)
        assertion, _ = component(children, "JSR223Assertion", "contract")
        prop(assertion, "scriptLanguage", "groovy")
        prop(assertion, "cacheKey", "true")
        # Groovy assertion messages can include response bodies/tokens. Never save them.
        script = (
            "try {\n"
            f"assert prev.getResponseCode() == '{status}'\n"
            + (
                "def data = [:]\n"
                if binary
                else "def data = prev.getResponseData().length == 0 ? [:] : "
                "new groovy.json.JsonSlurper().parseText(prev.getResponseDataAsString())\n"
            )
            + check
            + "\nprev.setSuccessful(true)\n"
            "} catch (Exception | AssertionError failure) {\n"
            "AssertionResult.setFailure(true)\n"
            "AssertionResult.setFailureMessage('Contract failed: ' + prev.getSampleLabel())\n"
            "}\n"
        )
        prop(assertion, "script", script)

    def credentials(self, other: bool = False) -> dict[str, object]:
        return {"correo": "${otherEmail}" if other else "${email}", "contrasena": "Solventa123!"}

    def register(self, other: bool = False, mobile: bool = False) -> None:
        self.request(
            "auth.register.other"
            if other
            else "auth.register.mobile"
            if mobile
            else "auth.register",
            "POST",
            "/movil/v1/usuarios" if mobile else "/v1/usuarios",
            201,
            {**self.credentials(other), "nombre": "Cliente JMeter sintetico"},
            check="assert data.usuario.id; assert data.usuario.rol == 'cliente'",
            token=None,
            service="mobile" if mobile else "web",
        )

    def provision_customer(self) -> None:
        self.request(
            "fixture.customer",
            "POST",
            "/v1/usuarios",
            201,
            {
                **self.credentials(),
                "nombre": "Cliente JMeter sintetico",
                "tipoDocumento": "passport",
                "numeroDocumento": "${document}",
                "telefono": "+573000000000",
                "fechaNacimiento": "1990-01-01",
            },
            check="assert data.usuario.id; assert data.usuario.rol == 'cliente'; "
            "assert data.usuario.numeroDocumento == vars.get('document'); "
            "assert data.usuario.telefono == '+573000000000'; "
            "assert data.usuario.fechaNacimiento == '1990-01-01'",
            token=None,
            service="identity",
        )

    def login(self, other: bool = False, mobile: bool = False) -> None:
        variable = "otherToken" if other else "mobileToken" if mobile else "token"
        self.request(
            "auth.login." + variable,
            "POST",
            "/movil/v1/sesiones" if mobile else "/v1/sesiones",
            body=self.credentials(other),
            token=None,
            service="mobile" if mobile else "web",
            check=f"assert data.tokenType == 'Bearer'; assert data.refreshToken; "
            f"vars.put('{variable}',data.accessToken); "
            f"vars.put('{variable}Refresh',data.refreshToken)",
        )

    def consent(self, authorized: bool, version: int) -> None:
        self.request(
            "consent." + str(version + 1),
            "PUT",
            CONSENT,
            body=_consent_payload(authorized, version),
            check=f"assert data.version == {version + 1}",
        )

    def transaction(self, label: str) -> ET.Element:
        old = self.tree
        controller, self.tree = component(self.tree, "TransactionController", label)
        prop(controller, "TransactionController.parent", True)
        prop(controller, "TransactionController.includeTimers", True)
        return old

    def quote(self, product: str, extended: bool = False, outage: bool = False) -> None:
        body: dict[str, object] = {"productoId": product, "datosProducto": product_data()[product]}
        self.script("new.operation", "vars.put('key', UUID.randomUUID().toString())")
        tree = self.transaction("journey.quote." + product)
        self.request(
            "quote.create." + product,
            "POST",
            REQUESTS,
            201,
            body,
            "assert data.id; vars.put('requestId',data.id)",
            key="key",
        )
        self.request(
            "quote.offers." + product,
            "GET",
            BASE + "/ofertas",
            check="assert data.ofertas.size() == 3; "
            "vars.put('offerId',data.ofertas[0].id); "
            "vars.put('offerVersion',data.ofertas[0].version.toString())",
        )
        self.tree = tree
        if extended:
            self.request(
                "quote.retry",
                "POST",
                REQUESTS,
                201,
                body,
                "assert data.id == vars.get('requestId')",
                key="key",
            )
            self.request("quote.read", "GET", BASE, check="assert data.id == vars.get('requestId')")
            self.request(
                "profile.read",
                "GET",
                BASE + "/perfilamiento",
                check="assert data.consentimientoVersion == 1; assert !data.fallbackUsed; "
                "assert data.fuentes.size() == 2; assert data.fuentes.every { it.simulada }",
            )
            self.request(
                "quote.conflict",
                "POST",
                REQUESTS,
                409,
                {"productoId": "automotive", "datosProducto": {"vehiclePlate": "XYZ987"}},
                "assert data.code == 'conflicto_idempotencia'",
                key="key",
            )
            self.request("ownership.quote", "GET", BASE, 404, token="otherToken")
            self.request("ownership.offers", "GET", BASE + "/ofertas", 404, token="otherToken")
        tree = self.transaction("outage.pending" if outage else "journey.emission." + product)
        self.request(
            "offer.select." + product,
            "PUT",
            BASE + "/seleccion",
            body={"ofertaId": "${offerId}", "version": "${offerVersion}"},
            check="assert data.prima; vars.put('premium',data.prima.monto.toString())",
            key="key",
        )
        self.request(
            "subscription.create." + product,
            "POST",
            BASE + "/suscripcion",
            201,
            check="assert data.estado == 'aprobada'; vars.put('subscriptionId',data.id)",
            key="key",
        )
        self.request(
            "payment.create." + product,
            "POST",
            BASE + "/pago-simulado",
            status=503 if outage else 200,
            body={},
            check="assert data.code == 'servicio_no_disponible'"
            if outage
            else "assert data.simulada; assert data.estado == 'confirmado'; "
            "assert data.importe.monto.toString() == vars.get('premium'); "
            "assert data.suscripcionId == vars.get('subscriptionId'); "
            "assert data.saga.estado == 'pago_confirmado'; vars.put('paymentId',data.id)",
            key="key",
        )
        if not outage:
            self.request(
                "emission.confirm." + product,
                "GET",
                BASE + "/suscripcion",
                check="assert data.saga.estado == 'emitida'",
            )
        self.tree = tree
        if extended:
            for method in ("GET", "POST"):
                self.request(
                    "payment.recovery." + method,
                    method,
                    BASE + "/pago-simulado",
                    check="assert data.id == vars.get('paymentId')",
                    key="key",
                )
                self.request(
                    "ownership.payment." + method,
                    method,
                    BASE + "/pago-simulado",
                    404,
                    token="otherToken",
                    key="key",
                )
            self.request(
                "subscription.read",
                "GET",
                BASE + "/suscripcion",
                check="assert data.saga.estado == 'emitida'",
            )
            self.request(
                "selection.read",
                "GET",
                BASE + "/seleccion",
                check="assert data.ofertaId == vars.get('offerId')",
            )

    def claim(self, size: int, digest: str, extended: bool = False) -> None:
        """HU24/HU25 Web: report on an emitted policy, store evidence and query its status."""
        self.script(
            "new.claim",
            "vars.put('claimKey', UUID.randomUUID().toString()); "
            "vars.put('uploadKey', UUID.randomUUID().toString()); "
            "vars.put('claimDate', java.time.Instant.now().minusSeconds(1).toString())",
        )
        self.request(
            "claim.policies",
            "GET",
            CLAIMS + "/polizas",
            check="assert data.polizas.size() > 0; "
            "assert data.polizas.every { it.estado == 'vigente' }; "
            "vars.put('policyId', data.polizas[0].id)",
        )
        report: dict[str, object] = {
            "polizaId": "${policyId}",
            "fechaEvento": "${claimDate}",
            "tipoEvento": "Choque simple",
            "descripcion": "Descripcion sintetica del evento para JMeter.",
            "lugar": "Ubicacion sintetica",
        }
        self.request(
            "claim.report",
            "POST",
            CLAIMS,
            201,
            report,
            "assert data.id; assert data.polizaId == vars.get('policyId'); "
            "vars.put('claimId', data.id)",
            key="claimKey",
        )
        if extended:
            self.request(
                "claim.report.retry",
                "POST",
                CLAIMS,
                body=report,
                check="assert data.id == vars.get('claimId')",
                key="claimKey",
            )
            self.request(
                "claim.report.conflict",
                "POST",
                CLAIMS,
                409,
                {**report, "descripcion": "Otra descripcion con la misma clave."},
                "assert data.code == 'idempotencia_conflictiva'",
                key="claimKey",
            )
        tree = self.transaction("journey.claim.status")
        self.request(
            "claim.status",
            "GET",
            CLAIM_STATUS,
            check="assert data.id == vars.get('claimId'); assert data.estado == 'reportado'; "
            "assert data.polizaId == vars.get('policyId'); assert data.historial.size() >= 1; "
            "assert data.historial[-1].estado == data.estado",
        )
        self.tree = tree
        if extended:
            self.request("claim.status.missing.token", "GET", CLAIM_STATUS, 401, token=None)
            self.request(
                "claim.status.ownership",
                "GET",
                CLAIM_STATUS,
                404,
                check="assert data.code == 'siniestro_no_encontrado'",
                token="otherToken",
            )
            self.request(
                "claim.status.unknown",
                "GET",
                "/v1/clientes/me/siniestros/${__UUID()}",
                404,
                check="assert data.code == 'siniestro_no_encontrado'",
            )
            self.request(
                "claim.status.invalid", "GET", "/v1/clientes/me/siniestros/no-es-uuid", 400
            )
        self.request(
            "evidence.configuration",
            "GET",
            CLAIMS + "/configuracion-evidencias",
            check="assert data.formatos.any { it.mimeType == 'image/png' }",
        )
        if extended:
            self.script(
                "evidence.format.key", "vars.put('formatKey', UUID.randomUUID().toString())"
            )
            self.request(
                "evidence.format.rejected",
                "POST",
                CLAIM + "/cargas",
                415,
                {"mimeType": "text/plain", "tamanoBytes": size, "sha256": digest},
                "assert data.code == 'formato_no_permitido'",
                key="formatKey",
            )
        upload: dict[str, object] = {"mimeType": "image/png", "tamanoBytes": size, "sha256": digest}
        self.request(
            "evidence.start",
            "POST",
            CLAIM + "/cargas",
            201,
            upload,
            "assert data.estado == 'pendiente'; assert data.offset == 0; "
            "vars.put('uploadId', data.id)",
            key="uploadKey",
        )
        if extended:
            self.request(
                "evidence.confirm.incomplete",
                "POST",
                UPLOAD + "/confirmacion",
                409,
                check="assert data.code == 'carga_incompleta'",
            )
        self.request(
            "evidence.content",
            "PUT",
            UPLOAD + "/contenido",
            file=EVIDENCE,
            extra_headers={"Content-Type": "application/octet-stream", "Upload-Offset": "0"},
            check=f"assert data.offset == {size}",
        )
        self.request(
            "evidence.confirm",
            "POST",
            UPLOAD + "/confirmacion",
            check="assert data.estado == 'confirmada'; "
            "assert data.evidenciaId == vars.get('uploadId')",
        )
        if extended:
            self.request(
                "evidence.confirm.retry",
                "POST",
                UPLOAD + "/confirmacion",
                check="assert data.estado == 'confirmada'",
            )
            self.request(
                "evidence.start.retry",
                "POST",
                CLAIM + "/cargas",
                body=upload,
                check="assert data.id == vars.get('uploadId'); assert data.estado == 'confirmada'",
                key="uploadKey",
            )
            self.request(
                "evidence.cancel.confirmed",
                "DELETE",
                UPLOAD,
                409,
                check="assert data.code == 'carga_confirmada'",
            )
        self.request(
            "evidence.list",
            "GET",
            CLAIM + "/evidencias",
            check=f"assert data.evidencias.size() == 1; "
            f"assert data.evidencias[0].sha256 == '{digest}'; "
            "assert data.evidencias[0].autorId; assert data.evidencias[0].creadaEn",
        )
        self.request(
            "evidence.download",
            "GET",
            CLAIM + "/evidencias/${uploadId}/contenido",
            check=f"assert prev.getResponseData().length == {size}",
            binary=True,
        )
        if extended:
            for suffix in ("evidencias", "evidencias/${uploadId}/contenido"):
                self.request(
                    "evidence.ownership." + suffix.split("/")[-1],
                    "GET",
                    CLAIM + "/" + suffix,
                    404,
                    token="otherToken",
                )

    def save(self, path: Path) -> None:
        ET.indent(self.root)
        ET.ElementTree(self.root).write(path, encoding="utf-8", xml_declaration=True)


def main_plan(path: Path, threads: int = 1, loops: int = 1, smoke: bool = True) -> None:
    plan = Plan(threads, loops)
    plan.provision_customer()
    plan.login()
    if smoke:
        plan.register(other=True)
        plan.login(other=True)
        plan.request("auth.missing", "GET", DASHBOARD, 401, token=None)
        plan.script("forged.token", "vars.put('forged','not-a-jwt')")
        plan.request("auth.forged", "GET", DASHBOARD, 401, token="forged")
        plan.request(
            "auth.duplicate",
            "POST",
            "/v1/usuarios",
            409,
            {**plan.credentials(), "nombre": "Duplicado"},
            token=None,
        )
        plan.request(
            "auth.wrong.password",
            "POST",
            "/v1/sesiones",
            401,
            {"correo": "${email}", "contrasena": "Wrong123!"},
            token=None,
        )
        plan.request(
            "quote.no.key",
            "POST",
            REQUESTS,
            400,
            {"productoId": "automotive", "datosProducto": {"vehiclePlate": "ABC123"}},
        )
        plan.request("consent.missing", "GET", CONSENT, 404)
        plan.request(
            "quote.no.consent",
            "POST",
            REQUESTS,
            403,
            {"productoId": "automotive", "datosProducto": {"vehiclePlate": "ABC123"}},
            "assert data.code == 'consentimiento_ausente'",
            key="key",
        )
    plan.consent(True, 0)
    if smoke:
        plan.request(
            "consent.stale.version",
            "PUT",
            CONSENT,
            409,
            _consent_payload(True, 0),
        )
        plan.request("catalog.missing", "GET", "/v1/productos-seguro/missing", 404)
        plan.request(
            "quote.invalid.product.data",
            "POST",
            REQUESTS,
            400,
            {"productoId": "automotive", "datosProducto": {"vehiclePlate": ""}},
            key="key",
        )
    plan.request(
        "catalog.products", "GET", "/v1/productos-seguro", check="assert data.productos.size() >= 8"
    )
    plan.request(
        "catalog.destinations", "GET", "/v1/destinos-viaje", check="assert data.destinos.size() > 0"
    )
    for product in PRODUCTS:
        plan.request("catalog." + product, "GET", "/v1/productos-seguro/" + product)
        plan.quote(product, extended=smoke and product == "automotive")
    plan.request(
        "journey.dashboard",
        "GET",
        DASHBOARD,
        check="assert data.polizas.size() == 8; assert data.pagos.size() == 8; "
        "assert data.suscripciones.size() == 8; assert data.cotizaciones.size() == 8; "
        "assert data.polizas.every { it.simulada }; "
        "assert data.notificaciones.disponibilidad == 'no_implementado'",
    )
    plan.claim(*evidence_file(path.parent), extended=smoke)
    if smoke:
        plan.request(
            "ownership.dashboard",
            "GET",
            DASHBOARD,
            token="otherToken",
            check="assert data.polizas == []; assert data.pagos == []",
        )
        plan.request("ownership.query", "GET", DASHBOARD + "?clienteId=invalid", 400)
        plan.consent(False, 1)
        for suffix in ("perfilamiento", "ofertas", "seleccion"):
            plan.request(
                "consent.revoked." + suffix,
                "GET",
                BASE + "/" + suffix,
                403,
                check="assert data.code == 'consentimiento_no_vigente'",
            )
        plan.consent(True, 2)
        plan.request("consent.old.profile", "GET", BASE + "/perfilamiento", 403)
        plan.request(
            "auth.current",
            "GET",
            "/v1/sesiones/actual",
            check="assert data.usuario.correo == vars.get('email')",
        )
        plan.request(
            "auth.refresh",
            "POST",
            "/v1/sesiones/renovar",
            body={"refreshToken": "${tokenRefresh}"},
            token=None,
            check="vars.put('token',data.accessToken); vars.put('tokenRefresh',data.refreshToken)",
        )
        plan.script("mobile.new.user", "vars.put('email','mobile-' + vars.get('email'))")
        plan.register(mobile=True)
        plan.login(mobile=True)
        plan.request(
            "auth.mobile.refresh",
            "POST",
            "/movil/v1/sesiones/renovar",
            body={"refreshToken": "${mobileTokenRefresh}"},
            token=None,
            service="mobile",
            check="vars.put('mobileToken',data.accessToken); "
            "vars.put('mobileTokenRefresh',data.refreshToken)",
        )
        plan.request(
            "auth.channel.web.to.mobile", "GET", "/movil/v1/sesiones/actual", 401, service="mobile"
        )
        plan.request(
            "auth.channel.mobile.to.web", "GET", "/v1/sesiones/actual", 401, token="mobileToken"
        )
        plan.request(
            "auth.mobile.current",
            "GET",
            "/movil/v1/sesiones/actual",
            token="mobileToken",
            service="mobile",
            check="assert data.usuario.correo == vars.get('email')",
        )
        for service, prefix, token in (
            ("web", "/v1", "token"),
            ("mobile", "/movil/v1", "mobileToken"),
        ):
            plan.request(
                "auth.logout." + service,
                "DELETE",
                prefix + "/sesiones/actual",
                204,
                body={"refreshToken": "${" + token + "Refresh}"},
                token=token,
                service=service,
            )
            plan.request(
                "auth.revoked.refresh." + service,
                "POST",
                prefix + "/sesiones/renovar",
                401,
                body={"refreshToken": "${" + token + "Refresh}"},
                token=None,
                service=service,
            )
    plan.save(path)


def persistence_plan(path: Path) -> None:
    plan = Plan()
    plan.login()
    plan.request(
        "restart.persisted.dashboard",
        "GET",
        DASHBOARD,
        check="assert data.polizas.size() == 8; assert data.pagos.size() == 8; "
        "vars.put('requestId', data.pagos[0].solicitudId); "
        "vars.put('paymentId', data.pagos[0].id)",
    )
    plan.request(
        "restart.retry.payment",
        "POST",
        BASE + "/pago-simulado",
        body={},
        key="key",
        check="assert data.id == vars.get('paymentId'); "
        "assert data.saga.estado == 'pago_confirmado'",
    )
    plan.save(path)


def outage_plan(path: Path) -> None:
    plan = Plan()
    plan.provision_customer()
    plan.login()
    plan.consent(True, 0)
    plan.quote("automotive", outage=True)
    plan.request(
        "outage.dashboard.unavailable",
        "GET",
        DASHBOARD,
        503,
        check="assert data.code == 'servicio_no_disponible'",
    )
    plan.save(path)


def recovery_plan(path: Path) -> None:
    plan = Plan()
    plan.login()
    plan.request(
        "recovery.pending.dashboard",
        "GET",
        DASHBOARD,
        check="assert data.polizas == []; assert data.pagos.size() == 1; "
        "assert data.suscripciones[0].saga.estado == 'pago_confirmado'; "
        "vars.put('requestId',data.pagos[0].solicitudId); vars.put('paymentId',data.pagos[0].id)",
    )
    for attempt in range(2):
        plan.request(
            f"recovery.retry.{attempt}",
            "POST",
            BASE + "/pago-simulado",
            body={},
            key="key",
            check="assert data.id == vars.get('paymentId'); "
            "assert data.saga.estado == 'pago_confirmado'",
        )
    plan.request(
        "recovery.emitted.dashboard",
        "GET",
        DASHBOARD,
        check="assert data.polizas.size() == 1; assert data.pagos.size() == 1; "
        "assert data.polizas[0].simulada; "
        "assert data.polizas[0].suscripcionId == data.pagos[0].suscripcionId",
    )
    plan.save(path)
