#!/usr/bin/env bash
set -euo pipefail

python=.venv-jenkins/bin/python
mkdir -p reports
"$python" -m pytest tests --junitxml=reports/root.xml --cov=scripts --cov-branch \
    --cov-report=term-missing --cov-report=xml:reports/coverage/root.xml \
    --cov-report=html:reports/coverage/root
for project in services/*/pyproject.toml; do
    [[ -f "$project" ]] || continue
    service_directory="$(dirname "$project")"
    if find "$service_directory/tests" -type f -name 'test_*.py' -print -quit | grep -q .; then
        service="$(basename "$service_directory")"
        "$python" -m pytest "$service_directory/tests" --junitxml="reports/$service.xml" \
            --cov="$service_directory/src" --cov-branch --cov-report=term-missing \
            --cov-report="xml:reports/coverage/$service.xml" \
            --cov-report="html:reports/coverage/$service"
    fi
done
