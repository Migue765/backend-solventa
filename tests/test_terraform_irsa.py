import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "terraform" / "modules" / "iam-irsa"


def test_runtime_irsa_removes_master_without_replacement_db_grants() -> None:
    config = (ROOT / "terraform" / "environments" / "prod" / "primary" / "main.tf").read_text()
    cotizacion = re.search(r'^module "iam_irsa" \{\n(.*?)^\}', config, re.M | re.S)
    identidad = re.search(r'^module "iam_irsa_identidad" \{\n(.*?)^\}', config, re.M | re.S)
    assert cotizacion is not None and identidad is not None
    assert not re.search(r"\bdb_secret_arn\s*=", cotizacion[1])
    assert re.search(
        r"secret_arns\s*=\s*\[module.redis.secret_arn,\s*module.jwt_key.secret_arn\]",
        identidad[1],
    )
    assert "master_user_secret_arn" not in cotizacion[1] + identidad[1]


def test_optional_db_secret_preserves_other_permissions_offline(tmp_path: Path) -> None:
    terraform = os.getenv("SOLVENTA_TEST_TERRAFORM_BIN") or shutil.which("terraform")
    mirror = os.getenv("SOLVENTA_TEST_TERRAFORM_MIRROR")
    if not terraform or not mirror:
        pytest.skip("Requiere Terraform existente y mirror filesystem offline explicitos.")
    assert Path(mirror).is_dir()
    policy = re.search(
        r'^data "aws_iam_policy_document" "service" \{\n.*?^\}',
        (MODULE / "main.tf").read_text(),
        re.M | re.S,
    )
    variable = re.search(
        r'^variable "db_secret_arn" \{\n.*?^\}',
        (MODULE / "variables.tf").read_text(),
        re.M | re.S,
    )
    assert policy is not None and variable is not None
    # Only the real provider's local policy renderer: no IAM resources or AWS data reads.
    config = """
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 5.100.0"
    }
  }
}
provider "aws" {
  region                      = "us-east-2"
  access_key                  = "local-policy-only"
  secret_key                  = "local-policy-only"
  skip_credentials_validation = true
  skip_region_validation      = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  shared_config_files         = []
  shared_credentials_files    = []
}
"""
    config += variable[0] + "\n" + policy[0] + "\n"
    for name in ("evidence_bucket_arn", "sns_topic_arn", "kms_key_arn"):
        config += f'variable "{name}" {{ type = string }}\n'
    (tmp_path / "main.tf").write_text(config)
    (tmp_path / "policy.tftest.hcl").write_text("""
variables {
  evidence_bucket_arn = "arn:aws:s3:::synthetic-evidence"
  sns_topic_arn       = "arn:aws:sns:us-east-2:000000000000:synthetic-events"
  kms_key_arn         = "arn:aws:kms:us-east-2:000000000000:key/synthetic-only"
}
run "default_without_db_access" {
  command = plan
  assert {
    condition = toset([
      for statement in jsondecode(data.aws_iam_policy_document.service.json).Statement :
      statement.Sid
    ]) == toset(["WriteEvidence", "ListEvidenceBucket", "PublishDecisionEvents", "UseCmk"])
    error_message = "El default debe quitar solo ReadDbSecret."
  }
}
run "explicit_legacy_secret_compatible" {
  command = plan
  variables {
    db_secret_arn = "arn:aws:secretsmanager:us-east-2:000000000000:secret:compat-only-abcdef"
  }
  assert {
    condition = contains(flatten([
      for statement in jsondecode(data.aws_iam_policy_document.service.json).Statement :
      statement.Resource if statement.Sid == "ReadDbSecret"
    ]), var.db_secret_arn)
    error_message = "Un consumidor explicito debe conservar el ARN solicitado."
  }
  assert {
    condition = length(jsondecode(data.aws_iam_policy_document.service.json).Statement) == 5
    error_message = "La compatibilidad no debe eliminar otros permisos."
  }
}
""")
    mirror_config = tmp_path / "offline.tfrc"
    mirror_config.write_text(
        "provider_installation {\n  filesystem_mirror {\n"
        f"    path = {json.dumps(mirror)}\n  }}\n}}\n"
    )
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("AWS_", "TF_"))
    }
    environment.update(
        TF_CLI_CONFIG_FILE=str(mirror_config),
        TF_DATA_DIR=str(tmp_path / "terraform-data"),
        AWS_EC2_METADATA_DISABLED="true",
        TF_IN_AUTOMATION="true",
        CHECKPOINT_DISABLE="1",
    )
    for arguments in (
        ("init", "-backend=false", "-input=false", "-no-color"),
        ("validate", "-no-color"),
        ("test", "-no-color"),
    ):
        result = subprocess.run(
            [terraform, f"-chdir={tmp_path}", *arguments],
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed, 0 failed." in result.stdout
