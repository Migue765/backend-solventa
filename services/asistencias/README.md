# Asistencias

Servicio backend de HU20 para consultar prestadores cercanos y registrar solicitudes
de asistencia. Es dueño del catálogo, las solicitudes y la auditoría de uso de
ubicación. El BFF móvil autentica al cliente y envía su UUID en `X-Cliente-ID`.

## Contrato

El contrato público móvil está en `contracts/openapi/asistencias-v1.yaml`.

- `POST /v1/prestadores/consulta`: recibe ubicación confirmada, tipo opcional y radio
  de hasta 100 km. Devuelve únicamente UUID, nombre, tipo y distancia aproximada.
- `POST /v1/asistencias`: exige `Idempotency-Key` UUID, prestador, tipo, ubicación y
  exactamente una póliza o un siniestro.

La ubicación admite origen `permiso` o `manual`, exige `confirmada=true`, zona horaria
explícita y una antigüedad máxima de 24 horas. La consulta no persiste coordenadas:
la auditoría conserva cliente, propósito, fecha y correlación. El registro sí guarda
la ubicación exacta porque es necesaria para atender la solicitud; no se escribe en
logs ni se devuelve al cliente.

El catálogo inicial de la migración contiene tres prestadores sintéticos de la red
Solventa para pruebas. No representa empresas reales ni un catálogo productivo.

## Autorización e idempotencia

Para registrar con póliza, Asistencias consulta Pólizas y exige que pertenezca al
cliente y esté vigente. Para registrar con siniestro, consulta su estado usando el
mismo cliente; inexistente y ajeno producen `404 recurso_no_autorizado`.

La identidad idempotente es cliente + `Idempotency-Key`. La misma clave y cuerpo
recuperan el UUID, estado y fecha originales con HTTP 200; otro cuerpo responde
`409 idempotencia_conflictiva`. El replay de una solicitud ya registrada no vuelve
a depender de Pólizas o Siniestros. Un fallo de dependencia responde 503 y nunca crea
una asistencia.

El prestador debe seguir disponible, ofrecer el tipo solicitado y estar a máximo
`ASISTENCIAS_DISTANCIA_MAX_KM` de la ubicación. Si no, responde
`409 prestador_no_disponible`.

## Configuración

- `ASISTENCIAS_DATABASE_URL`: SQLite local durable o PostgreSQL.
- `INTERNAL_API_TOKEN`: credencial compartida con BFF, Pólizas y Siniestros.
- `POLIZAS_SERVICE_URL`: URL interna de Pólizas.
- `SINIESTROS_SERVICE_URL`: URL interna de Siniestros.
- `DEPENDENCY_TIMEOUT_SECONDS`: timeout HTTP, 5 por defecto.
- `ASISTENCIAS_DISTANCIA_MAX_KM`: distancia máxima al registrar, 100 por defecto.

## Ejecución y pruebas

```powershell
python -m pip install -e ".\services\asistencias[dev]"
$env:ASISTENCIAS_DATABASE_URL = "sqlite:///asistencias.db"
python -m alembic -c services\asistencias\alembic.ini upgrade head
python -m flask --app asistencias.app run --port 15006
python -m pytest services\asistencias\tests
```

No se incluyen pantallas ni integración con proveedores externos en HU20-BE.
