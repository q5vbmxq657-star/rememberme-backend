#!/bin/zsh
set -euo pipefail

BACKEND="${REMEMBERME_BACKEND_ROOT:-$HOME/Developer/RemembermeAI/rememberme-backend}"
PYTHON="$BACKEND/.venv/bin/python"

if [[ ! -d "$BACKEND" ]]; then
    echo "FEHLER: Backend wurde nicht gefunden:"
    echo "$BACKEND"
    exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
    echo "FEHLER: Produktions-Python wurde nicht gefunden:"
    echo "$PYTHON"
    exit 1
fi

cd "$BACKEND"

if [[ -f "$BACKEND/.env" ]]; then
    set -a
    source "$BACKEND/.env"
    set +a
fi

REQUIRED_KEYS=(
    "TAVUS_API_KEY"
    "TAVUS_REPLICA_ID"
    "TAVUS_PERSONA_ID"
    "LIVEKIT_URL"
    "LIVEKIT_API_KEY"
    "LIVEKIT_API_SECRET"
)

for KEY in "${REQUIRED_KEYS[@]}"; do
    VALUE="${(P)KEY:-}"

    if [[ -z "${VALUE//[[:space:]]/}" ]]; then
        echo "FEHLER: Fehlende Worker-Konfiguration: $KEY"
        exit 1
    fi
done

if [[ "${AVATAR_RUNTIME_ENABLE_TAVUS:-false:l}" != "true" ]]; then
    echo "FEHLER: AVATAR_RUNTIME_ENABLE_TAVUS ist nicht true."
    exit 1
fi

echo "================================================================================"
echo "REMEMBERMEAI — TAVUS AVATAR WORKER"
echo "================================================================================"
echo
echo "Backend: $BACKEND"
echo "Worker:  ${AVATAR_RUNTIME_TAVUS_WORKER_NAME:-rememberme-tavus-avatar}"
echo
echo "Der Worker registriert sich jetzt bei LiveKit."
echo "Beenden mit Ctrl+C."
echo "================================================================================"

exec "$PYTHON" -B -m app.workers.avatar_tavus_worker start
