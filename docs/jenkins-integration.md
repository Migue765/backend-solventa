# Jenkins backend — preparación

## Alcance

El pipeline reproduce la calidad de Actions: formato/lint Ruff, tipos mypy, contratos OpenAPI y pruebas raíz y de los servicios implementados. Python 3.12 y dependencias por proyecto en un virtualenv de workspace. Los servicios vacíos no se presentan como implementados ni probados. Reportes JUnit y limpieza al finalizar.

No cambia Actions, aplicaciones, contratos, credenciales o protecciones; no despliega servicios ni valida PostgreSQL si las pruebas existentes usan SQLite. Las dependencias de herramientas están fijadas en requirements-dev.txt; las de servicios tienen rangos, no un lockfile completo.

## Configuración privada pendiente

Migue debe autorizar e instalar una App Jenkins con acceso seleccionado únicamente a Migue765/backend-solventa: Contents/Metadata/Pull requests read, Checks/Commit statuses write. No usar PEM web/Android/backport ni guardar claves en Git.

- Carpeta: `solventa-backend`, store exclusivo de carpeta.
- Credencial propuesta: `solventa-backend-github-app`, tipo GitHub App, PEM PKCS#8. App ID/slug/Installation ID pendientes de Migue; no copiar IDs de otros proyectos.
- Multibranch: `solventa-backend/pr-checks`, origin PRs hacia **develop/main**, no master ni forks.
- Check: `Solventa Backend Jenkins`, publicado por la App backend.
- Agente compartido actual, un ejecutor total, label `backend-ci` solo después de provisionar Python 3.12 sin cambiar Java/Node/Android.
- Adaptar lista aprobada del shared lifecycle y reconexión para incluir backend; hoy solo contempla Android y web. Verificar cola anidada y protección de trabajo ocupado antes de habilitar solicitudes.
- Scanner confiable separado, ledger/config propios, sin builds por indexing/events. Exacto `/test-jenkins`, comentario humano nuevo sin editar con WRITE/MAINTAIN/ADMIN, head SHA comprobado y procesamiento por ID.
- No instalar/activar jobs, iniciar pruebas ni hacer el check obligatorio hasta verificar App, herramientas y lifecycle. Sin webhook público ni capacidad adicional sin aprobación.

## Prueba y protecciones

Primero validar un PR real: install, calidad, contratos, tests, JUnit, check asociado a App, arranque/reconexión automática e idle stop. Después Migue podrá exigir el check backend junto con los checks existentes y una aprobación en develop/main. Juan tiene WRITE, no ADMIN; no conoce el detalle de protección sin consulta del dueño.

GitFlow y backport ya existen en este repositorio: revisar su implementación antes de adaptar automatizaciones. No reemplazar workflows ni introducir bypass por nombre de rama.
