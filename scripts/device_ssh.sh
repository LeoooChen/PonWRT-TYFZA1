#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Project-local key and host records for the TYFZA1 RAM development image.
P=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
exec /usr/bin/ssh -F /dev/null -i "$P/analysis/private/bringup_ed25519" \
    -o IdentityAgent=none -o IdentitiesOnly=yes -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new -o GlobalKnownHostsFile=/dev/null \
    -o "UserKnownHostsFile=$P/analysis/private/known_hosts" \
    -o ConnectTimeout=5 root@192.168.1.1 "$@"
