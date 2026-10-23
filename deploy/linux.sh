#!/bin/sh
# Linux deployment launcher. Never removes or reinitializes an existing volume.
set -eu
umask 077
task_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
task_action=${1:-start}
task_image=${2:-gep:0.1.1}
task_port=${GEP_PORT:-8080}
case "$task_port" in ''|*[!0-9]*) echo 'GEP_PORT must be a number.' >&2; exit 2;; esac
[ "$task_port" -ge 1024 ] && [ "$task_port" -le 65535 ] || { echo 'GEP_PORT must be 1024-65535.' >&2; exit 2; }
case "$task_action" in start|stop|status) ;; *) echo 'Usage: sh deploy/linux.sh start|stop|status [image]' >&2; exit 2;; esac
cd "$task_root"
docker info --format '{{.OSType}}' | grep -qx linux || { echo 'A Linux Docker engine is required.' >&2; exit 1; }
docker compose version >/dev/null
if [ ! -f .env ]; then
    [ "$task_action" = start ] || { echo 'Start a new instance first.' >&2; exit 1; }
    if ! docker image inspect "$task_image" >/dev/null 2>&1; then
        [ -f gep-image.tar ] || { echo 'The release image archive is missing.' >&2; exit 1; }
        docker image load --input gep-image.tar
    fi
    task_uuid=$(cat /proc/sys/kernel/random/uuid)
    task_suffix=$(printf '%s' "$task_uuid" | tr -d '-' | cut -c1-12)
    # Noclobber keeps a concurrent launcher from replacing the selected volume.
    (set -C; printf '%s\n' "GEP_IMAGE=$task_image" "GEP_PROJECT_NAME=gep_$task_suffix" \
        "GEP_VOLUME=gep_${task_suffix}_data" "GEP_EXPECTED_INSTANCE=$task_uuid" \
        'GEP_SETUP_ENABLED=1' 'GEP_BIND_ADDRESS=127.0.0.1' "GEP_PORT=$task_port" \
        'GEP_ADMIN_HOST=admin.localhost' 'GEP_EXPERIMENT_HOST=experiment.localhost' \
        'GEP_WWW_HOST=www.localhost' "GEP_ADMIN_ORIGIN=http://admin.localhost:$task_port" \
        "GEP_PUBLIC_API=http://experiment.localhost:$task_port" 'GEP_HTTPS=0' 'GEP_BASE_DOMAIN=' > .env)
fi
case "$task_action" in
    start) docker compose --env-file .env -f compose.yaml up -d --wait --wait-timeout 90
           printf '%s\n' "Open http://admin.localhost:$task_port/login locally or through an SSH tunnel (existing instances keep their .env port).";;
    stop) docker compose --env-file .env -f compose.yaml stop;;
    status) docker compose --env-file .env -f compose.yaml ps;;
esac
