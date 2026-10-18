'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = name => fs.readFileSync(path.join(__dirname, '../workflows', name), 'utf8');

test('Calidad conserva comprobaciones existentes, sin CD ni checks extra de backport', () => {
  const ci = read('quality.yml');
  assert.match(ci, /name: Calidad/);
  for (const command of ['ruff format --check .', 'ruff check .', 'mypy scripts tests',
    'python scripts/validate_openapi.py', 'python -m pytest tests']) assert.ok(ci.includes(command));
  assert.doesNotMatch(ci, /backport_policy:|deploy:|contents: write|deployments: write/);
  assert.ok(ci.includes('node --test .github/scripts/*.test.cjs'));
});
test('solo candidatos verifican política desde base confiable; errores vuelven a CI normal', () => {
  const steps = read('quality.yml').split('\n      - name: ').slice(1);
  assert.ok(steps[0].includes('ref: ${{ github.event.pull_request.base.sha }}'));
  for (const step of steps.slice(0, 2)) assert.ok(step.includes("startsWith(github.head_ref, 'backport/release-pr-')"));
  assert.ok(steps[1].includes("core.setOutput('skip_tests', 'false')"));
  for (const step of steps.slice(2)) assert.ok(step.includes("steps.policy.outputs.skip_tests != 'true'"), step.split('\n')[0]);
});
test('backport automático desactivado por defecto, solo desde main y sin privilegios administrativos', () => {
  const automatic = read('automatic-backport.yml');
  assert.ok(automatic.includes("vars.BACKPORT_AUTOMATION_ENABLED == 'true'"));
  assert.ok(automatic.includes("github.ref == 'refs/heads/main'"));
  assert.ok(automatic.includes('ref: main'));
  assert.ok(automatic.includes('repositories: ${{ github.event.repository.name }}'));
  assert.doesNotMatch(automatic, /pull_request_target|administration:|permission-administration/);
});
test('hotfix conserva el flujo manual previo aunque se active el automático de releases', () => {
  const legacy = read('backport.yml');
  assert.ok(legacy.includes("vars.BACKPORT_AUTOMATION_ENABLED != 'true' || startsWith(github.head_ref, 'hotfix/')"));
  assert.ok(legacy.includes('secrets.BACKPORT_TOKEN || secrets.GITHUB_TOKEN'));
  assert.ok(read('gitflow-validation.yml').includes('allowed="feature/ backport/"'));
});
