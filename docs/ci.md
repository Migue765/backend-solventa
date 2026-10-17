# Comprobaciones del backend

## Resultados visibles en GitHub

- **Cobertura** publica una tabla de líneas/ramas por servicio y scripts, con conteos JUnit
  actuales y sin un promedio que esconda diferencias. Su enlace abre Checks en GitHub.
- **CI / Validación (Jenkins)** es el check principal obligatorio, publicado por el
  controlador. El Jenkinsfile no publica un alias duplicado.
- El workflow del PR define **CI · Actions**; GitHub puede mostrar todavía el nombre de la
  rama predeterminada hasta integrarlo. Se conservan los jobs `Calidad` y `validate-branch`.
  Las ejecuciones obsoletas de calidad se cancelan al llegar nuevos commits al mismo PR.
- JUnit queda disponible en Jenkins, sin el check redundante `Tests / Declarative: Post Actions`.
  El workspace se limpia antes del checkout; el resumen solo cuenta los XML esperados.
- Un reporte ausente o inválido se identifica como parcial, no como 0 %. Hace fallar un
  pipeline que iba bien; si ya falló otra etapa, el check parcial queda neutral.
  Una cobertura generada no certifica que las pruebas o el resto del pipeline hayan pasado.

`python -m pytest tests/test_coverage_summary.py` valida el publicador sin red ni credenciales.
Los porcentajes salen de los XML de esa ejecución, no de una línea base escrita a mano.

### Configuración administrativa verificada

**Operación 2026-10-17:** la App se renombró a **Solventa CI Backend** (`solventa-ci-backend`),
conservando ID `5278678`, instalación `170401410` y credencial `solventa-backend-github-app`.
El ruleset activo `24763930` sigue protegiendo `develop` y `main` con:

- `Calidad` y `validate-branch` de Actions (`15368`).
- `CI / Validación (Jenkins)` de la App `5278678`.
- Una aprobación, descarte de aprobaciones obsoletas, conversaciones resueltas y actualización estricta.
- Sin actores de bypass, force-push ni borrado de ramas protegidas.

Se añadió el requisito nuevo antes de cambiar el publicador del controlador y retirar el
anterior. El resultado global se publica automáticamente para todas las ramas del job,
incluidas las que aún usan un Jenkinsfile antiguo. No se reinició Jenkins por este cambio.
`Cobertura` sigue informativa: no se impone un mínimo ni un check que ramas antiguas no generan.

Se confirmó un caso real en PR26 #3: Jenkins agrupó solicitudes 31bad12/4cb9782 y compiló
la primera. El generador confiable ahora incorpora la política que difiere comentarios
mientras ese PR está en cola o ejecutándose, sin consumirlos. Tests:
`python -m pytest tests/test_jenkins_controller.py`. No atribuir el verde antiguo al SHA nuevo.
La corrección ya se desplegó y recargó de forma controlada, sin duplicar timers. El generador
conserva el hash de la plantilla verificada y la preparación inicialmente deshabilitada.

`skipNotifications=true` suprime `continuous-integration/jenkins/pr-merge`; JUnit se conserva
en Jenkins sin publicar un check adicional. El pipeline publica **Cobertura** y el controlador
publica el resultado global. Los nombres obligatorios de Actions no cambian para no dejar
ramas antiguas esperando jobs que no existen; cualquier migración futura debe ser coordinada.

Integrar este PR no cambia automáticamente el controlador, la App ni las protecciones.

| Proceso | Cuándo | Qué comprueba |
| --- | --- | --- |
| Actions: `Calidad` | Cada PR y push a develop/main | Formato, lint, tipos, OpenAPI y pruebas |
| Actions: `validate-branch` | PR a develop/main | Origen y destino según GitFlow |
| Jenkins: `CI / Validación (Jenkins)` | Comentario nuevo `/test-jenkins` de un colaborador | Mismas validaciones, JUnit y cobertura |

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
