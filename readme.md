# Solventa Backend

Repositorio monorepo del Backend y la infraestructura de Solventa para Proyecto Final II.

Contiene la estructura prevista para microservicios Python, contratos compartidos, librerías técnicas e infraestructura como código con Terraform. La documentación de arquitectura describe el objetivo técnico; las instrucciones locales deben corresponder únicamente a los servicios y configuraciones que estén realmente disponibles en el repositorio.

## Comprobaciones y cobertura

[📊 Cobertura de código](https://github.com/Migue765/backend-solventa/wiki/Cobertura-de-codigo)

En cada [pull request](https://github.com/Migue765/backend-solventa/pulls), abre **Checks → Cobertura**
para ver líneas, ramas, pruebas y revisión compilada. El reporte corresponde a ese commit;
no es un porcentaje fijo de la portada. En backend muestra scripts y cada servicio por separado.

Actions se ejecuta automáticamente. Un colaborador solicita Jenkins con un comentario nuevo
`/test-jenkins`; después de subir cambios debe solicitarlo para la revisión nueva.
Los reportes HTML/XML se conservan en Jenkins privado. No se exige un porcentaje mínimo nuevo.
Consulta [la guía de CI](docs/ci.md) para ver alcance, nombres y lectura de resultados.


## Tecnologías y herramientas

- Python para microservicios.
- Terraform para infraestructura como código en AWS.
- PostgreSQL/Aurora y Redis, según el servicio y el entorno.
- Contratos OpenAPI para APIs síncronas.
- Esquemas de eventos bajo `contracts/events/`.
- Git y GitHub Actions para colaboración y validaciones.


## Requisitos previos

Instala las herramientas requeridas por el servicio en el que vas a trabajar:

- Git.
- Python 3.12 o superior (lo exige `requires-python` en cada servicio).
- PostgreSQL local, si el servicio usa base de datos.
- Docker, para construir las imágenes de los servicios.
- Terraform, solo para tareas de infraestructura.

Comprueba las herramientas instaladas:

```bash
git --version
python --version
docker --version
terraform version
```

El comando `terraform version` solo es necesario si vas a trabajar con Terraform. En Windows, si `python` no está reconocido, comprueba si el equipo utiliza el lanzador `py --version`.

## Instalación del repositorio

```bash
git clone https://github.com/Migue765/backend-solventa.git
cd backend-solventa
```

## Estructura del repositorio

```text
.
├── terraform/
│   ├── bootstrap/
│   ├── global/
│   ├── modules/
│   └── environments/
│       ├── dev/
│       ├── staging/
│       └── prod/
│           ├── primary/
│           └── secondary/
├── services/
│   └── <servicio>/
│       ├── src/<paquete>/
│       │   ├── api/
│       │   ├── application/
│       │   ├── domain/
│       │   └── infrastructure/
│       └── tests/
│           ├── unit/
│           └── integration/
├── contracts/
│   ├── openapi/
│   └── events/
├── libs/
│   ├── observability/
│   └── messaging/
└── docs/
    ├── architecture/
    └── adr/
```

## Microservicios previstos

| Carpeta | Microservicio | Contexto | Estado |
|---|---|---|---|
| `bff-web` | BFF Web | Experiencias y API | Implementado, con [README](services/bff-web/README.md) |
| `bff-movil` | BFF Móvil | Experiencias y API | Implementado, con [README](services/bff-movil/README.md) |
| `api-socios` | API de Socios | Distribución | Estructura inicial |
| `identidad` | Consentimiento y KYC | Identidad y Cliente | Implementado, con [README](services/identidad/README.md) |
| `perfilamiento` | Personalización y perfil de riesgo | Perfilamiento y Personalización | Implementado, con [README](services/perfilamiento/README.md) |
| `cotizacion` | Tarificación y Cotización | Cotización | Implementado, con [README](services/cotizacion/README.md) |
| `polizas` | Suscripción, emisión y ciclo de vida | Producto y Suscripción / Pólizas | Implementado, con [README](services/polizas/README.md) |
| `siniestros` | Siniestros | Siniestros | Registro, estado, historial, evidencia en S3 y procesamiento durable de eventos paramétricos; [README](services/siniestros/README.md) y [despliegue](deploy/k8s/siniestros/README.md) |
| `notificaciones` | Notificaciones | Comunicaciones | Eventos, preferencias, avisos programados, reintentos y trazabilidad; [README](services/notificaciones/README.md) y [despliegue](deploy/k8s/notificaciones/README.md) |
| `asistencias` | Asistencias | Asistencias | Prestadores cercanos, solicitudes idempotentes y auditoría de ubicación; [README](services/asistencias/README.md) y [despliegue](deploy/k8s/asistencias/README.md) |
| `pagos` | Cobros, pagos y recaudo | Pagos y Recaudo | Estructura inicial |
| `cumplimiento` | Fraude, analítica y auditoría | Cumplimiento y Auditoría | Estructura inicial |
| `integraciones` | Adaptadores externos | Integraciones | Estructura inicial |

«Estructura inicial» indica que la carpeta existe, pero todavía no tiene código de dominio ni pruebas. Antes de seguir las instrucciones de ejecución de un servicio, revisa su README.

## Instalación y ejecución local de un microservicio

Los comandos exactos dependen de los archivos presentes en cada carpeta. Para preparar un servicio:

1. Entra en `services/<servicio>/`.
2. Identifica el archivo de dependencias y la versión de Python declarada.
3. Crea un entorno virtual.
4. Instala las dependencias según el gestor utilizado por el servicio.
5. Configura las variables locales necesarias.
6. Inicia el servicio con el punto de entrada documentado en su carpeta.
7. Ejecuta las pruebas disponibles.

Ejemplo de creación de un entorno virtual, si el servicio usa Python estándar:

```bash
python -m venv .venv
```

Activación en Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

### Web local: una base PostgreSQL, datos exclusivamente sinteticos

El recorrido local para comprobar **una BD logica** usa PostgreSQL 17 nativo
en Windows: no necesita virtualizacion ni Docker. Conserva `solventa_prod` como
nombre de BD de aplicacion. Cada dominio posee su esquema, un rol propietario
NOLOGIN, un migrador propio y un runtime separado sin DDL ni acceso a los otros
esquemas. No se conectan bases preexistentes: cada arranque inicializa un cluster
dedicado nuevo con autenticacion SCRAM en `127.0.0.1`, sin servicio de Windows,
autostart, cambios de PATH global ni reglas de firewall.

Prepara Python 3.12 y PostgreSQL 17 para tu equipo. Para Windows, parte de
[postgresql.org](https://www.postgresql.org/download/windows/), que enlaza los
[binarios ZIP de EDB](https://www.enterprisedb.com/download-postgresql-binaries).
Descarga la version 17 x64 y su archivo `.zip.sha256` del mismo proveedor,
compara `Get-FileHash -Algorithm SHA256` con el checksum publicado **antes**
de ejecutar y extrae el ZIP en una carpeta local. No uses binarios de terceros
ni el instalador como administrador. La preparacion comprobada usa 17.12-1:
`postgresql-17.12-1-windows-x64-binaries.zip`, SHA256
`a2cd8a774389347b70be7748ac4a37ef43cb6ee539c5fc3e85ae264b15e4a04c`.

Desde la raiz del checkout (PowerShell):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\services\identidad[dev,postgres]" -e ".\services\bff-web" -e ".\services\bff-movil" -e ".\services\cotizacion" -e ".\services\perfilamiento" -e ".\services\polizas" -e ".\services\siniestros" -e ".\services\notificaciones" -e ".\services\asistencias"
$pgBin = (Resolve-Path "$env:LOCALAPPDATA\PostgreSQL17\pgsql\bin").Path # ZIP verificado extraido aqui.
.\.venv\Scripts\python.exe -m scripts.local_web --postgres --postgres-bin $pgBin --postgres-port 55417 --web-port 5001
```

La carpeta de PostgreSQL es una ubicacion local elegida por quien prepara el
equipo, fuera del repositorio; los binarios y los datos no se agregan a Git. El script no
descarga ni instala PostgreSQL. Rechaza falta de binarios, puerto ocupado y errores
de migracion; **nunca pasa de PostgreSQL a SQLite por un error**.

En cada arranque se migran Identidad, Cotizacion, Polizas, Siniestros,
Notificaciones y Asistencias con sus historiales Alembic existentes en esquemas
distintos. Perfilamiento reutiliza su inicializacion existente, solo sobre
esquema nuevo; `create_all` no es una migracion de datos existentes. Se repite
la inicializacion para comprobar que no colisionan los historiales. Son 75 tablas
de los seis dominios web mas 3 de Asistencias (78 en total), sin contar las tablas
Alembic. Polizas incluye 7 tablas tras `polizas_0004` y Siniestros 23 tras
`0010_evaluacion_indemnizaciones`, que une evaluacion automatica e indemnizaciones.
Notificaciones y Asistencias quedan preparadas en SQL sin arrancar sus APIs
porque no las necesita este recorrido web.

Antes de `ready:true`, ademas del recorrido JWE descrito abajo, se comprueban
`current_database`, `current_schema`, usuario efectivo/runtime/migrador, CRUD
propio, rechazo de DDL runtime, acceso runtime denegado al historial Alembic y
denegacion de lectura/escritura entre dominios.
`databaseEvidence` muestra solo estos resultados, nunca DSN ni contrasenas.
Las conexiones generadas quedan en `postgres\connections.json` dentro del
directorio privado nuevo de esa ejecucion. No uses esas credenciales fuera de
la instancia sintetica. Ctrl+C detiene los servicios HTTP propios y ese cluster;
conserva los datos para inspeccion. `--check --web-port 0` comprueba y cierra todo.

Este modo acredita el aislamiento local de BD, **no** API Gateway administrado,
TLS, WAF, Multi-AZ ni recuperacion de desastres. El BFF local sigue siendo HTTP
directo en loopback: no presentar esa URL como cumplimiento de la entrada
web/app por API Gateway del despliegue. La comunicacion interna permanece privada.
Redis simulado, fuentes sinteticas y archivos locales se explican abajo; no
sustituyen Redis/S3 reales ni autorizan despliegue, corte o movimiento de datos.

Para ejecutar la comprobacion focal contra PostgreSQL real:

```powershell
$env:SOLVENTA_TEST_POSTGRES_BIN = $pgBin
.\.venv\Scripts\python.exe -m pytest tests\test_local_web.py -q
```

Sin esa variable, la prueba PostgreSQL se informa omitida, no acreditada por
SQLite. Cada ejecucion requiere un directorio nuevo; no hay reanudacion ni
migracion de una BD con datos reales.

### Web local anterior: SQLite sintetico, no arquitectura de BD unica

Desde la raiz del checkout, con Python 3.12, prepara una vez estas dependencias
versionadas (PowerShell; no necesita Docker, AWS, PostgreSQL ni Redis externos):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\services\identidad[dev]" -e ".\services\bff-web" -e ".\services\bff-movil" -e ".\services\cotizacion" -e ".\services\perfilamiento" -e ".\services\polizas" -e ".\services\siniestros"
.\.venv\Scripts\python.exe -m scripts.local_web --web-port 5001
```

El ultimo comando inicia BFF web, Identidad, Cotizacion, Perfilamiento, Polizas y
Siniestros con HTTP real en loopback. El BFF queda en `http://127.0.0.1:5001/v1`;
los servicios internos usan puertos libres. No inicia BFF movil, Asistencias ni
Notificaciones; bff-movil se instala solo porque el harness compartido lo importa.
El proxy web debe reenviar `/v1` sin quitarlo; la configuracion y compilacion del
frontend corresponden a su repositorio. No expone CORS ni escucha en la red:
otro PC debe arrancar su propio checkout, no conectar a este proceso.

Antes de imprimir `ready:true`, el bootstrap crea cliente/socio sinteticos,
consentimiento de prueba y una poliza academica; verifica login JWE, replay,
rechazo de plaintext, portal socio, denegacion cliente, consulta por documento y
cotizacion. Las contrasenas nuevas solo quedan en el `accountsFile` indicado;
no se imprimen tokens ni contrasenas. El directorio tiene ACL del usuario actual
en Windows (whoami/icacls incluidos en el sistema) o modo0700 en POSIX.
Cada arranque usa un directorio nuevo bajo `reports/`, ignorado por Git;
`--data-dir` permite elegir otro NUEVO y rechaza reutilizar datos existentes.
SQLite/uploads permanecen alli para inspeccion: no se copian datos de origen.
RSA/JWT/JWE y token interno se generan en memoria; no hay secretos preexistentes.

Ambos recorridos son modos sinteticos explicitos: fakeredis para desafios, FuenteMock para
OpenFinance/OpenData y pago/poliza academicos. Un error API aborta el arranque;
no se sustituye por un resultado mock. No prueba proveedores ni almacenamiento
productivos. El alta del fixture usa el contrato directo existente de Identidad;
no ejercita ni acredita el registro JWE publico del BFF.
No ejecuta Terraform ni modifica servidores, demos o instalaciones Android.

Para comprobar arranque y cierre sin dejar servicios, usa
`python -m scripts.local_web --check --web-port 0`. Para el proceso interactivo,
Ctrl+C cierra solo sus servicios. Si `DATABASE_URL`, `DB_SCHEMA` o
`POLIZAS_DATABASE_URL` estan definidas, el bootstrap falla: eliminalas del proceso
de prueba, no de configuraciones compartidas. Un puerto ocupado tambien falla;
elige otro con `--web-port` y actualiza el proxy del frontend.

## Comandos de calidad

Son los mismos que ejecuta el flujo `quality.yml` de GitHub Actions. Córrelos desde la raíz del repositorio, con el entorno virtual activo:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy scripts tests
python -m mypy --config-file services/<servicio>/mypy.ini services/<servicio>/src services/<servicio>/tests
python -m pytest services/<servicio>/tests
```

Ejecuta el comando de tipos por servicio solo si existe su `mypy.ini`; el flujo de CI omite los servicios sin ese archivo.

`ruff format` sin `--check` aplica el formato. Para no generar diferencias ajenas a tu cambio, pásale solo los archivos que modificaste.


## Reglas de arquitectura

- Cada servicio es dueño de sus datos; no se comparten tablas ni se importa el código de dominio de otro servicio.
- Los servicios se comunican mediante contratos de `contracts/` (HTTP o eventos).
- `libs/` contiene código técnico transversal, nunca reglas de negocio.
- Los módulos de Terraform no definen `providers` ni `backend`; eso se configura por ambiente.

## Terraform e infraestructura

El repositorio contempla módulos para red, EKS, API Gateway, WAF, RDS/Aurora, Redis, S3, KMS, Secrets Manager, mensajería, ECR, IAM y observabilidad.

El [productivo de la cuenta nueva](terraform/environments/nueva-cuenta/README.md)
usa nueve ECR privados, uno por API, sin reutilizar el estado anterior.
Identidad, BFF Web, Perfilamiento, Cotización y Pólizas ya funcionan sobre EKS y una sola
base relacional; el runbook distingue lo verificado de las APIs aún pendientes.

## Pruebas y verificación

Para cada servicio, documenta y ejecuta las comprobaciones que realmente estén disponibles:

1. Instalación limpia de dependencias.
2. Arranque local con configuración válida.
3. Endpoint de salud, si está implementado.
4. Conexión a la base de datos, si el servicio la requiere.
5. Pruebas unitarias.
6. Pruebas de integración, si están configuradas.


## Flujo de trabajo Git

El repositorio sigue GitFlow:

| Rama/prefijo | Propósito |
|---|---|
| `main` | Código estable; recibe releases y hotfixes |
| `develop` | Integración del desarrollo |
| `feature/*` | Nuevas funcionalidades; se integra en `develop` |
| `release/*` | Preparación de versión; se integra en `main` |
| `hotfix/*` | Correcciones urgentes basadas en `main` |
| `backport/*` | Retorno de cambios de `main` a `develop` |

Flujo de feature:

```bash
git checkout develop
git pull
git checkout -b feature/mi-funcionalidad
# realizar cambios y commits
git push -u origin feature/mi-funcionalidad
```

## Referencias

- **Jira:** backlog aprobado y criterios de aceptación.
- **Documento de arquitectura:** `docs/architecture/`, cuando esté disponible.
- **Decisiones arquitectónicas:** `docs/adr/`, cuando esté disponible.
- **Contratos API:** `contracts/openapi/`.
- **Contratos de eventos:** `contracts/events/`.

## Solución de problemas

### El comando de Python no funciona
Comprueba la versión instalada y que corresponda a la declarada por el servicio. En Windows, prueba `py --version` si el comando `python` no está disponible.

### Faltan dependencias o archivos de configuración
Confirma que estás en la carpeta del servicio correcto y que sus archivos de dependencias y configuración estén versionados. Consulta el README específico del microservicio.

### No se puede conectar a PostgreSQL
Verifica que la instancia esté ejecutándose, que el host y puerto sean correctos y que las variables locales correspondan al ambiente de desarrollo.

### Terraform solicita credenciales o estado remoto
Eso puede ser esperado para tareas de infraestructura. Verifica permisos y configuración con el equipo; no agregues credenciales al repositorio ni ejecutes cambios sobre ambientes compartidos sin autorización.
