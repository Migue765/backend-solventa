# Integracion principal de Polizas

No crear un preview ni desplegar imagenes de una feature. Se requiere integrar
la PR, promocionar por el flujo de releases principal y aprobar su despliegue.
Los cambios de Terraform crean ECR `solventa/polizas` y el pipeline
`solventa-prod-polizas`, reutilizando conexion, red y roles con alcance al
namespace `polizas`. El tag `polizas-v*` es disjunto de los otros servicios.
Revisar el plan antes del apply; este documento no autoriza cambiar recursos
ajenos ni ejecutar un apply completo sin revision.

## Prerrequisitos de la primera entrega

1. En la cuenta nueva usar la unica PostgreSQL `solventa_prod`, esquema `polizas`,
   owner NOLOGIN y usuarios runtime/migrator separados, con TLS `verify-full` y
   CA RDS de la imagen. No compartir tablas ni usar SQLite/emptyDir.
   Aprovisionar mediante el procedimiento de bases aprobado, sin imprimir
   credenciales ni versionarlas.
2. Aplicar `namespace.yaml`; sincronizar el Secret externo `polizas-runtime`
   con `POLIZAS_DATABASE_URL` (driver `postgresql+psycopg`) y el mismo
   `INTERNAL_API_TOKEN` de Cotizacion. El pipeline no crea ni obtiene secretos.
3. Revisar/aplicar los modulos nuevos de infraestructura desde la rama integrada;
   publicar un tag de release de Polizas y seguir build, migracion y rollout.
   El pipeline aplica `service.yaml` y `deployment.yaml` y soporta el primer
   Deployment inexistente. Si la primera entrega falla no simula un rollback
   hacia una version que nunca existio.
4. Desplegar las versiones integradas de Cotizacion (migracion `emision_polizas`)
   y BFF Web. Solo entonces habilitar en el Deployment principal de Cotizacion:

```powershell
kubectl -n cotizacion set env deployment/cotizacion POLIZAS_SERVICE_URL=http://polizas.polizas.svc.cluster.local:8080
kubectl -n cotizacion rollout status deployment/cotizacion --timeout=300s
```

Esta activacion es intencionadamente explicita: un rollout de Cotizacion no
activa la dependencia antes de que exista el nuevo servicio. Es una variable
persistida en el Deployment principal, no una configuracion de un preview.
No se necesita un endpoint publico nuevo ni modificar las rutas del gateway.
El frontend compatible debe integrarse en la misma entrega.

## Aceptacion de negocio obligatoria

Desde la web principal con un cliente de prueba controlado: registro/login,
consentimiento/perfilamiento vigente, cotizacion, ofertas, seleccion,
suscripcion, pago simulado y vuelta al inicio. Comprobar ID de poliza,
suscripcion origen, tipo, fechas y estado; conservar el rotulo academico.
Repetir el pago y recargar la pagina no debe generar otro pago ni poliza.
Iniciar otra sesion/cliente: sus polizas no deben incluir las del primero.

Comprobar las filas en los esquemas cotizacion/polizas con un operador autorizado, sin exponer
identidad, secretos o tokens en informes. Verificar que pagos historicos no
se convierten en polizas y que el dashboard no tiene efectos de escritura.
Una caida de Polizas debe verse como `503`, no como una lista vacia ni emision
exitosa. Ready, `/health` o una prueba local no sustituyen esta aceptacion.

## Rollback

Conservar los esquemas y las migraciones aditivas. Si hay que retirar la nueva
dependencia, deshabilitar la emision en Cotizacion antes de revertir servicios:

```powershell
kubectl -n cotizacion set env deployment/cotizacion POLIZAS_SERVICE_URL-
kubectl -n cotizacion rollout status deployment/cotizacion --timeout=300s
```

El dashboard responde `503` si se retira la dependencia; el marcador legado ya
no es valido ni representa cero polizas. Los pagos marcados pendientes necesitan
reintento despues de rehabilitar Polizas; nunca borrar sus datos para recuperar.
