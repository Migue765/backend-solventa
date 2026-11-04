# Siniestros

Servicio interno con base de datos propia. Hoy implementa la consulta de estado e
historial de un siniestro (HU25 Web, SOL-318). Es la capacidad de dominio que
compartira HU21 movil (SOL-309): ambos canales leen la misma respuesta, sin reglas
por canal. El registro del aviso (HU18/HU23, SOL-311/SOL-313) y las transiciones
de evaluacion (HU26/HU27) todavia no estan implementados; hasta entonces los
siniestros solo existen si se insertan directamente en la base.

## Modelo

Sigue el agregado `Siniestro` del modelo de dominio del documento de arquitectura
(contexto Siniestros, datos propios segun D-02, clasificacion **Restringida**).

- `siniestros`: cliente, poliza, `tipo` (`asistido` | `parametrico`), estado vigente,
  `ocurrido_en` (evento), `reportado_en` y `actualizado_en`.
- `historial_estados_siniestro`: cambios por `secuencia`, con `estado`, `ocurrido_en`
  y `visible_cliente`.

`montoReclamado`, Decision de Siniestro, Evaluacion de Fraude y Evidencias pertenecen
al agregado pero no forman parte de esta proyeccion: por minimizacion de datos la
consulta del cliente nunca los expone. Se incorporaran con HU18/HU23, HU24 y HU26/HU27.

Estados: `reportado`, `en_evaluacion`, `en_revision`, `en_inspeccion`, `aprobado`,
`rechazado`, `pagado`, `cerrado`. La descripcion que ve el cliente sale de un catalogo
del dominio (`domain/estado.py`); no se guarda ni expone texto interno.

## Historial permitido e integridad

El historial devuelto contiene los cambios con `visible_cliente=true` y siempre el cambio
que fijo el estado vigente. Antes de responder se verifica que el historial no este vacio,
use un tipo conocido, tenga `ocurrido_en <= reportado_en`,
inicie con `reportado` en `reportado_en`, sea cronologico, use estados conocidos y que
su ultimo cambio coincida con `estado` y `actualizado_en`. Si algo no cuadra responde
`503 siniestro_no_integro`: un registro incoherente nunca se presenta como estado valido.

## API interna

Exige el `INTERNAL_API_TOKEN` como Bearer. No se publica en API Gateway ni en el ALB.

`GET /v1/siniestros/{id}?clienteId=<UUID>` devuelve
`{id,polizaId,tipo,estado,descripcion,ocurridoEn,reportadoEn,actualizadoEn,historial:[{estado,descripcion,ocurridoEn}]}`.
La busqueda filtra por id **y** cliente, asi que un siniestro inexistente y uno de otro
cliente responden igual: `404 siniestro_no_encontrado`. Parametros invalidos: `400`;
credencial ausente o incorrecta: `401`; fallo de persistencia: `503`. Todas las
respuestas llevan `Cache-Control: no-store` y `X-Correlation-ID`.

## Prueba local con la Web (SOL-177)

Usa el entorno `.venv` de la raiz con identidad, cotizacion, siniestros y bff-web
instalados (`pip install -e`). Abre una consola de PowerShell por servicio, todas en la
raiz del backend, y define en **cada una** la misma credencial interna local:

```powershell
$env:INTERNAL_API_TOKEN = "<credencial local, no produccion>"
```

1. Identidad: sigue los pasos de [bff-web](../bff-web/README.md#integración-local-en-powershell)
   en el puerto `15003`.
2. Cotizacion (solo para que el tablero de inicio cargue): igual que en bff-web, puerto `15000`.
3. Siniestros:

   ```powershell
   $env:SINIESTROS_DATABASE_URL = "sqlite:///siniestros-local.db"
   .\.venv\Scripts\python -m alembic -c services\siniestros\alembic.ini upgrade head
   .\.venv\Scripts\python -m flask --app siniestros.app run --host 127.0.0.1 --port 15005
   ```

4. BFF Web en el puerto `5001`, que es el destino de `proxy.conf.json` de la Web
   (detén antes cualquier BFF anterior que ocupe ese puerto):

   ```powershell
   $env:PYTHONPATH = (Resolve-Path services\bff-web\src).Path
   $env:IDENTIDAD_URL = "http://127.0.0.1:15003"
   $env:JWT_ISSUER = "http://127.0.0.1:15003"
   $env:QUOTE_SERVICE_URL = "http://127.0.0.1:15000"
   $env:SINIESTROS_SERVICE_URL = "http://127.0.0.1:15005"
   .\.venv\Scripts\python -m flask --app bff_web.app run --host 127.0.0.1 --port 5001
   ```

5. Registra una cuenta, obtén su id y siembra tres siniestros de ejemplo
   (reportado, en inspeccion con un paso interno oculto, y parametrico pagado):

   ```powershell
   $cuenta = @{ nombre = "Cliente Local"; correo = "cliente.local@solventa.co"; contrasena = Read-Host "Contrasena (min. 8)" }
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5001/v1/usuarios -ContentType "application/json" -Body ($cuenta | ConvertTo-Json)
   $sesion = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5001/v1/sesiones -ContentType "application/json" -Body (@{ correo = $cuenta.correo; contrasena = $cuenta.contrasena } | ConvertTo-Json)
   $env:SINIESTROS_DATABASE_URL = "sqlite:///siniestros-local.db"
   .\.venv\Scripts\python services\siniestros\scripts\sembrar_local.py --cliente-id $sesion.usuario.id
   ```

   El script imprime los numeros de siniestro y solo acepta bases SQLite.

6. En `solventa-web-pf2`, `pnpm start:backend`, abre `http://localhost:4200`, inicia
   sesion con esa cuenta y entra a **Siniestros**; pega uno de los numeros impresos.
   Para ver el caso no disponible usa un UUID cualquiera; para ver el error recuperable
   detén Siniestros y pulsa **Actualizar**.

## Ejecucion y validacion

Desde la raiz, en PowerShell:

```powershell
python -m pip install -e ".\services\siniestros[dev]"
$env:SINIESTROS_DATABASE_URL = "sqlite:///siniestros-local.db"
$env:INTERNAL_API_TOKEN = "<credencial local, no produccion>"
python -m alembic -c services\siniestros\alembic.ini upgrade head
python -m flask --app siniestros.app run --port 5005
```

En el BFF Web usar `SINIESTROS_SERVICE_URL=http://127.0.0.1:5005` y la misma credencial.

`python -m pytest services\siniestros\tests tests\test_claims_flow.py` verifica consulta,
historial permitido, aislamiento entre clientes, inexistencia, registros incoherentes,
fallo de persistencia, contrato OpenAPI y latencia. La latencia se mide con 200 consultas
en proceso y extremo a extremo (BFF -> HTTP -> Siniestros sobre SQLite) contra
p95 <= 150 ms y p99 <= 300 ms. Es una linea base local, no acredita el SLA desplegado.
