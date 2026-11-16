#!/bin/sh
# Starts the app as an unprivileged user.
#
# A Railway volume is mounted owned by root, which an unprivileged user cannot write to. So this starts as root only long enough to
# hand the data folders to the app user, then drops privileges for good. If the container was already started as a non-root user
# (for example Railway's RAILWAY_RUN_UID), it simply runs the command.
set -e

if [ "$(id -u)" = "0" ]; then
  APP_UID="$(id -u app)"
  state_dir="$(dirname "${STATE_PATH:-/data/tender_state.db}")"
  for dir in "$state_dir" "${VAULT_DIR:-}" "$(dirname "${BOQ_DB_PATH:-$state_dir/boq.db}")"; do
    [ -n "$dir" ] || continue
    mkdir -p "$dir"
    # Only when the top folder is not already the app user's, so a large Vault is not walked again at every start.
    if [ "$(stat -c %u "$dir")" != "$APP_UID" ]; then
      chown -R "$APP_UID:$APP_UID" "$dir"
    fi
  done
  exec setpriv --reuid="$APP_UID" --regid="$APP_UID" --init-groups --no-new-privs -- "$@"
fi

exec "$@"
