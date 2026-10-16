# Consentimiento de perfilamiento

Identidad conserva el consentimiento autoritativo. No comparte tablas con Perfilamiento.
Cada autorizacion o revocacion agrega una version y un evento Outbox en la misma transaccion.
La autorizacion explicita dura treinta dias y cubre Open Finance y Open Data para cotizacion.

```powershell
python -m pip install -e ".\services\identidad[dev]"
$env:INTERNAL_API_TOKEN = "<secreto local compartido entre servicios>"
$env:IDENTIDAD_DATABASE_URL = "sqlite:///identidad.db"
python -m flask --app identidad.app init-db
python -m flask --app identidad.app run --port 5002
```

`init-db` crea el esquema inicial, no modifica tablas existentes. Para PostgreSQL instalar
`[postgres]` y usar `postgresql+psycopg://...`. No versionar las credenciales.

API interna protegida con `Authorization: Bearer <INTERNAL_API_TOKEN>`:

| Metodo | Ruta | Uso |
|---|---|---|
| GET | `/health` | Salud sin datos del cliente |
| GET | `/v1/clientes/{uuid}/consentimiento-perfilamiento` | Ultima version o 404 |
| PUT | misma ruta | `{"autorizado":true,"version":0}` para autorizar; false para revocar |

Cada escritura requiere la version actual (cero si no existe), devuelve la siguiente version
y rechaza conflictos con 409. El cliente publico nunca elige el UUID: lo agrega el BFF desde
su sesion. Las respuestas no se cachean. La lectura incluye estado y vigencia, no presume
que una autorizacion antigua siga vigente.

Este alcance no implementa KYC, registro de usuarios ni un distribuidor de eventos. El Outbox
queda persistido; Perfilamiento valida directamente contra Identidad antes de cada uso.
