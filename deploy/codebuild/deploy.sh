#!/usr/bin/env bash
# Despliega la version IMAGE_TAG: migra la base, actualiza el Deployment y revierte si el rollout falla.
set -euo pipefail

: "${CLUSTER_NAME:?}" "${NAMESPACE:?}" "${ECR_REPOSITORY_URL:?}" "${IMAGE_TAG:?}" "${AWS_REGION:?}"

export IMAGE="${ECR_REPOSITORY_URL}:${IMAGE_TAG}"
# Los nombres de Job deben ser DNS-1123: minusculas, digitos y guiones.
export MIGRATION_ID="$(printf '%s' "$IMAGE_TAG" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9\n' '-')"

aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION"

previous="$(kubectl -n "$NAMESPACE" get deployment cotizacion -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)"
echo "Version actual: ${previous:-ninguna} -> nueva: $IMAGE"

echo "== Migraciones =="
envsubst < deploy/k8s/migrate-job.yaml | kubectl apply -f -
if ! kubectl -n "$NAMESPACE" wait --for=condition=complete "job/cotizacion-migrate-${MIGRATION_ID}" --timeout=300s; then
  echo "La migracion fallo; no se despliega la nueva version." >&2
  kubectl -n "$NAMESPACE" logs "job/cotizacion-migrate-${MIGRATION_ID}" --tail=50 >&2 || true
  exit 1
fi

echo "== Rollout =="
kubectl -n "$NAMESPACE" set image deployment/cotizacion "cotizacion=${IMAGE}"
if ! kubectl -n "$NAMESPACE" rollout status deployment/cotizacion --timeout=300s; then
  echo "El rollout fallo; revirtiendo a ${previous:-la revision anterior}." >&2
  kubectl -n "$NAMESPACE" rollout undo deployment/cotizacion
  kubectl -n "$NAMESPACE" rollout status deployment/cotizacion --timeout=300s || true
  exit 1
fi

echo "Despliegue de ${IMAGE} completado."
