#!/usr/bin/env bash
# Configuration de l'hôte : mises à jour automatiques et SSH. Idempotent.
#
#   sudo server/host/install.sh
#
# Le pare-feu de l'hôte est firewalld (zone public : ssh, http, https, dhcpv6-client),
# seconde couche derrière la Security List Oracle. Ce script ne le modifie pas.
# Rien ici ne redémarre de service applicatif : SSH est rechargé, les connexions
# ouvertes restent en place.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
[ "$(id -u)" -eq 0 ] || { echo "install.sh : à lancer avec sudo" >&2; exit 1; }
[ $# -eq 0 ] || { echo "usage : install.sh" >&2; exit 2; }

# Valider avant d'activer : une configuration refusée est retirée aussitôt.
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

# rpcbind n'a aucun usage ici (pas de NFS) ; firewalld le bloque déjà, il disparaît.
systemctl mask --now rpcbind.service rpcbind.socket >/dev/null

echo "hôte configuré :"
systemctl list-timers --no-pager 'apt-daily*' | sed -n '2,3p'
