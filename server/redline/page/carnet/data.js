// Les chiffres : chargés écran par écran, gardés sur l'appareil, recalculés au retour.
import {$} from '../shared/dom.js';
import {app, loading} from './state.js';
import {message} from './ui.js';
import {api} from './api.js';
import {dm, isoDate} from './format.js';
import {render, screen} from './nav.js';

// Charge un écran ; une même requête en vol n'est jamais doublée.
function load(key, path) {
  if (loading.has(key)) return loading.get(key);
  const job = (async () => {
    let data;
    try { data = await api(path); }
    catch (error) {
      if (!app.views[key]) { app.failures[key] = error.message; if (key === screen().key && !$('connected').hidden) render(); }
      throw error;
    }
    delete app.failures[key];
    const signature = JSON.stringify(data), changed = signature !== app.signatures[key];
    app.views[key] = data; app.signatures[key] = signature;
    if (key === 'today') { app.today = data; app.loadedAt = new Date(); stamp(); $('offline').hidden = true; }
    keep();
    const view = screen();
    const shown = key === view.key || (key === 'weeks' && view.root && app.active === 'week');
    if (changed && shown && !$('connected').hidden) render();
    return data;
  })().finally(() => loading.delete(key));
  loading.set(key, job);
  return job;
}
export const loadToday = () => load('today', 'today');
// L'écran visible se charge (une carte, immuable, une seule fois) ; la racine de
// Semaine emmène sa liste des semaines passées.
export function ensure() {
  const view = screen();
  if (view.path && !(view.key.startsWith('entry:') && app.views[view.key])) {
    load(view.key, view.path).catch(error => { if (app.views[view.key]) message(error.message); });
  }
  if (view.root && app.active === 'week') load('weeks', 'weeks').catch(() => {});
}
// Les derniers chiffres restent sur l'appareil pour que le carnet s'ouvre sans réseau,
// en montagne comme dans le métro (accord de Sam, 2026-10-08) : les trois racines
// et la liste des semaines, jamais une carte. Effacés à la déconnexion ou quand
// l'accès expire, ignorés au-delà de sept jours.
const STORE = 'carnet-data', STALE = 7 * 24 * 3600 * 1000;
function keep() {
  if (!app.today || !app.loadedAt) return;
  const kept = {};
  ['today', 'course', 'weeks', 'week:' + app.today.week].forEach(key => { if (app.views[key]) kept[key] = app.views[key]; });
  try { localStorage.setItem(STORE, JSON.stringify({at: app.loadedAt.getTime(), views: kept})); } catch { /* appareil plein ou privé : on s'en passe */ }
}
export function forget() {
  app.stash = null;
  try { localStorage.removeItem(STORE); } catch { /* rien à effacer */ }
}
export function recall() {
  try {
    const kept = JSON.parse(localStorage.getItem(STORE) || 'null');
    if (kept && kept.views && kept.views.today && Date.now() - kept.at < STALE) return kept;
  } catch { /* illisible : on repart du réseau */ }
  return null;
}
// Remet en mémoire les chiffres gardés, sans les marquer à jour : le prochain calcul
// les remplace et ne redessine que ce qui a changé.
export function hydrate(kept) {
  Object.entries(kept.views).forEach(([key, data]) => {
    if (!app.views[key]) { app.views[key] = data; app.signatures[key] = JSON.stringify(data); }
  });
  app.today = app.views.today; app.loadedAt = new Date(kept.at); stamp();
}
const keptAt = () => (app.loadedAt && isoDate(app.loadedAt) !== isoDate(new Date()) ? 'du ' + dm(isoDate(app.loadedAt)) + ' à ' : 'de ') + clockOf(app.loadedAt);
// La fraîcheur se lit dans les réglages : les chiffres sont recalculés au retour dans
// l'app, tirer la page n'est qu'un recours. Elle ne monte en haut que hors ligne.
const clockOf = date => date.toLocaleTimeString('fr-FR', {hour: '2-digit', minute: '2-digit'});
export function stamp() {
  $('stamp').textContent = (app.loadedAt ? 'Chiffres à jour à ' + clockOf(app.loadedAt) + '. ' : '') + 'Tire la page vers le bas pour actualiser.';
}
// `manual` : tirer la page. Sinon le recalcul est silencieux : pas de message, et une
// carte d'archive, immuable, n'est pas rechargée.
export async function refresh(manual = false) {
  if (app.refreshing) return;
  app.refreshing = true;
  try {
    await loadToday();
    const view = screen();
    if (view.key === 'week:' + app.today.week && !app.views[view.key]) render();
    const jobs = [load('week:' + app.today.week, 'week/' + app.today.week), load('course', 'race'), load('weeks', 'weeks')];
    const current = screen();
    const again = current.path && current.key !== 'today' && !(current.key.startsWith('entry:') && app.views[current.key])
      && (manual || current.key !== 'archive' || !app.views.archive);
    const waiting = again ? load(current.key, current.path) : null;
    jobs.forEach(job => job.catch(() => {}));
    if (waiting) await waiting;
  } catch (error) {
    if (!$('connected').hidden) {
      const offline = error instanceof TypeError || !navigator.onLine;
      // Sans réseau à l'ouverture, les chiffres gardés prennent le relais, datés.
      if (offline && !app.views.today && app.stash) { hydrate(app.stash); render(); }
      if (offline && app.loadedAt) { $('offline').textContent = 'Hors ligne · chiffres ' + keptAt(); $('offline').hidden = false; }
      if (manual) message(offline ? 'Pas de réseau. Tes chiffres reviennent avec la connexion.' : error.message);
    }
  } finally {
    app.refreshing = false;
  }
}
