#!/bin/sh
# Install/restart only the on-demand worker. Does not restart Chrome or change credentials.
set -eu
cd "$HOME/cpk"
mkdir -p state "$HOME/Library/LaunchAgents"
chmod 700 state
WORKER_PLIST="$HOME/Library/LaunchAgents/com.cpk.worker.plist"
sed "s|__HOME__|$HOME|g" deploy/com.cpk.worker.plist > "$WORKER_PLIST"
WORKER_DOMAIN="gui/$(id -u)"
launchctl bootout "$WORKER_DOMAIN/com.cpk.worker" 2>/dev/null || true
# bootout can return before launchd releases the old registration.
WORKER_BOOTSTRAP_ATTEMPT=0
until launchctl bootstrap "$WORKER_DOMAIN" "$WORKER_PLIST"; do
    WORKER_BOOTSTRAP_ATTEMPT=$((WORKER_BOOTSTRAP_ATTEMPT + 1))
    if [ "$WORKER_BOOTSTRAP_ATTEMPT" -ge 10 ]; then
        echo 'Worker registration failed; inspect launchctl before retrying.' >&2
        exit 1
    fi
    sleep 1
done
launchctl enable "$WORKER_DOMAIN/com.cpk.worker"
echo 'Installed on-demand worker; no scheduled searches.'
