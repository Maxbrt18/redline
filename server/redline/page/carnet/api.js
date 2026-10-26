// Le seul accès réseau du carnet : /carnet-api, avec le cookie de l'appareil.
import {app} from './state.js';
import {lock} from './session.js';

export async function api(path, body) {
  // Tant que le lien s'échange contre le cookie de l'appareil, rien d'autre ne part :
  // un premier appel sans cookie revenait en 401 et verrouillait un carnet qui venait de
  // s'ouvrir (2026-10-26). Un échange raté se dit lui-même : ce qui l'attendait ne part pas.
  if (path !== 'connect' && app.connecting) {
    try { await app.connecting; } catch { throw new Error('Connexion impossible.'); }
  }
  const response = await fetch('/carnet-api/' + path, {
    method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: body === undefined ? {} : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const value = await response.json();
  if (!response.ok) {
    if (response.status === 401) lock(value.error);
    throw new Error(value.error || 'Le carnet est indisponible pour le moment.');
  }
  return value;
}
