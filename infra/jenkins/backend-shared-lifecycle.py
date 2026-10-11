"""Approved three-project demand wrapper; preserve original idle policy and identity."""

import importlib.util
import sys


def main() -> int:
    shared_spec = importlib.util.spec_from_file_location(
        "shared_demand", "/opt/jenkins-web/shared-agent-lifecycle.py"
    )
    if shared_spec is None or shared_spec.loader is None:
        raise RuntimeError("Shared demand module unavailable")
    shared = importlib.util.module_from_spec(shared_spec)
    shared_spec.loader.exec_module(shared)
    spec = importlib.util.spec_from_file_location(
        "original_lifecycle", "/opt/jenkins-pilot/agent-lifecycle.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Original lifecycle unavailable")
    lifecycle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lifecycle)
    projects = ("solventa-android", "solventa-web/pr-checks", "solventa-backend/pr-checks")
    lifecycle.read_demand = lambda queue, node, unused: shared.read_shared_demand(queue, node, projects)
    return int(lifecycle.main())


if __name__ == "__main__":
    sys.exit(main())
