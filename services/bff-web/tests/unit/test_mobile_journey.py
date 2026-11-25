import pytest
from bff_web.config import Config
from bff_web.mobile_journey import register_mobile_journey
from flask import Flask


@pytest.mark.parametrize("entorno,pago", [("real", False), ("sintetico", True)])
def test_pago_solo_en_demo_sin_ocultar_suscripcion(entorno: str, pago: bool) -> None:
    local = "http://127.0.0.1"
    config = Config(local, "issuer", quote_service_url=local, policies_service_url=local)
    app = Flask(__name__)
    register_mobile_journey(app, config, entorno=entorno)
    rutas = {
        (r.rule.rsplit("/", 1)[-1], m) for r in app.url_map.iter_rules() for m in r.methods or ()
    }
    for method in ("GET", "POST"):
        assert (("pago-simulado", method) in rutas) is pago
        assert ("suscripcion", method) in rutas


def test_demo_rechaza_cotizacion_no_local() -> None:
    local = "http://127.0.0.1"
    config = Config(
        local, "issuer", quote_service_url="https://cotizacion", policies_service_url=local
    )
    with pytest.raises(ValueError, match="loopback"):
        register_mobile_journey(Flask(__name__), config, entorno="sintetico")
