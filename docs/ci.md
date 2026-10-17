# Comprobaciones del backend

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
