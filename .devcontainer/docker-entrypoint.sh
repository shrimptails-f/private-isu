#!/usr/bin/env bash
set -eu

# Match the socket's host-side group, whose numeric ID varies between hosts.
socket_gid=$(stat -c '%g' /var/run/docker.sock)
if ! getent group "$socket_gid" >/dev/null; then
  groupadd -g "$socket_gid" docker-host
fi
usermod -aG "$socket_gid" dev
exec gosu dev "$@"
