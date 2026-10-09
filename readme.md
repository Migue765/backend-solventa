Este es el repositorio de backend

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
