"""Prepare backend-only trusted files from the verified web scanner; keep disabled."""

import hashlib
import json
import os
from pathlib import Path

ROOT = Path("/var/lib/jenkins")
DEST = Path("/opt/jenkins-backend")


def prepare_files(scanner: str, policy: str) -> tuple[str, str]:
    substitutions = {
        "JazminCorAndes": "Migue765",
        "solventa-web-pf2": "backend-solventa",
        "solventa-web-github-app": "solventa-backend-github-app",
        "solventa-web/pr-checks": "solventa-backend/pr-checks",
        "5277435": "5278678",
        "web-comment-trigger": "backend-comment-trigger",
        "/opt/jenkins-web/": "/opt/jenkins-backend/",
        "['develop', 'master']": "['develop', 'main']",
        "Web comment scan": "Backend comment scan",
        "Queued web PR": "Queued backend PR",
    }
    for old, new in substitutions.items():
        scanner = scanner.replace(old, new)
    guard = "if (!policy.canSchedule(job.isInQueue(), job.isBuilding())) return"
    if guard not in scanner:
        anchor = "def branch = project.getProjectFactory().getBranch(job)"
        if scanner.count(anchor) != 1:
            raise RuntimeError("Expected one scheduling point; inspect before adapting")
        scanner = scanner.replace(anchor, guard + "\n                " + anchor)
    if "static boolean canSchedule(" not in policy:
        policy = policy.replace(
            "class SolventaWebCommentPolicy {",
            "class SolventaWebCommentPolicy {\n"
            "    static boolean canSchedule(boolean queued, boolean building) {\n"
            "        !queued && !building\n    }\n",
        )
    policy = policy.replace("SolventaWebCommentPolicy", "SolventaBackendCommentPolicy")
    policy = policy.replace("['develop', 'master']", "['develop', 'main']")
    if "static boolean canSchedule(" not in policy:
        raise RuntimeError("Expected queue policy; inspect before adapting")
    return scanner, policy


def main() -> None:
    scanner = (ROOT / "init.groovy.d/09-web-comment-trigger.groovy").read_text()
    policy = Path("/opt/jenkins-web/comment-policy.groovy").read_text()
    expected = "812e1fe51c8ede5430694815ba7201c99cfcf94e15ff380dafd8d8d6c2e4e267"
    if hashlib.sha256(scanner.encode()).hexdigest() != expected:
        raise RuntimeError("Web scanner differs from verified template; inspect before adapting")
    scanner, policy = prepare_files(scanner, policy)
    config_path = ROOT / "backend-comment-trigger.json"
    config = {
        "enabled": False,
        "owner": "Migue765",
        "repository": "backend-solventa",
        "credentialId": "solventa-backend-github-app",
    }
    if config_path.exists() and json.loads(config_path.read_text()) != config:
        raise RuntimeError("Existing backend activation/configuration requires review")
    DEST.mkdir(exist_ok=True, mode=0o755)
    paths = {
        DEST / "comment-policy.groovy": policy,
        ROOT / "init.groovy.d/11-backend-comment-trigger.groovy": scanner,
        config_path: json.dumps(config),
    }
    for path, content in paths.items():
        path.write_text(content)
        os.chmod(path, 0o600)
        import pwd

        account = pwd.getpwnam("jenkins")
        os.chown(path, account.pw_uid, account.pw_gid)
    print("Backend files prepared with enabled=false; no jobs or timers started")


if __name__ == "__main__":
    main()
