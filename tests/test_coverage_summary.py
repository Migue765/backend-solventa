from pathlib import Path

from scripts.coverage_summary import counters, junit_counts, summarize


def test_reports_and_current_junit_only(tmp_path: Path) -> None:
    report = tmp_path / "coverage.xml"
    report.write_text(
        '<coverage lines-covered="3" lines-valid="4" branches-covered="1" branches-valid="2"/>'
    )
    junit = tmp_path / "current.xml"
    junit.write_text(
        "<testsuite><testcase/><testcase><failure/></testcase>"
        "<testcase><skipped/></testcase></testsuite>"
    )
    (tmp_path / "old.xml").write_text("<testsuite><testcase/></testsuite>")
    assert counters(report) == ((3, 4), (1, 2))
    summary, complete = summarize([f"Servicio={report}"], [str(junit), str(junit)], "pytest")
    assert complete
    assert "| 1 | 1 | 1 | 3 |" in summary


def test_missing_and_malformed_reports_are_partial(tmp_path: Path) -> None:
    path = tmp_path / "bad.xml"
    for text in (None, "<broken", '<coverage lines-covered="5" lines-valid="4"/>'):
        if text is not None:
            path.write_text(text)
        summary, complete = summarize([f"Servicio={path}"], [str(path)], "pytest")
        assert not complete
        assert "Reporte parcial" in summary


def test_target_is_informative_and_not_decided_by_rounding(tmp_path: Path) -> None:
    junit = tmp_path / "tests.xml"
    junit.write_text("<testsuite><testcase/></testsuite>")
    report = tmp_path / "coverage.xml"
    for covered, total, expected in (
        (3, 4, "🟠 A 5.00 pp"),
        (4, 5, "🟢 Objetivo alcanzado"),
        (79999, 100000, "🟠 A < 0.01 pp"),
    ):
        report.write_text(
            f'<coverage lines-covered="{covered}" lines-valid="{total}" '
            'branches-covered="0" branches-valid="0"/>'
        )
        summary, complete = summarize([f"Servicio={report}"], [str(junit)], "pytest")
        assert complete
        assert "Objetivo recomendado: 80 %" in summary
        assert expected in summary
        assert "⚪ No aplica" in summary
        assert "Sin umbral" not in summary


def test_modules_are_measured_independently_without_average(tmp_path: Path) -> None:
    junit = tmp_path / "tests.xml"
    junit.write_text("<testsuite><testcase/></testsuite>")
    sources = []
    for label, covered in (("Sesiones", 1), ("Usuarios", 4)):
        report = tmp_path / f"{label}.xml"
        report.write_text(
            f'<coverage lines-covered="{covered}" lines-valid="4" '
            'branches-covered="0" branches-valid="0"/>'
        )
        sources.append(f"{label}={report}")
    summary, complete = summarize(sources, [str(junit)], "pytest")
    assert complete
    assert "| Sesiones | Líneas | **25.00%** | 1 / 4 | 3 | 🟠" in summary
    assert "| Usuarios | Líneas | **100.00%** | 4 / 4 | 0 | 🟢" in summary
    assert "62.50%" not in summary


def test_error_has_priority_over_skipped(tmp_path: Path) -> None:
    junit = tmp_path / "tests.xml"
    junit.write_text("<testsuite><testcase><error/><skipped/></testcase></testsuite>")
    assert junit_counts([str(junit)]) == (0, 1, 0, [])
