import csv
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from identidad.domain.consent import ConsentScope  # type: ignore[import-untyped]

from tests.jmeter.local_stack import LocalStack
from tests.jmeter.plans import CONSENT, PRODUCTS, REQUESTS, main_plan, outage_plan, persistence_plan
from tests.jmeter.results import evaluate, percentile
from tests.test_encrypted_local_stack import call


def test_jmeter_workflow_is_manual_with_performance_disabled_by_default() -> None:
    path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "jmeter.yml"
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["performance"]["default"] == "false"


def write_jtl(path: Path, rows: list[tuple[str, str, int]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("label", "success", "elapsed"))
        writer.writerows(rows)
    return path


def journeys(count: int = 1, elapsed: int = 10) -> list[tuple[str, str, int]]:
    labels = [
        *(f"journey.quote.{product}" for product in PRODUCTS),
        *(f"journey.emission.{product}" for product in PRODUCTS),
        "journey.dashboard",
    ]
    return [(label, "true", elapsed) for label in labels for _ in range(count)]


def test_generated_plans_use_real_http_and_safe_assertions(tmp_path: Path) -> None:
    main = tmp_path / "main.jmx"
    main_plan(main)
    root = ET.parse(main).getroot()
    samplers = root.findall(".//HTTPSamplerProxy")
    assert len(samplers) > 80
    assert len(root.findall(".//JSR223Assertion")) == len(samplers)
    for group in root.findall(".//hashTree"):
        children = list(group)
        assert len(children) % 2 == 0
        assert all(child.tag == "hashTree" for child in children[1::2])
    assert len(root.findall(".//TransactionController")) == 16
    assert "getResponseDataAsString" in main.read_text()
    assert "failure.getMessage" not in main.read_text()
    recovery = tmp_path / "restart.jmx"
    persistence_plan(recovery)
    assert len(ET.parse(recovery).findall(".//HTTPSamplerProxy")) == 3


def test_generated_consents_have_current_scope_and_runtime_expiry(tmp_path: Path) -> None:
    path = tmp_path / "consents.jmx"
    main_plan(path)
    root = ET.parse(path).getroot()
    samplers = [
        sampler
        for sampler in root.findall(".//HTTPSamplerProxy")
        if sampler.findtext("stringProp[@name='HTTPSampler.path']") == CONSENT
        and sampler.findtext("stringProp[@name='HTTPSampler.method']") == "PUT"
    ]
    assert len(samplers) == 4
    for sampler in samplers:
        raw = sampler.findtext(".//stringProp[@name='Argument.value']")
        assert raw is not None
        body = json.loads(raw)
        if body["autorizado"]:
            scope = body["solicitud"]
            assert scope["vigenteHasta"] == "${consentValidUntil}"
            assert set(scope) == {
                "finalidad",
                "alcances",
                "fuentes",
                "datos",
                "versionTerminos",
                "vigenteHasta",
            }
            assert scope["finalidad"] == "cotizacion"
            assert scope["alcances"] == scope["fuentes"] == ["open_finance", "open_data"]
            assert scope["datos"] == ["indicador_riesgo"]
            ConsentScope.model_validate(
                {
                    **scope,
                    "vigenteHasta": (datetime.now(UTC) + timedelta(days=29)).isoformat(),
                }
            )
        else:
            assert body == {"autorizado": False, "version": 1}
    scripts = [node.text or "" for node in root.findall(".//stringProp[@name='script']")]
    assert any(
        "vars.put('consentValidUntil', java.time.Instant.now().plusSeconds(2505600).toString())"
        in script
        for script in scripts
    )


@pytest.mark.parametrize("phase", ["main", "outage"])
def test_generated_customer_reaches_consent_boundary_over_http(tmp_path: Path, phase: str) -> None:
    path = tmp_path / f"{phase}.jmx"
    (main_plan if phase == "main" else outage_plan)(path)
    sampler = ET.parse(path).find(".//HTTPSamplerProxy")
    assert sampler is not None
    raw = sampler.findtext(".//stringProp[@name='Argument.value']")
    assert raw is not None
    body = json.loads(
        raw.replace("${email}", f"jmeter-{uuid4().hex}@example.com").replace(
            "${document}", "JM00000000000001"
        )
    )
    port = sampler.findtext("stringProp[@name='HTTPSampler.port']")
    service = {"${__P(webPort)}": "web", "${__P(identityPort)}": "identity"}[str(port)]
    with LocalStack(tmp_path / "stack") as stack:
        status, registered = call(stack.urls[service] + "/v1/usuarios", body)
        assert status == 201
        status, session = call(
            stack.urls["web"] + "/v1/sesiones",
            {"correo": body["correo"], "contrasena": body["contrasena"]},
        )
        assert status == 200
        status, problem = call(
            stack.urls["web"] + REQUESTS,
            {"productoId": "automotive", "datosProducto": {"vehiclePlate": "ABC123"}},
            {
                "Authorization": "Bearer " + session["accessToken"],
                "Idempotency-Key": str(uuid4()),
            },
        )
        assert (status, problem.get("code")) == (403, "consentimiento_ausente")
        for field in ("tipoDocumento", "numeroDocumento", "telefono", "fechaNacimiento"):
            assert registered["usuario"][field] == body[field]


def test_missing_or_empty_results_fail(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        evaluate(tmp_path / "absent", 1)
    with pytest.raises(ValueError, match="Missing"):
        evaluate(write_jtl(tmp_path / "empty", []), 1)


def test_truncated_run_and_failed_assertion_are_not_success(tmp_path: Path) -> None:
    path = tmp_path / "run.jtl"
    assert not evaluate(write_jtl(path, journeys()[:-1]), 1)["passed"]
    rows = [*journeys(), ("ownership.quote", "false", 1)]
    assert not evaluate(write_jtl(path, rows), 1)["passed"]


def test_latency_boundaries_and_sample_floor(tmp_path: Path) -> None:
    path = tmp_path / "run.jtl"
    assert evaluate(write_jtl(path, journeys(20, 150)), 20, True)["passed"]
    result = evaluate(write_jtl(path, journeys(20, 151)), 20, True)
    assert not result["passed"]
    errors = result["errors"]
    assert isinstance(errors, list)
    assert any("journey.dashboard" in error for error in errors)
    assert not evaluate(write_jtl(path, journeys()), 1, True)["passed"]
    assert evaluate(write_jtl(path, journeys(1, 5000)), 1, False)["passed"]


def test_nearest_rank_percentiles_and_counts(tmp_path: Path) -> None:
    assert percentile(list(range(1, 101)), 0.95) == 95
    assert percentile(list(range(1, 101)), 0.99) == 99
    with pytest.raises(ValueError):
        percentile([], 0.95)
    result = evaluate(write_jtl(tmp_path / "run.jtl", journeys()), 1)
    assert result["quote_journeys_offered"] == 8
    assert result["quote_journeys_completed"] == 8


def test_mortgage_capacity_uses_bounded_concurrency(tmp_path: Path) -> None:
    path = write_jtl(
        tmp_path / "capacity.jtl",
        [("journey.quote.mortgage-life", "true", 400)] * 20,
    )

    passing = evaluate(path, 0, True, capacity_concurrency=3)
    failing = evaluate(path, 0, True, capacity_concurrency=2)

    assert passing["mortgage_capacity_operations_per_hour"] == 27_000
    assert passing["passed"]
    assert failing["mortgage_capacity_operations_per_hour"] == 18_000
    assert not failing["passed"]


@pytest.mark.parametrize(
    ("label", "p95", "p99"),
    [
        ("journey.quote.life", 250, 500),
        ("journey.quote.mortgage-life", 400, 800),
        ("journey.emission.life", 1500, 3000),
        ("journey.dashboard", 150, 300),
    ],
)
def test_each_budget_is_enforced(tmp_path: Path, label: str, p95: int, p99: int) -> None:
    path = tmp_path / "latency.jtl"
    rows = [(label, "true", p95)] * 19 + [(label, "true", p99)]
    assert evaluate(write_jtl(path, rows), 0, True)["passed"]
    rows[-1] = (label, "true", p99 + 1)
    assert not evaluate(write_jtl(path, rows), 0, True)["passed"]
    rows = [(label, "true", p95 + 1)] * 20
    assert not evaluate(write_jtl(path, rows), 0, True)["passed"]


def test_recovery_cannot_pass_on_initialization_alone(tmp_path: Path) -> None:
    path = write_jtl(tmp_path / "recovery.jtl", [("initialize", "true", 1)])
    assert not evaluate(path, 0, required_labels=("recovery.retry.0",))["passed"]
