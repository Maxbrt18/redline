// The figures: loaded screen by screen, kept on the device, recomputed on return.
import {$} from '../shared/dom.js';
import {app, loading} from './state.js';
import {message} from './ui.js';
import {api} from './api.js';
import {dm, isoDate} from './format.js';
import {render, screen} from './nav.js';

// Loads a screen; the same request in flight is never duplicated.
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
// The visible screen loads (a card is immutable, loaded once); the Week root also
// brings its list of past weeks.
export function ensure() {
  const view = screen();
  if (view.path && !(view.key.startsWith('entry:') && app.views[view.key])) {
    load(view.key, view.path).catch(error => { if (app.views[view.key]) message(error.message); });
  }
  if (view.root && app.active === 'week') load('weeks', 'weeks').catch(() => {});
}
// The latest figures stay on the device so the notebook opens without a network: the
// three roots and the list of weeks, never a card. Cleared on sign-out or when access
// expires, ignored after seven days.
const STORE = 'carnet-data', STALE = 7 * 24 * 3600 * 1000;
function keep() {
  if (!app.today || !app.loadedAt) return;
  const kept = {};
  ['today', 'course', 'weeks', 'week:' + app.today.week].forEach(key => { if (app.views[key]) kept[key] = app.views[key]; });
  try { localStorage.setItem(STORE, JSON.stringify({at: app.loadedAt.getTime(), views: kept})); } catch { /* device full or private: do without */ }
}
export function forget() {
  app.stash = null;
  try { localStorage.removeItem(STORE); } catch { /* nothing to clear */ }
}
export function recall() {
  try {
    const kept = JSON.parse(localStorage.getItem(STORE) || 'null');
    if (kept && kept.views && kept.views.today && Date.now() - kept.at < STALE) return kept;
  } catch { /* unreadable: start again from the network */ }
  return null;
}
// Restores the kept figures without marking them fresh: the next computation replaces
// them and redraws only what changed.
export function hydrate(kept) {
  Object.entries(kept.views).forEach(([key, data]) => {
    if (!app.views[key]) { app.views[key] = data; app.signatures[key] = JSON.stringify(data); }
  });
  app.today = app.views.today; app.loadedAt = new Date(kept.at); stamp();
}
const keptAt = () => (app.loadedAt && isoDate(app.loadedAt) !== isoDate(new Date()) ? 'du ' + dm(isoDate(app.loadedAt)) + ' à ' : 'de ') + clockOf(app.loadedAt);
// Freshness is shown in the settings: figures are recomputed on return to the app,
// pulling the page is only a fallback. It scrolls to the top only when offline.
const clockOf = date => date.toLocaleTimeString('fr-FR', {hour: '2-digit', minute: '2-digit'});
export function stamp() {
  $('stamp').textContent = (app.loadedAt ? 'Chiffres à jour à ' + clockOf(app.loadedAt) + '. ' : '') + 'Tire la page vers le bas pour actualiser.';
}
// `manual`: pulling the page. Otherwise the recomputation is silent: no message, and an
// immutable archive card is not reloaded.
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
      // Without a network at launch, the kept figures take over, dated.
      if (offline && !app.views.today && app.stash) { hydrate(app.stash); render(); }
      if (offline && app.loadedAt) { $('offline').textContent = 'Hors ligne · chiffres ' + keptAt(); $('offline').hidden = false; }
      if (manual) message(offline ? 'Pas de réseau. Tes chiffres reviennent avec la connexion.' : error.message);
    }
  } finally {
    app.refreshing = false;
  }
}
