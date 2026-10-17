from pathlib import Path

from scripts.coverage_summary import counters, summarize


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
    assert counters(report) == ("75.00% (3/4)", "50.00% (1/2)")
    summary, complete = summarize([f"Servicio={report}"], [str(junit), str(junit)], "pytest")
    assert complete
    assert "1 correctas, 1 fallidas, 1 omitidas (3 casos JUnit)" in summary


def test_missing_and_malformed_reports_are_partial(tmp_path: Path) -> None:
    path = tmp_path / "bad.xml"
    for text in (None, "<broken", '<coverage lines-covered="5" lines-valid="4"/>'):
        if text is not None:
            path.write_text(text)
        summary, complete = summarize([f"Servicio={path}"], [str(path)], "pytest")
        assert not complete
        assert "Reporte parcial" in summary
