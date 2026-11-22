import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("shared_mailbox", [False, True])
def test_academic_ses_policy_is_restricted_to_identity_and_verified_admin(
    tmp_path: Path, shared_mailbox: bool
) -> None:
    terraform = os.getenv("SOLVENTA_TEST_TERRAFORM_BIN") or shutil.which("terraform")
    if not terraform:
        pytest.skip("Requiere Terraform existente; no instala herramientas ni consulta AWS.")
    sender = "sender@example.com"
    admin = sender if shared_mailbox else "admin@example.com"
    role = "arn:aws:iam::000000000000:role/synthetic-identidad"
    sender_arn = "arn:aws:ses:us-east-2:000000000000:identity/" + sender
    admin_arn = "arn:aws:ses:us-east-2:000000000000:identity/" + admin
    parameters = {
        "identity_role_arn": role,
        "ses_region": "us-east-2",
        "sender_identity_arn": sender_arn,
        "admin_identity_arn": admin_arn,
        "sender": sender,
        "admin_mailbox": admin,
    }
    template = ROOT / "deploy" / "k8s" / "identidad" / "recovery-ses-policy.json.tftpl"
    expression = (
        f"jsonencode(jsondecode(templatefile({json.dumps(str(template))}, "
        f"jsondecode({json.dumps(json.dumps(parameters))}))))\n"
    )
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("AWS_", "TF_"))
    }
    environment.update(
        TF_DATA_DIR=str(tmp_path / "terraform-data"),
        TF_IN_AUTOMATION="true",
        CHECKPOINT_DISABLE="1",
        AWS_EC2_METADATA_DISABLED="true",
    )
    result = subprocess.run(
        [terraform, f"-chdir={tmp_path}", "console", "-no-color"],
        input=expression,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    policy = json.loads(json.loads(result.stdout.strip()))
    statements = {statement["Action"][0]: statement for statement in policy["Statement"]}
    assert set(statements) == {"ses:GetAccount", "ses:GetEmailIdentity", "ses:SendEmail"}
    for statement in statements.values():
        assert statement["Effect"] == "Allow"
        assert statement["Condition"]["ArnEquals"]["aws:PrincipalArn"] == role
        assert statement["Condition"]["StringEquals"]["aws:RequestedRegion"] == "us-east-2"
    assert statements["ses:GetAccount"]["Resource"] == ["*"]
    assert set(statements["ses:GetEmailIdentity"]["Resource"]) == {sender_arn, admin_arn}
    send = statements["ses:SendEmail"]
    assert send["Resource"] == [sender_arn]
    assert send["Condition"]["StringEquals"]["ses:FromAddress"] == sender
    assert send["Condition"]["ForAllValues:StringEquals"]["ses:Recipients"] == [admin]
    assert send["Condition"]["Null"]["ses:Recipients"] == "false"


def test_recovery_environment_is_optional_without_enabling_or_mutating_existing_secrets() -> None:
    manifest = yaml.safe_load(
        (ROOT / "deploy" / "k8s" / "identidad" / "deployment.yaml").read_text(encoding="utf-8")
    )
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    variables = {entry["name"]: entry for entry in container["env"]}
    for name in (
        "RECOVERY_DELIVERY_MODE",
        "RECOVERY_ADMIN_MAILBOX",
        "RECOVERY_SES_REGION",
        "RECOVERY_EMAIL_SENDER",
        "RECOVERY_WEB_URL",
    ):
        assert variables[name]["valueFrom"]["secretKeyRef"] == {
            "name": "identidad-config",
            "key": name,
            "optional": True,
        }
    assert variables["ENCRYPTED_LOGIN_ENABLED"]["value"] == "false"
    assert variables["DATABASE_URL"]["valueFrom"]["secretKeyRef"]["key"] == "DATABASE_URL"
