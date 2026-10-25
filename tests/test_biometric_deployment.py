from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def test_manifests_keep_biometrics_off_until_operator_enables_them() -> None:
    for service in ("identidad", "bff-movil"):
        manifest = _read(f"deploy/k8s/{service}/deployment.yaml")
        assert 'name: BIOMETRIC_LOGIN_ENABLED\n              value: "false"' in manifest
    assert "BIOMETRIC_LOGIN_ENABLED" not in _read("deploy/k8s/bff-web/deployment.yaml")


def test_identidad_gets_redis_from_secret_and_issuer_matches_bff() -> None:
    identidad = _read("deploy/k8s/identidad/deployment.yaml")
    assert "name: REDIS_URL\n              valueFrom:\n                secretKeyRef:" in identidad
    assert "value: ${JWT_ISSUER}" in identidad
    assert "value: ${JWT_ISSUER}" in _read("deploy/k8s/bff-movil/deployment.yaml")


def test_deploy_runs_migration_before_image_and_does_not_flip_flags() -> None:
    deploy = _read("deploy/codebuild/deploy.sh")
    assert "BIOMETRIC_LOGIN_ENABLED" not in deploy
    assert deploy.index("migrate-job.yaml") < deploy.index("set image")
    assert '"alembic", "upgrade", "head"' in _read("deploy/k8s/identidad/migrate-job.yaml")
    assert "0006" in _read("services/identidad/migrations/versions/0006_acceso_biometrico.py")


def test_runbook_orders_routes_migration_redis_then_flags_with_rollback() -> None:
    doc = _read("docs/acceso-biometrico.md")
    section = doc.split("## Habilitacion en el entorno real")[1].split("## Evidencia")[0]
    assert "-target" not in section.replace("sin `-target`", "")
    steps = ["terraform plan", "alembic current", "redis.Redis.from_url", "ENABLED=true"]
    positions = [section.index(step) for step in steps]
    assert positions == sorted(positions)
    off = section.split("**Desactivar")[1]
    assert off.index("-n bff-movil") < off.index("Identidad, esperando")
    enable = section.split("**Activar.**")[1]
    assert enable.index("-n identidad set env") < enable.index("-n bff-movil")
    assert "MIGRATE=true" in section and "MIGRATE=false" in section
