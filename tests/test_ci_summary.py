from pathlib import Path

from scripts.ci_summary import summarize


def write(root: Path, filename: str, contents: str) -> None:
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def test_actual_steps_and_junit_are_split_per_module(tmp_path: Path) -> None:
    write(tmp_path, "reports/root.xml", '<testsuite tests="999"><testcase/></testsuite>')
    write(tmp_path, "services/usuarios/pyproject.toml", "")
    write(tmp_path, "services/usuarios/tests/test_user.py", "")
    write(
        tmp_path, "reports/usuarios.xml", "<testsuite><testcase><failure/></testcase></testsuite>"
    )
    write(tmp_path, "reports/old.xml", "<testsuite><testcase/><testcase/></testsuite>")
    result = summarize(
        {"tests": {"outcome": "failure"}},
        tmp_path,
        "a" * 40,
        "https://github.com/owner/repo/actions/runs/1",
    )
    assert "| Pruebas | ❌ Falló |" in result
    assert "| Scripts | 1 | 0 | 0 | 1 | ✅ Disponible |" in result
    assert "| usuarios | 0 | 1 | 0 | 1 | ✅ Disponible |" in result
    assert "a" * 40 in result


def test_missing_service_report_is_not_presented_as_zero_failures(tmp_path: Path) -> None:
    write(tmp_path, "services/usuarios/pyproject.toml", "")
    write(tmp_path, "services/usuarios/tests/test_user.py", "")
    result = summarize({}, tmp_path, "a" * 40, "https://github.com/owner/repo/actions/runs/1")
    assert "| usuarios | — | — | — | — | ⚠️ Ausente o incompleto |" in result
    assert "| usuarios | 0 | 0 | 0 | 0 |" not in result


def test_malformed_report_does_not_hide_step_failure(tmp_path: Path) -> None:
    write(tmp_path, "reports/root.xml", "<broken")
    result = summarize(
        {"tests": {"outcome": "failure"}},
        tmp_path,
        "a" * 40,
        "https://github.com/owner/repo/actions/runs/1",
    )
    assert "❌ Falló" in result
    assert "⚠️ Ausente o incompleto" in result


def test_backport_reuses_evidence_without_fake_test_counts(tmp_path: Path) -> None:
    result = summarize(
        {"policy": {"outputs": {"skip_tests": "true"}}},
        tmp_path,
        "a" * 40,
        "https://github.com/owner/repo/actions/runs/1",
    )
    assert "Evidencia de la release verificada" in result
    assert "Revisión de la política" in result
    assert "Pruebas por módulo" not in result
    assert "✅ Correcto" not in result
