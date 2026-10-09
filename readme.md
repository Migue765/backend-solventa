Este es el repositorio de backend

## Estructura del repositorio

Monorepo con la infraestructura (Terraform) y los microservicios (Python) de Solventa, según el documento de arquitectura (contextos delimitados, puertos y adaptadores, despliegue en EKS multi-AZ con Warm Standby multirregional).

```
.
├── terraform/                # Infraestructura como código (AWS)
│   ├── bootstrap/            # Backend remoto del state (S3 + lock)
│   ├── global/               # Recursos globales (DNS, replicación de ECR, IAM)
│   ├── modules/              # Módulos reutilizables, uno por capacidad
│   └── environments/         # Composición de módulos por ambiente
│       ├── dev/
│       ├── staging/
│       └── prod/
│           ├── primary/      # Región principal (multi-AZ)
│           └── secondary/    # Región de recuperación (Warm Standby)
├── services/                 # Un microservicio por carpeta (un contexto / workload)
│   └── <servicio>/
│       ├── src/<paquete>/
│       │   ├── api/            # Entrada HTTP: routers y esquemas
│       │   ├── application/    # Casos de uso y puertos
│       │   ├── domain/         # Agregados, entidades, value objects y eventos
│       │   └── infrastructure/ # Adaptadores: BD, mensajería, proveedores externos
│       └── tests/
│           ├── unit/
│           └── integration/
├── contracts/                # Contratos versionados
│   ├── openapi/              # APIs síncronas
│   └── events/               # Esquemas de eventos del Event Bus
├── libs/                     # Librerías técnicas compartidas (sin reglas de negocio)
│   ├── observability/        # Logs, métricas, trazas y correlationId
│   └── messaging/            # Outbox, Inbox e idempotencia
└── docs/
    ├── architecture/         # Documento de arquitectura y diagramas
    └── adr/                  # Registro de decisiones de arquitectura
```

### Microservicios

| Carpeta          | Microservicio                               | Contexto                       |
|------------------|---------------------------------------------|--------------------------------|
| `bff-web`        | BFF Web                                     | Experiencias y API             |
| `bff-movil`      | BFF Móvil                                   | Experiencias y API             |
| `api-socios`     | API de Socios                               | Experiencias y API / Distribución |
| `identidad`      | Consentimiento y KYC                        | Identidad y Cliente            |
| `perfilamiento`  | Personalización y perfil de riesgo          | Perfilamiento y Personalización |
| `cotizacion`     | Tarificación y Cotización                   | Cotización                     |
| `polizas`        | Suscripción, emisión y ciclo de vida        | Producto y Suscripción / Pólizas |
| `siniestros`     | Siniestros                                  | Siniestros                     |
| `pagos`          | Cobros, pagos y recaudo                     | Pagos y Recaudo                |
| `cumplimiento`   | Fraude, analítica y auditoría               | Cumplimiento y Auditoría       |
| `integraciones`  | Adaptadores externos                        | Integraciones (puertos y adaptadores) |

Reglas:
- Cada servicio es dueño de sus datos: no se comparten tablas ni se importa código de dominio de otro servicio.
- Los servicios se comunican solo mediante los contratos de `contracts/` (HTTP o eventos).
- `libs/` contiene solo código técnico transversal, nunca reglas de negocio.

### Módulos de Terraform

| Módulo              | Recurso AWS                                         |
|---------------------|-----------------------------------------------------|
| `network`           | VPC multi-AZ, subredes, NAT, endpoints              |
| `eks`               | Clúster EKS privado y node pools                    |
| `api-gateway`       | API Gateway y VPC Link V2                           |
| `waf`               | Reglas WAF del borde                                |
| `rds-aurora`        | Aurora por dominio, réplica global y backups (PITR) |
| `elasticache-redis` | Redis multi-AZ (caché no autoritativa)              |
| `s3`                | Buckets de evidencias (versionado, Object Lock/WORM, replicación) |
| `kms`               | Llaves de cifrado                                   |
| `secrets-manager`   | Secretos y rotación                                 |
| `messaging`         | Event Bus, colas y DLQ                              |
| `ecr`               | Repositorios de imágenes                            |
| `iam-irsa`          | Roles IAM por service account                       |
| `observability`     | Logs, métricas, alarmas y dashboards                |

