// Escenario ASR-ESC-01: de 500 a 50.000 cotizaciones por minuto.
// Uso: k6 run -e BASE_URL=https://<api> loadtest/cotizacion.js
import http from "k6/http";
import { check } from "k6";
import { uuidv4 } from "https://jslib.k6.io/k6-utils/1.4.0/index.js";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";
const PRODUCTO_AUTOMOTRIZ = "11111111-1111-4111-8111-111111111111";

export const options = {
  scenarios: {
    rampa: {
      executor: "ramping-arrival-rate",
      startRate: 500,
      timeUnit: "1m",
      preAllocatedVUs: 200,
      maxVUs: 2000,
      stages: [
        { target: 500, duration: "2m" }, // línea base estable
        { target: 50000, duration: "1m" }, // sube a 50.000 RPM en 60 s
        { target: 50000, duration: "5m" }, // ventana estable
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<250", "p(99)<500"],
  },
};

export default function () {
  const cuerpo = JSON.stringify({
    clienteId: uuidv4(),
    productoId: PRODUCTO_AUTOMOTRIZ,
    perfilId: "perfil-carga",
    moneda: "COP",
    coberturas: [{ coberturaId: uuidv4(), sumaAsegurada: "25000000", deducible: "500000" }],
  });
  const respuesta = http.post(`${BASE_URL}/v1/cotizaciones`, cuerpo, {
    headers: { "Content-Type": "application/json" },
  });
  check(respuesta, { "201": (r) => r.status === 201 });
}
