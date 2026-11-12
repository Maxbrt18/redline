// The device sign-in: open the notebook, lock it, hand over to Safari.
import {$} from '../shared/dom.js';
import {app, skies} from './state.js';
import {content, message, notice} from './ui.js';
import {api} from './api.js';
import {isoDate} from './format.js';
import {closeViewer} from './viewer.js';
import {land, render} from './nav.js';
import {ensure, forget, hydrate, recall, refresh} from './data.js';
import {closeSheet} from './sheet.js';
import {dropWorkout, resumeWorkout} from './workout.js';

export function lock(text) {
  dropWorkout(); land(); app.generation++; app.today = null; app.openDay = null; app.stacks = {today: [], week: [], course: []};
  app.views = {}; app.signatures = {}; app.failures = {}; app.scrolls = {}; skies.clear();
  closeSheet(true); closeViewer(); forget(); $('offline').hidden = true;
  content.replaceChildren(); $('connected').hidden = true; $('locked').hidden = false;
  message(''); notice(text);
}
// Meta in-app browsers: their cookie does not carry over to Safari, and a link
// consumed here would no longer work there. The link therefore stays intact until chosen.
export const embedded = /FBAN|FBAV|FB_IAB|Messenger|Instagram/i.test(navigator.userAgent);
// The app opens on its own scheme with the same fragment: the token goes to the app, never to the server.
const appLink = (host) => `redline://${host}${location.hash}`;
export function handoff(token) {
  notice(''); $('handoff').hidden = false;
  $('handoff-app').href = appLink('connect');
  $('handoff-safari').href = location.href.replace(/^https:/, 'x-safari-https:');
  $('handoff-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(location.href); $('handoff-copy').textContent = 'Lien copié — colle-le dans Safari'; }
    catch { message('Copie impossible : utilise le menu ⋯ → Ouvrir dans le navigateur.'); }
  });
  $('handoff-here').addEventListener('click', () => { $('handoff').hidden = true; connect(token); });
}
// A session link opened in Messenger: the app runs it with its timers and sounds; the page
// still can, here.
export function sessionHandoff(stay) {
  notice(''); $('handoff').hidden = false;
  $('handoff-title').textContent = 'Ouvre ta séance';
  $('handoff-why').textContent = 'Dans l’app Redline, la séance garde ses minuteurs et ses sons même écran verrouillé, et son compte rendu part dès que le réseau revient.';
  $('handoff-browser').hidden = true;
  $('handoff-app').href = appLink('s');
  $('handoff-here').textContent = 'Continuer ici';
  $('handoff-here').addEventListener('click', () => { $('handoff').hidden = true; stay(); });
}
export async function connect(token) {
  const entry = new URLSearchParams(location.hash.slice(1)).get('entry');
  // Remove the secret before any navigation and never store it in JavaScript.
  history.replaceState(null, '', '/carnet');
  // A card received in Messenger opens on top of Today: back leads there.
  if (entry) app.stacks.today = [{k: 'entry', id: entry}];
  // Kept figures from today show right away; those from another day only wait for a
  // network failure.
  app.stash = recall();
  if (app.stash && app.stash.views.today.date === isoDate(new Date())) hydrate(app.stash);
  // The link exchange goes first; figures requested meanwhile wait for it.
  if (token) app.connecting = api('connect', {token});
  // The app structure is there at once; the figures take their place.
  notice(''); $('locked').hidden = true; $('connected').hidden = false; render(); ensure();
  resumeWorkout();
  if (token) {
    try { await app.connecting; } catch (error) { lock(error.message); return; } finally { app.connecting = null; }
  }
  await refresh();
}
