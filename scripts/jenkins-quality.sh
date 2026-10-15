#!/usr/bin/env bash
set -euo pipefail

python=.venv-jenkins/bin/python
"$python" -m ruff format --check .
"$python" -m ruff check .
"$python" -m mypy scripts tests
for config in services/*/mypy.ini; do
    [[ -f "$config" ]] || continue
    service_directory="$(dirname "$config")"
    "$python" -m mypy --config-file "$config" "$service_directory/src" "$service_directory/tests"
done
"$python" scripts/validate_openapi.py
mkdir -p reports
"$python" -m pytest tests --junitxml=reports/root.xml
for project in services/*/pyproject.toml; do
    [[ -f "$project" ]] || continue
    service_directory="$(dirname "$project")"
    if find "$service_directory/tests" -type f -name 'test_*.py' -print -quit | grep -q .; then
        "$python" -m pytest "$service_directory/tests" --junitxml="reports/$(basename "$service_directory").xml"
    fi
done
