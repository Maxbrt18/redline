#!/usr/bin/env bash
# Host configuration: automatic updates and SSH. Idempotent.
#
#   sudo deploy/host/install.sh
#
# The host firewall is firewalld (public zone: ssh, http, https, dhcpv6-client), a
# second layer behind the Oracle Security List. This script does not modify it.
# Nothing here restarts an application service: SSH is reloaded, open connections
# stay in place.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
[ "$(id -u)" -eq 0 ] || { echo "install.sh : à lancer avec sudo" >&2; exit 1; }
[ $# -eq 0 ] || { echo "usage : install.sh" >&2; exit 2; }

# Validate before enabling: a rejected configuration is removed immediately.
install -o root -g root -m 0644 "$here/sshd-redline.conf" \
    /etc/ssh/sshd_config.d/40-redline.conf
if ! sshd -t; then
    rm -f /etc/ssh/sshd_config.d/40-redline.conf
    echo "configuration SSH refusée : fichier retiré, rien n'a été rechargé" >&2
    exit 1
fi

install -o root -g root -m 0644 "$here/52redline-unattended-upgrades" \
    /etc/apt/apt.conf.d/52redline-unattended-upgrades
apt-config dump >/dev/null
install -D -o root -g root -m 0644 "$here/apt-daily.timer.conf" \
    /etc/systemd/system/apt-daily.timer.d/redline.conf
install -D -o root -g root -m 0644 "$here/apt-daily-upgrade.timer.conf" \
    /etc/systemd/system/apt-daily-upgrade.timer.d/redline.conf

systemctl daemon-reload
systemctl restart apt-daily.timer apt-daily-upgrade.timer
systemctl reload ssh

# rpcbind has no use here (no NFS); firewalld already blocks it, so it is masked.
systemctl mask --now rpcbind.service rpcbind.socket >/dev/null

echo "hôte configuré :"
systemctl list-timers --no-pager 'apt-daily*' | sed -n '2,3p'
