// The only network access of the notebook: /notebook-api, with the device cookie.
import {app} from './state.js';
import {lock} from './session.js';

export async function api(path, body) {
  // While the link is being exchanged for the device cookie, nothing else is sent: a first
  // call without a cookie would return 401 and lock a notebook that has just opened. A
  // failed exchange reports itself: whatever was waiting on it is not sent.
  if (path !== 'connect' && app.connecting) {
    try { await app.connecting; } catch { throw new Error('Connexion impossible.'); }
  }
  const response = await fetch('/notebook-api/' + path, {
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
