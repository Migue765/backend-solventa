#!/usr/bin/env bash
# Despliega la version IMAGE_TAG: migra la base, actualiza el Deployment y revierte si el rollout falla.
# SERVICE_NAME (default cotizacion) elige Deployment, Job de migracion y manifiestos:
# deploy/k8s para cotizacion y deploy/k8s/<servicio> para el resto.
# MIGRATE=false (default true) omite el Job de migraciones, para servicios sin base de datos propia (ej. los BFF).
set -euo pipefail

MIGRATE="${MIGRATE:-true}"

SERVICE_NAME="${SERVICE_NAME:-cotizacion}"
if [ "$SERVICE_NAME" = "cotizacion" ]; then
  K8S_DIR="deploy/k8s"
else
  K8S_DIR="deploy/k8s/${SERVICE_NAME}"
fi

: "${CLUSTER_NAME:?}" "${NAMESPACE:?}" "${ECR_REPOSITORY_URL:?}" "${IMAGE_TAG:?}" "${AWS_REGION:?}"

if [ "$NAMESPACE" = "hu11-perfilamiento" ]; then
  echo "HU11 esta retirado: no se permite recrear ni actualizar sus workloads." >&2
  exit 1
fi
if [ "$NAMESPACE" = "calcular-cotizacion-preview" ]; then
  echo "Preview aislado: no se permite desplegar en calcular-cotizacion-preview." >&2
  exit 1
fi
if [ "$SERVICE_NAME" = "perfilamiento" ] && [ "$NAMESPACE" != "perfilamiento" ]; then
  echo "Perfilamiento solo se despliega en el namespace productivo perfilamiento." >&2
  exit 1
fi
if [ "$SERVICE_NAME" = "polizas" ] && [ "$NAMESPACE" != "polizas" ]; then
  echo "Polizas solo se despliega en el namespace productivo polizas." >&2
  exit 1
fi

export IMAGE="${ECR_REPOSITORY_URL}:${IMAGE_TAG}"
# Los nombres de Job deben ser DNS-1123: minusculas, digitos y guiones.
export MIGRATION_ID="$(printf '%s' "$IMAGE_TAG" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9\n' '-')"

aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION"

previous="$(kubectl -n "$NAMESPACE" get deployment "$SERVICE_NAME" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)"
echo "Version actual: ${previous:-ninguna} -> nueva: $IMAGE"

if [ "$MIGRATE" = "true" ]; then
  echo "== Migraciones =="
  envsubst < "${K8S_DIR}/migrate-job.yaml" | kubectl apply -f -
  if ! kubectl -n "$NAMESPACE" wait --for=condition=complete "job/${SERVICE_NAME}-migrate-${MIGRATION_ID}" --timeout=300s; then
    echo "La migracion fallo; no se despliega la nueva version." >&2
    kubectl -n "$NAMESPACE" logs "job/${SERVICE_NAME}-migrate-${MIGRATION_ID}" --tail=50 >&2 || true
    exit 1
  fi
else
  echo "== Migraciones omitidas (MIGRATE=false) =="
fi

echo "== Rollout =="
if [ -f "${K8S_DIR}/hpa.yaml" ]; then
  kubectl apply -f "${K8S_DIR}/hpa.yaml"
fi
if [ "$SERVICE_NAME" = "polizas" ]; then
  kubectl apply -f "${K8S_DIR}/service.yaml"
  envsubst < "${K8S_DIR}/deployment.yaml" | kubectl apply -f -
else
  kubectl -n "$NAMESPACE" scale deployment/"$SERVICE_NAME" --replicas=1
  kubectl -n "$NAMESPACE" set image deployment/"$SERVICE_NAME" "${SERVICE_NAME}=${IMAGE}"
fi
if ! kubectl -n "$NAMESPACE" rollout status deployment/"$SERVICE_NAME" --timeout=300s; then
  echo "El rollout fallo; revirtiendo a ${previous:-la revision anterior}." >&2
  if [ -n "$previous" ]; then
    kubectl -n "$NAMESPACE" rollout undo deployment/"$SERVICE_NAME"
    kubectl -n "$NAMESPACE" rollout status deployment/"$SERVICE_NAME" --timeout=300s || true
  fi
  exit 1
fi

if [ "$SERVICE_NAME" = "identidad" ]; then
  envsubst < "${K8S_DIR}/consent-outbox-cronjob.yaml" | kubectl apply -f -
fi

echo "Despliegue de ${IMAGE} completado."
