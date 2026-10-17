# Comprobaciones del backend

## Resultados visibles en GitHub

- **Cobertura** publica una tabla de líneas/ramas por servicio y scripts, con conteos JUnit
  actuales y sin un promedio que esconda diferencias. Su enlace abre Checks en GitHub.
- **CI / Validación (Jenkins)** es el nuevo nombre de presentación; se conserva
  **Solventa Backend Jenkins** mientras siga siendo el contexto obligatorio.
- Actions se agrupa como **CI · Actions**, conservando `Calidad`. Las ejecuciones obsoletas
  de ese workflow se cancelan al llegar nuevos commits al mismo PR.
- JUnit queda disponible en Jenkins, sin el check redundante `Tests / Declarative: Post Actions`.
  El workspace se limpia antes del checkout; el resumen solo cuenta los XML esperados.
- Un reporte ausente o inválido se identifica como parcial, no como 0 %. Hace fallar un
  pipeline que iba bien; si ya falló otra etapa, el check parcial queda neutral.
  Una cobertura generada no certifica que las pruebas o el resto del pipeline hayan pasado.

`python -m pytest tests/test_coverage_summary.py` valida el publicador sin red ni credenciales.
Los porcentajes salen de los XML de esa ejecución, no de una línea base escrita a mano.

### Migración pendiente con Migue

Se confirmó un caso real en PR26 #3: Jenkins agrupó solicitudes 31bad12/4cb9782 y compiló
la primera. El generador confiable ahora incorpora la política que difiere comentarios
mientras ese PR está en cola o ejecutándose, sin consumirlos. Tests:
`python -m pytest tests/test_jenkins_controller.py`. No atribuir el verde antiguo al SHA nuevo.
La corrección requiere despliegue y recarga administrativa controlada del scanner, sin
duplicar timers. El generador conserva sus verificaciones de plantilla y activación deshabilitada.

1. Verificar `CI / Validación (Jenkins)` en el mismo SHA y publicado por la App `5278678`.
2. Migue debe sustituir el contexto antiguo en el ruleset `24763930`, conservando la App
   esperada, `Calidad`, `validate-branch`, revisión y demás restricciones. No desactivar el ruleset.
3. Con la preparación integrada y la protección nueva verificada, retirar el check automático
   antiguo del controlador; el publicador explícito del Jenkinsfile conserva el nuevo.
4. `infra/jenkins/configure-reporting.groovy`, ejecutado por un administrador en el controlador,
   quita solo `continuous-integration/jenkins/pr-merge` después de comprobar que no sea requerido.
   No ejecutar desde código del PR ni reiniciar Jenkins.
5. Migue puede renombrar su App visible a **Solventa CI Backend**, conservando ID, instalación
   y credenciales. Después migrar `Calidad` a `CI / Calidad (Actions)` y `validate-branch`
   a `Git / Flujo de ramas` de forma coordinada con el ruleset.

Integrar este PR no cambia automáticamente el controlador, la App ni las protecciones.

| Proceso | Cuándo | Qué comprueba |
| --- | --- | --- |
| Actions: `Calidad` | Cada PR y push a develop/main | Formato, lint, tipos, OpenAPI y pruebas |
| Actions: `validate-branch` | PR a develop/main | Origen y destino según GitFlow |
| Jenkins: `Solventa Backend Jenkins` | Comentario nuevo `/test-jenkins` de un colaborador | Mismas validaciones, JUnit y cobertura |

Después de nuevos commits, solicitar Jenkins otra vez. La revisión humana es independiente
de los checks; no se deben omitir para integrar un PR normal. Una publicación/despliegue
es un proceso separado: compilar o pasar pruebas no demuestra que un servicio esté en AWS.

## Leer Jenkins

Etapas: Código → Dependencias → Formato y lint → Tipos → Contratos OpenAPI → Pruebas y cobertura.
La etapa roja identifica el problema. Un fallo de una prueba conserva el resultado fallido;
los reportes disponibles se archivan incluso cuando el pipeline no termina correctamente.

- **Tests**: reporte JUnit de raíz y de cada servicio implementado.
- **Artifacts → reports/coverage/**: XML Cobertura y HTML por servicio; descargar el
  directorio HTML completo y abrir su `index.html` si Jenkins restringe su visualización.
- La consola muestra líneas faltantes y cobertura por ejecución. No hay porcentaje mínimo
  nuevo ni promedio entre servicios que oculte un módulo con poca cobertura.
- Raíz mide `scripts`; cada servicio mide su `src`, con pruebas unitarias e integración
  existentes. Un servicio vacío no se presenta como probado. Cobertura no mide calidad
  de asserts, disponibilidad de AWS ni cumplimiento funcional completo.

El agente compartido tiene un ejecutor: web/Android/backend pueden esperar en cola.
No enviar otro comentario si ya existe una ejecución para el mismo commit.
