# Perfilamiento con consentimiento persistido

Se reutilizan el Deployment/Service `perfilamiento` y la base de datos del
namespace existente `hu11-perfilamiento`; no se reemplazan los usuarios ni el
JWT del servicio Identidad. La imagen se construye desde `services/perfilamiento`
y puede publicarse como `perfilamiento-<version>-<commit>` en el repositorio ECR
existente de Cotizacion. `deployment.yaml` solo sustituye `${IMAGE}`.

`hu11-runtime` debe contener `PERFILAMIENTO_DATABASE_URL` e `INTERNAL_API_TOKEN`.
Identidad, Cotizacion y BFF Web usan una copia del mismo token en el secreto
`profiling-internal` de su propio namespace. No incluir valores en Git ni en logs.
El perfilamiento consulta el consentimiento del servicio JWT de Identidad.
Esta reutilizacion cambia tambien la dependencia del entorno HU11 aislado:
sus usuarios sinteticos anteriores no equivalen a los usuarios JWT actuales.

Orden: migrar Identidad a `0002`, desplegar Identidad con el token interno,
desplegar Perfilamiento, habilitar Cotizacion y actualizar el BFF. Los
manifiestos de esos servicios referencian el secreto; la creacion/copia de
secretos se hace fuera de Git.

Las tablas de perfilamiento existentes se conservan; para una base vacia ejecutar
`flask --app perfilamiento.app init-db` antes de arrancar. La inicializacion no
se ejecuta al recibir peticiones. Las fuentes siguen siendo fixtures sinteticas,
pero la autorizacion/revocacion y las relaciones perfil/cotizacion se persisten.
No se crean consentimientos ni perfiles para las solicitudes anteriores.
