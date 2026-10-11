# Solventa Backend

Repositorio monorepo del Backend y la infraestructura de Solventa para Proyecto Final II.

Contiene la estructura prevista para microservicios Python, contratos compartidos, librerías técnicas e infraestructura como código con Terraform. La documentación de arquitectura describe el objetivo técnico; las instrucciones locales deben corresponder únicamente a los servicios y configuraciones que estén realmente disponibles en el repositorio.


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
- Python 
- PostgreSQL local 
- Docker, .
- Terraform 

Comprueba las herramientas instaladas:

```bash
git --version
python --version
terraform version
```

El comando `terraform version` solo es necesario si vas a trabajar con Terraform. En Windows, si `python` no está reconocido, comprueba si el equipo utiliza el lanzador `py --version`.

## Instalación del repositorio

```bash
git clone https://github.com/Migue765/backend-solventa.git
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

| Carpeta | Microservicio | Contexto |
|---|---|---|
| `bff-web` | BFF Web | Experiencias y API |
| `bff-movil` | BFF Móvil | Experiencias y API |
| `api-socios` | API de Socios | Distribución |
| `identidad` | Consentimiento y KYC | Identidad y Cliente |
| `perfilamiento` | Personalización y perfil de riesgo | Perfilamiento y Personalización |
| `cotizacion` | Tarificación y Cotización | Cotización |
| `polizas` | Suscripción, emisión y ciclo de vida | Producto y Suscripción / Pólizas |
| `siniestros` | Siniestros | Siniestros |
| `pagos` | Cobros, pagos y recaudo | Pagos y Recaudo |
| `cumplimiento` | Fraude, analítica y auditoría | Cumplimiento y Auditoría |
| `integraciones` | Adaptadores externos | Integraciones |

Esta tabla describe los servicios previstos. Verifica que la carpeta y la configuración de cada servicio existan antes de seguir sus instrucciones de ejecución.

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
