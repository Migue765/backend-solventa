#!/usr/bin/env bash
# Despliega la version IMAGE_TAG: migra la base, actualiza el Deployment y revierte si el rollout falla.
# SERVICE_NAME (default cotizacion) elige Deployment, Job de migracion y manifiestos:
# deploy/k8s para cotizacion y deploy/k8s/<servicio> para el resto.
# MIGRATE=false (default true) omite el Job de migraciones, para servicios sin base de datos propia (ej. los BFF).
# Polizas, siniestros, notificaciones y asistencias aplican su Service y Deployment completos.
# Siniestros exige que su ServiceAccount ya tenga el rol IRSA del bucket de evidencias.
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
case "$SERVICE_NAME" in
  siniestros | notificaciones | asistencias)
    if [ "$NAMESPACE" != "$SERVICE_NAME" ]; then
      echo "El servicio ${SERVICE_NAME} solo se despliega en el namespace productivo ${SERVICE_NAME}." >&2
      exit 1
    fi
    ;;
esac

export AWS_REGION
export IMAGE="${ECR_REPOSITORY_URL}:${IMAGE_TAG}"
# Los nombres de Job deben ser DNS-1123: minusculas, digitos y guiones.
export MIGRATION_ID="$(printf '%s' "$IMAGE_TAG" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9\n' '-')"

aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION"

if [ "$SERVICE_NAME" = "siniestros" ]; then
  # Sin el rol IRSA los pods no se crean o no pueden guardar evidencias en S3.
  irsa_role="$(kubectl -n "$NAMESPACE" get serviceaccount siniestros -o jsonpath='{.metadata.annotations.eks\.amazonaws\.com/role-arn}' 2>/dev/null || true)"
  case "$irsa_role" in
    arn:aws:iam::*:role/*) ;;
    *)
      echo "Falta el ServiceAccount siniestros con un rol IAM en eks.amazonaws.com/role-arn; aplicar deploy/k8s/siniestros/serviceaccount.yaml con envsubst antes de desplegar." >&2
      exit 1
      ;;
  esac
fi

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
if [ "$SERVICE_NAME" = "bff-web" ]; then
  # Extraer solo imagen y dependencia; el resto del entorno pertenece al runtime.
  if ! bff_patch="$(kubectl set image --local -f "${K8S_DIR}/deployment.yaml" "bff-web=${IMAGE}" \
    --allow-missing-template-keys=false -o go-template='
{{- range $container := .spec.template.spec.containers -}}
{{- if eq $container.name "bff-web" -}}
{{- range $container.env -}}
{{- if eq .name "PERFILAMIENTO_SERVICE_URL" -}}
{{- if index . "value" -}}
{"spec":{"template":{"spec":{"containers":[{"name":"bff-web","image":{{printf "%q" $container.image}},"env":[{"name":"PERFILAMIENTO_SERVICE_URL","value":{{printf "%q" .value}},"valueFrom":null}]}]}}}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}')"; then
    echo "No fue posible preparar la actualizacion de bff-web desde su manifiesto." >&2
    exit 1
  fi
  if [ -z "$bff_patch" ]; then
    echo "Falta PERFILAMIENTO_SERVICE_URL con valor en el manifiesto de bff-web." >&2
    exit 1
  fi
fi
if [ -f "${K8S_DIR}/hpa.yaml" ]; then
  kubectl apply -f "${K8S_DIR}/hpa.yaml"
fi
case "$SERVICE_NAME" in
  polizas | siniestros | notificaciones | asistencias) full_manifest=true ;;
  *) full_manifest=false ;;
esac
if [ "$full_manifest" = "true" ]; then
  kubectl apply -f "${K8S_DIR}/service.yaml"
  envsubst < "${K8S_DIR}/deployment.yaml" | kubectl apply -f -
else
  kubectl -n "$NAMESPACE" scale deployment/"$SERVICE_NAME" --replicas=1
  if [ "$SERVICE_NAME" = "bff-web" ]; then
    if ! kubectl -n "$NAMESPACE" patch deployment/"$SERVICE_NAME" --type=strategic -p "$bff_patch"; then
      echo "No se confirmo la actualizacion de bff-web; revisar su estado antes de reintentar." >&2
      exit 1
    fi
  else
    kubectl -n "$NAMESPACE" set image deployment/"$SERVICE_NAME" "${SERVICE_NAME}=${IMAGE}"
  fi
fi
if ! kubectl -n "$NAMESPACE" rollout status deployment/"$SERVICE_NAME" --timeout=300s; then
  echo "El rollout fallo; revirtiendo a ${previous:-la revision anterior}." >&2
  if [ -n "$previous" ]; then
    if ! kubectl -n "$NAMESPACE" rollout undo deployment/"$SERVICE_NAME"; then
      echo "No fue posible iniciar el rollback de ${SERVICE_NAME}." >&2
      exit 1
    fi
    if ! kubectl -n "$NAMESPACE" rollout status deployment/"$SERVICE_NAME" --timeout=300s; then
      echo "El rollback de ${SERVICE_NAME} no quedo confirmado." >&2
    else
      echo "Rollback de ${SERVICE_NAME} confirmado." >&2
    fi
  fi
  exit 1
fi

if [ "$SERVICE_NAME" = "identidad" ]; then
  envsubst < "${K8S_DIR}/consent-outbox-cronjob.yaml" | kubectl apply -f -
fi
if [ "$SERVICE_NAME" = "notificaciones" ]; then
  envsubst < "${K8S_DIR}/despachos-cronjob.yaml" | kubectl apply -f -
fi

echo "Despliegue de ${IMAGE} completado."
