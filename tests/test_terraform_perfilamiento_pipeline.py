import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_perfilamiento_pipeline_uses_dedicated_release_resources() -> None:
    config = (ROOT / "terraform/environments/prod/primary/perfilamiento.tf").read_text()
    ecr = config.split('module "ecr_perfilamiento" {', maxsplit=1)[1].split("\n}", maxsplit=1)[0]
    pipeline = config.split('module "cicd_perfilamiento" {', maxsplit=1)[1].split(
        "\n}", maxsplit=1
    )[0]

    assert re.search(r'^\s*repository_name\s*=\s*"solventa/perfilamiento"$', ecr, re.M)
    assert re.search(r'^\s*service_name\s*=\s*"perfilamiento"$', pipeline, re.M)
    assert re.search(r'^\s*service_dir\s*=\s*"services/perfilamiento"$', pipeline, re.M)
    assert re.search(r'^\s*service_namespace\s*=\s*"perfilamiento"$', pipeline, re.M)
    assert re.search(r'^\s*release_tag_patterns\s*=\s*\["perfilamiento-v\*"\]$', pipeline, re.M)
    assert re.search(r"^\s*run_migrations\s*=\s*true$", pipeline, re.M)
    assert re.search(
        r"^\s*automatic_triggers_enabled\s*=\s*var\.cicd_automatic_triggers_enabled$",
        pipeline,
        re.M,
    )
    assert re.search(
        r"^\s*github_connection_arn\s*=\s*module\.cicd\.connection_arn$", pipeline, re.M
    )
