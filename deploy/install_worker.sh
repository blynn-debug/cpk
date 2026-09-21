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
launchctl bootstrap "$WORKER_DOMAIN" "$WORKER_PLIST"
launchctl enable "$WORKER_DOMAIN/com.cpk.worker"
echo 'Installed on-demand worker; no scheduled searches.'