Buenas prácticas:
- Los módulos no definen providers ni backend; eso lo hace cada ambiente.
- Cada ambiente (y cada región de `prod`) tiene su propio state remoto.
- Las versiones de Terraform y de los providers se fijan en `versions.tf`, y `.terraform.lock.hcl` se versiona.

## Gitflow

El repositorio sigue el modelo **gitflow** con dos ramas permanentes:

| Rama      | Propósito                                      |
|-----------|------------------------------------------------|
| `main`    | Código en producción. Solo recibe releases y hotfixes. |
| `develop` | Integración del desarrollo en curso.           |

### Ramas de trabajo y a dónde pueden mergearse

| Prefijo       | Se crea desde | Se mergea a | Uso                                         |
|---------------|---------------|-------------|---------------------------------------------|
| `feature/*`   | `develop`     | `develop`   | Nuevas funcionalidades.                     |
| `release/*`   | `develop`     | `main`      | Preparar una versión para producción.       |
| `hotfix/*`    | `main`        | `main`      | Correcciones urgentes en producción.        |
| `backport/*`  | `main`        | `develop`   | Generada automáticamente tras un release o hotfix. |

Cualquier otra combinación (por ejemplo `feature/*` → `main` o `hotfix/*` → `develop`) es rechazada.

```
feature/x ──► develop ──► release/x ──► main
                 ▲                        │
                 └──── backport/<fecha> ◄─┘
                                          ▲
                              hotfix/x ───┘
```

### Reglas automatizadas

1. **Validación de ramas** (`.github/workflows/gitflow-validation.yml`)
   En cada PR hacia `main` o `develop` se ejecuta el check `validate-branch`, que falla si el prefijo de la rama origen no está permitido para la rama destino según la tabla anterior.

2. **Al menos 1 aprobación**
   Se aplica con branch protection en `main` y `develop`: ningún PR se puede mergear sin 1 review aprobada y sin que el check `validate-branch` pase.

3. **Backport automático** (`.github/workflows/backport.yml`)
   Cuando se mergea un PR de `release/*` o `hotfix/*` a `main`, se crea la rama `backport/<YYYY-MM-DD>` desde `main` y se abre automáticamente un PR hacia `develop`, para que los cambios de producción vuelvan al desarrollo. Si ya existe un backport ese día, se agrega la hora (`backport/<YYYY-MM-DD>-<HHMMSS>`).
   Los hotfixes también se backportean porque, en gitflow, todo lo que llega a `main` debe volver a `develop`.

### Flujo de trabajo

```bash
# Feature
git checkout develop && git pull
git checkout -b feature/mi-funcionalidad
# ... commits ...
git push -u origin feature/mi-funcionalidad   # PR -> develop

# Release
git checkout develop && git pull
git checkout -b release/1.0.0
git push -u origin release/1.0.0              # PR -> main (al mergear se crea el backport)

# Hotfix
git checkout main && git pull
git checkout -b hotfix/fix-critico
git push -u origin hotfix/fix-critico         # PR -> main (al mergear se crea el backport)
```

### Configuración inicial (una sola vez, requiere admin del repo)

**Branch protection** en `main` y `develop` (1 aprobación + check `validate-branch` obligatorio):

```bash
for branch in main develop; do
  gh api -X PUT repos/Migue765/backend-solventa/branches/$branch/protection --input - <<JSON
{
  "required_status_checks": { "strict": true, "contexts": ["validate-branch"] },
  "enforce_admins": true,
  "required_pull_request_reviews": { "required_approving_review_count": 1 },
  "restrictions": null
}
JSON
done
```

**Permitir que GitHub Actions cree PRs** (necesario para el backport):

```bash
gh api -X PUT repos/Migue765/backend-solventa/actions/permissions/workflow \
  -f default_workflow_permissions=write -F can_approve_pull_request_reviews=true
```

**Token para el backport (recomendado):** los PRs creados con el `GITHUB_TOKEN` por defecto no disparan otros workflows, así que el check `validate-branch` no correría en el PR de backport y quedaría bloqueado. Para evitarlo, creá un Personal Access Token con permisos `contents` y `pull-requests` de escritura y guardalo como secret `BACKPORT_TOKEN`:

```bash
gh secret set BACKPORT_TOKEN -R Migue765/backend-solventa
```
