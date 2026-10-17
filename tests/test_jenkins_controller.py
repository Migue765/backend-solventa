import runpy

import pytest


def test_backend_template_defers_busy_jobs_without_consuming_comments() -> None:
    prepare = runpy.run_path("infra/jenkins/prepare-controller.py")["prepare_files"]
    scanner = (
        "def branch = project.getProjectFactory().getBranch(job)\n"
        "def revision = source.fetch(branch.head, null)\n"
        "job.scheduleBuild2(0)\nprocessed.add(id)\n"
    )
    policy = "class SolventaWebCommentPolicy {\n}\n"
    scanner, policy = prepare(scanner, policy)
    guard = "if (!policy.canSchedule(job.isInQueue(), job.isBuilding())) return"
    assert scanner.index(guard) < scanner.index("source.fetch")
    assert scanner.index(guard) < scanner.index("processed.add")
    assert "static boolean canSchedule(" in policy
    assert "!queued && !building" in policy
    assert "SolventaBackendCommentPolicy" in policy
    assert prepare(scanner, policy) == (scanner, policy)


def test_backend_template_rejects_unknown_scheduling_layout() -> None:
    prepare = runpy.run_path("infra/jenkins/prepare-controller.py")["prepare_files"]
    with pytest.raises(RuntimeError, match="scheduling point"):
        prepare("unrecognized layout", "class SolventaWebCommentPolicy {}")
