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
| `siniestros` | Siniestros | Siniestros | Registro autorizado y evidencia privada compartida; validacion local, sin despliegue |
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

No se incluye un comando universal para iniciar todos los servicios porque el punto de entrada y el gestor de dependencias deben verificarse por servicio. Cada microservicio debe documentar sus propios comandos una vez implementado.

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
