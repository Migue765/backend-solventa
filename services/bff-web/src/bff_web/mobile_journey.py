"""Operaciones de cliente compartidas con el BFF movil; no monta el portal web."""

from datetime import UTC, datetime

from flask import Blueprint, Flask, Response, g, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import RequestEntityTooLarge

from bff_web.api import consents, policy_cancellations, policy_documents, quotes
from bff_web.api.identity import invalid_request_response, rejection_response
from bff_web.api.schemas import EncryptedRegisterRequest, created_user_to_json
from bff_web.api.security import bearer_token, invalid_session_response
from bff_web.api.users import registration_challenge
from bff_web.config import Config
from bff_web.domain.errors import (
    IdentityRejectedError,
    IdentityUnavailableError,
    InvalidAccessTokenError,
)
from bff_web.domain.profiling import ProfilingError
from bff_web.domain.quote_errors import QuoteServiceUnavailableError
from bff_web.infrastructure.http_identity_gateway import HttpIdentityGateway
from bff_web.infrastructure.http_policy_cancellation_gateway import HttpPolicyCancellationGateway
from bff_web.infrastructure.http_policy_document_gateway import HttpPolicyDocumentGateway
from bff_web.infrastructure.http_profiling_gateway import HttpProfilingGateway
from bff_web.infrastructure.http_quote_gateway import HttpQuoteGateway
from bff_web.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier, http_jwks_fetcher
from bff_web.problems import problem_response


def register_mobile_journey(app: Flask, config: Config) -> None:
    token = config.internal_api_token
    identity = HttpIdentityGateway(
        config.identity_url,
        config.identity_timeout_seconds,
        client_id="movil",
        internal_api_token=token,
    )
    verifier = JwtVerifier(
        JwksKeyStore(http_jwks_fetcher(config.jwks_endpoint, config.identity_timeout_seconds)),
        config.jwt_issuer,
        config.jwt_audience,
        "movil",
    )
    app.extensions.update(
        journey_identity=identity,
        quote_gateway=HttpQuoteGateway(config.quote_service_url, internal_api_token=token),
        profiling_gateway=HttpProfilingGateway(
            config.identity_url, config.quote_service_url, token
        ),
        policy_document_gateway=HttpPolicyDocumentGateway(config.policies_service_url, token),
        policy_cancellation_gateway=HttpPolicyCancellationGateway(
            config.policies_service_url, token
        ),
    )
    bp = Blueprint("journey", __name__, url_prefix="/movil/v1")
    base = "/solicitudes-cotizacion"
    quote = base + "/<request_id>"
    policy = "/polizas/<policy_id>"
    bp.route("/consentimientos/perfilamiento", methods=["GET", "PUT"])(consents.profiling_consent)
    bp.get("/productos-seguro")(quotes.list_products)
    bp.get("/productos-seguro/<product_id>")(quotes.get_product)
    bp.get("/destinos-viaje")(quotes.list_travel_destinations)
    bp.get(base)(quotes.list_requests)
    bp.post(base)(quotes.create_request)

    @bp.get(base + "/en-curso")
    def in_progress() -> Response:
        if request.args or request.get_data():
            return problem_response(
                400,
                "solicitud_invalida",
                "Solicitud invalida",
                "No se admiten parametros ni cuerpo.",
            )
        try:
            dashboard = quotes._gateway().get_dashboard(g.journey_claims.user.user_id)
        except QuoteServiceUnavailableError:
            return quotes._unavailable()
        subs = {sub.request_id: sub for sub in dashboard.subscriptions}
        rejected = {pay.request_id for pay in dashboard.payments if pay.status == "rechazado"}
        now = datetime.now(UTC)
        items = []
        for item in dashboard.quotes:
            if item.request_id is None or item.request_id in rejected:
                continue
            sub = subs.get(item.request_id)
            if sub and (sub.status == "rechazada" or sub.saga.status in {"emitida", "fallida"}):
                continue
            if sub is None and item.valid_until <= now:
                continue
            items.append(
                {
                    "solicitudId": item.request_id,
                    "productoId": item.product_id,
                    "creadaEn": item.created_at.isoformat(),
                    "validaHasta": item.valid_until.isoformat(),
                }
            )
        return jsonify(solicitudes=items)

    bp.get(quote)(quotes.get_request)
    bp.get(quote + "/ofertas")(quotes.list_offers)
    bp.get(quote + "/seleccion-provisional")(quotes.get_provisional_selection)
    bp.put(quote + "/seleccion-provisional")(quotes.select_provisional_offer)
    bp.get(quote + "/seleccion")(quotes.get_selection)
    bp.put(quote + "/seleccion")(quotes.select_offer)
    bp.post(quote + "/suscripcion")(quotes.start_subscription)
    bp.get(quote + "/suscripcion")(quotes.get_subscription)
    bp.post(quote + "/pago-simulado")(quotes.confirm_simulated_payment)
    bp.get(quote + "/pago-simulado")(quotes.get_simulated_payment)
    bp.get(policy + "/documento")(policy_documents.get_document)
    bp.get(policy + "/documentos/<document_id>/descarga")(policy_documents.download)
    bp.get(policy + "/cancelacion")(policy_cancellations.get_state)
    bp.post(policy + "/cancelaciones")(policy_cancellations.cancel)
    bp.errorhandler(ProfilingError)(consents.profiling_error)

    @bp.before_request
    def customer() -> Response | None:
        try:
            claims = verifier.verify(bearer_token() or "")
        except InvalidAccessTokenError:
            return invalid_session_response()
        if claims.user.role != "cliente" or claims.scope != "cliente":
            return problem_response(
                403, "alcance_insuficiente", "Acceso denegado", "Requiere cliente."
            )
        if claims.session_id is None or not identity.session_active(
            claims.session_id, str(g.correlation_id)
        ):
            return invalid_session_response()
        g.journey_claims = claims
        return None

    @bp.errorhandler(IdentityUnavailableError)
    def unavailable(_: IdentityUnavailableError) -> Response:
        response = problem_response(
            503, "servicio_no_disponible", "Servicio no disponible", "Intenta nuevamente."
        )
        response.headers["Retry-After"] = "5"
        return response

    @bp.after_request
    def private(response: Response) -> Response:
        if request.endpoint in {"journey.get_document", "journey.download"}:
            return policy_documents.private(response)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    app.register_blueprint(bp)
    register_customer_enrollment(app, identity)


def register_customer_enrollment(app: Flask, identity: HttpIdentityGateway) -> None:
    bp = Blueprint("customer_enrollment", __name__, url_prefix="/movil/v1/usuarios")

    @bp.get("/desafio")
    def challenge() -> Response:
        return registration_challenge(identity)

    @bp.post("/registro-completo")
    def register() -> Response:
        request.max_content_length = 16384
        try:
            payload = EncryptedRegisterRequest.model_validate(request.get_json())
            user = identity.encrypted_register(
                payload.model_dump(mode="json"), str(g.correlation_id)
            )
        except (ValidationError, RequestEntityTooLarge):
            return invalid_request_response()
        except IdentityRejectedError as error:
            return rejection_response(error)
        response = jsonify(created_user_to_json(user))
        response.status_code = 201
        return response

    @bp.errorhandler(IdentityUnavailableError)
    def unavailable(_: IdentityUnavailableError) -> Response:
        return problem_response(
            503,
            "servicio_no_disponible",
            "Servicio no disponible",
            "No se pudo confirmar el registro.",
        )

    @bp.after_request
    def private(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store"
        return response

    app.register_blueprint(bp)
