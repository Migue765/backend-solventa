"""Summarize backend CI using step outcomes and current JUnit reports per module."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from scripts.coverage_summary import junit_counts


def summarize(steps: dict[str, Any], root: Path, revision: str, run_url: str) -> str:
    lines = ["## Validación del backend", ""]
    reused = steps.get("policy", {}).get("outputs", {}).get("skip_tests") == "true"
    if reused:
        lines += ["♻️ **Evidencia de la release verificada.** No se repiten las pruebas.", ""]
    else:
        checks = [
            ("automation", "Automatización"),
            ("dependencies", "Dependencias"),
            ("format", "Formato"),
            ("lint", "Lint"),
            ("types", "Tipos"),
            ("contracts", "Contratos OpenAPI"),
            ("tests", "Pruebas"),
            ("reports", "Informes disponibles"),
        ]
        states = {
            "success": "✅ Correcto",
            "failure": "❌ Falló",
            "skipped": "⏭️ Omitido",
            "cancelled": "⏹️ Cancelado",
        }
        lines += ["| Comprobación | Resultado |", "| --- | --- |"]
        for step, label in checks:
            state = states.get(steps.get(step, {}).get("outcome"), "⚪ Sin resultado")
            lines.append(f"| {label} | {state} |")
        modules = [("Scripts", "root")]
        for project in sorted(root.glob("services/*/pyproject.toml")):
            if any((project.parent / "tests").rglob("test_*.py")):
                modules.append((project.parent.name, project.parent.name))
        lines += [
            "",
            "### Pruebas por módulo",
            "",
            "| Módulo | ✅ Correctas | ❌ Fallidas | ⏭️ Omitidas | Total | Informe |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for label, report in modules:
            passed, failed, skipped, problems = junit_counts([str(root / f"reports/{report}.xml")])
            if problems:
                lines.append(f"| {label} | — | — | — | — | ⚠️ Ausente o incompleto |")
            else:
                lines.append(
                    f"| {label} | {passed} | {failed} | {skipped} | "
                    f"{passed + failed + skipped} | ✅ Disponible |"
                )
    revision_label = "Revisión de la política" if reused else "Revisión validada"
    lines += [
        "",
        f"[Ver ejecución e informes]({run_url})",
        "",
        f"**{revision_label}:** `{revision}`.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    steps = json.loads(os.environ["CI_STEPS"])
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    run_url = (
        f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
        f"/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    )
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
        output.write(summarize(steps, Path("."), revision, run_url))


if __name__ == "__main__":
    main()
