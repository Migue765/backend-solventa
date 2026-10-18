# CI, CD y backport verificado del backend

## Estado

El código está preparado para revisión. El automático permanece desactivado mientras
`BACKPORT_AUTOMATION_ENABLED` no sea `true`. Este PR no crea Apps, secretos, excepciones,
recursos AWS ni nuevos despliegues. Su activación requiere la cuenta propietaria.

## Separación de responsabilidades

- GitHub Actions conserva **Calidad** (formato, lint, tipos, contratos y pruebas) y
  **validate-branch**. Las pruebas pequeñas de automatización se incluyen en Calidad,
  sin checks adicionales de backport en PRs normales. Jenkins y cobertura no se modifican.
- El CD existente está definido aparte en `terraform/modules/cicd` y `deploy/codebuild`:
  tags de servicio → CodePipeline → build/pruebas/imagen ECR → aprobación cuando esté
  configurada → migración y rollout. Se conserva este mecanismo; no se añade un segundo
  publicador de infraestructura ni un job de despliegue omitido en cada PR de GitHub.
- La revisión del código de infraestructura no certifica el estado de los pipelines en
  AWS. No se ejecutó Terraform ni se consultaron/modificaron recursos, roles o permisos.

## Backport automático de releases

Solo PRs `release/*` del mismo repositorio integrados en main, con aprobación humana
vigente y estos checks exitosos del head aprobado:

- Calidad: App 15368.
- validate-branch: App 15368.
- CI / Validación (Jenkins): App 5278678.

La App crea `backport/release-pr-<número>` con el merge SHA exacto y asigna JUANES545,
conservando otros asignados. Antes de integrar valida autor, identidad numérica real del bot mediante
Users API, repositorio, base/head actuales, rama y ancestry. Solo se omiten pruebas del
PR después de validar evidencia desde la base confiable; cualquier rechazo vuelve a CI
normal. No solicita Jenkins ni otra aprobación para el backport exacto.

Develop debe ser ancestro de la entrega. Divergencia, conflicto, evidencia obsoleta o
commits adicionales detienen el flujo. No se sobrescriben ramas ni se reabren/duplican PRs
cerrados. La consulta de mergeabilidad transitoria tiene reintentos limitados.

El merge exacto usa `[skip ci]` para no repetir el CI del push; no crea tags de servicio
ni altera los disparadores de CodePipeline. Los hotfixes conservan el workflow manual
existente, incluso al activar el automático de releases. No se amplía el bypass a hotfixes.

La API fija el head SHA, no el base: se relee develop y se compara el árbol integrado,
pero una carrera con otro merge puede requerir revisión posterior. Nunca se revierte
automáticamente una integración completada.

## Pendiente de Migue: configuración administrativa

1. Revisar/integrar este PR y promover el código a main mediante una release aprobada.
   Mantener el flag desactivado hasta tener la política confiable en develop y main.
2. Crear una App dedicada, sin webhook, instalada solo en backend-solventa. Permisos:
   Contents write, Pull requests write, Checks read, Metadata read. No reutilizar la App
   de Jenkins ni conceder Administration. Workflows write solo ante una necesidad real
   comprobada y autorizada.
3. Guardar la clave privadamente como `BACKPORT_APP_PRIVATE_KEY`; configurar
   `BACKPORT_APP_ID`, `BACKPORT_APP_SLUG` y `BACKPORT_AUTOMATION_ENABLED=false`.
   El PAT opcional del flujo legado no se migra ni se utiliza por el flujo nuevo.
4. Respaldar el ruleset 24763930 completo. Actualmente protege main y develop juntos.
   Antes de retirar develop de esa regla, crear dos rulesets activos solo para develop:
   - Integridad sin bypass: PR obligatorio, conversaciones resueltas, sin force-push/borrado.
   - Aprobación/CI: una aprobación, descartar obsoletas, checks estrictos actuales y mismos
     IDs de Apps. Único bypass: App dedicada, Integration, modo pull_request.
5. Verificar equivalencia antes de excluir develop del ruleset original. Main conserva
   exactamente su protección original; nunca conceder allí la excepción ni bypass de roles.
6. Resolver la divergencia actual con revisión o una release que incluya develop. Activar
   el flag y observar una release real: PR de App, evidencia, integración, árbol y ausencia
   de un despliegue duplicado. No fabricar una release o tag de servicio para probarlo.

La excepción de GitHub se concede por App, no por nombre de rama. El workflow confiable
de main limita su uso a sincronizaciones exactas; proteger la clave y revisar esa política.
El token se crea para el App ID y su clave; el slug autenticado debe coincidir con el
configurado. Users API confirma login, tipo Bot e ID del autor. No se depende de
`performed_via_github_app`, ausente en PRs y potencialmente nulo en Issues API.

## Recuperación

Reejecutar **Sincronizar release con develop** en main con el número `source_pr`. Si hay
divergencia, continuar mediante PR manual revisado. `BACKPORT_AUTOMATION_ENABLED=false`
restablece la creación manual previa para futuras releases; no toca PRs existentes ni AWS.
