# Prueba aislada HU11 en AWS

Este despliegue no reemplaza BFF, Identidad ni Cotizacion existentes. Usa el namespace
`hu11-perfilamiento`, una replica por servicio y los esquemas separados `hu11_identidad`,
`hu11_perfilamiento`, `hu11_cotizacion` sobre el RDS de pruebas existente. Cada servicio
usa sus tablas; no lee ni modifica tablas de otro contexto.
El despliegue establece `COTIZACION_PERFILAMIENTO_HABILITADO=true`; sin esa opcion se conserva
el comportamiento anterior para no interrumpir los despliegues que aun no migran a HU11.

La imagen de prueba contiene los cuatro paquetes pero se ejecutan en procesos y servicios
independientes. El BFF usa un solo worker porque las sesiones actuales estan en memoria.
Al reiniciarlo se requiere iniciar sesion otra vez. Los consentimientos y perfiles quedan en RDS.

## Antes de desplegar

Instalar los cuatro servicios y las herramientas del repositorio. Ejecutar:

```powershell
python -m pytest tests services\identidad\tests services\perfilamiento\tests services\cotizacion\tests services\bff-web\tests
python -m ruff check .
python -m ruff format --check .
python -m mypy scripts tests
python scripts\validate_openapi.py
```

Para construir sin Docker local puede usarse el proyecto CodeBuild existente de Cotizacion con
un ZIP del codigo en su bucket de artefactos, source override S3, artefactos NO_ARTIFACTS y
`buildspecOverride=deploy/hu11/buildspec.yml`. Requiere `ECR_REPOSITORY_URL` y un tag
`HU11_IMAGE_TAG` unico. No subir archivos locales de credenciales ni configuracion de Copilot.

## Servicios

Preparar fuera del repositorio un archivo JSON con `email` y `password` de un cliente sintetico.
El script transforma la clave en un hash y no la imprime.

```powershell
python deploy\hu11\deploy.py --image "<ECR>:<tag>" --kubeconfig "<kubeconfig de prueba>" --access-file "<archivo local privado>"
```

El script lee el secreto `cotizacion-db` del namespace original solo para conectar al RDS.
Crea un secreto propio con URLs de esquema, token interno y credencial de prueba. No cambia el
secreto ni los deployments originales. El token se conserva en despliegues siguientes.
Un Job crea los esquemas/tablas iniciales y aplica las migraciones de Cotizacion antes del rollout.
No usar este script para una migracion de produccion ni con clientes reales.

## Ruta publica

Crear un target group propio en el ALB interno y asociar `bff-web:8080` del namespace HU11 mediante
TargetGroupBinding. Agregar una regla exclusiva `/hu11/*` con reescritura de URL
`^/hu11/(.*)$` a `/$1`, sin modificar las reglas existentes. En HTTP API agregar
`ANY /hu11/{proxy+}` apuntando a la integracion VPC Link existente.

La URL de prueba queda `<API_GATEWAY>/hu11`. Solo publica el BFF; Identidad, Perfilamiento y
Cotizacion quedan internos. Desde la web local usar un proxy HTTPS, conservando cookie Secure.
Probar login, autorizacion, cotizacion, consulta del perfil, ofertas y revocacion.

## Limites

Los proveedores son mocks; no se validan ASR de carga, WORM ni propagacion por eventos. No se
crea otro cluster, base, NAT o ALB. Si se retira el entorno, borrar solo su ruta, regla, target
group y namespace, nunca recursos de los servicios originales. Conservar o eliminar sus
esquemas con aprobacion explicita porque contienen evidencia persistida de prueba.
