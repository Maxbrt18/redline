// La connexion de l'appareil : ouvrir le carnet, le verrouiller, passer la main à Safari.
import {$} from '../shared/dom.js';
import {app, skies} from './state.js';
import {content, message, notice} from './ui.js';
import {api} from './api.js';
import {isoDate} from './format.js';
import {closeViewer} from './viewer.js';
import {land, render} from './nav.js';
import {ensure, forget, hydrate, recall, refresh} from './data.js';
import {closeSheet} from './sheet.js';
import {dropSeance, resumeSeance} from './seance.js';

export function lock(text) {
  dropSeance(); land(); app.generation++; app.today = null; app.openDay = null; app.stacks = {today: [], week: [], course: []};
  app.views = {}; app.signatures = {}; app.failures = {}; app.scrolls = {}; skies.clear();
  closeSheet(true); closeViewer(); forget(); $('offline').hidden = true;
  content.replaceChildren(); $('connected').hidden = true; $('locked').hidden = false;
  message(''); notice(text);
}
// Navigateurs intégrés de Meta : leur cookie ne suit pas dans Safari, et un lien
// consommé ici ne servirait plus là-bas. Le lien reste donc intact jusqu'au choix.
export const embedded = /FBAN|FBAV|FB_IAB|Messenger|Instagram/i.test(navigator.userAgent);
export function handoff(token) {
  notice(''); $('handoff').hidden = false;
  $('handoff-safari').href = location.href.replace(/^https:/, 'x-safari-https:');
  $('handoff-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(location.href); $('handoff-copy').textContent = 'Lien copié — colle-le dans Safari'; }
    catch { message('Copie impossible : utilise le menu ⋯ → Ouvrir dans le navigateur.'); }
  });
  $('handoff-here').addEventListener('click', () => { $('handoff').hidden = true; connect(token); });
}
export async function connect(token) {
  const entry = new URLSearchParams(location.hash.slice(1)).get('entry');
  // Retirer le secret avant toute navigation et ne jamais le stocker en JavaScript.
  history.replaceState(null, '', '/carnet');
  // Une carte reçue dans Messenger s'ouvre par-dessus Aujourd'hui : le retour y mène.
  if (entry) app.stacks.today = [{k: 'entry', id: entry}];
  // Les chiffres gardés d'aujourd'hui s'affichent tout de suite ; ceux d'un autre jour
  // n'attendent qu'une panne de réseau.
  app.stash = recall();
  if (app.stash && app.stash.views.today.date === isoDate(new Date())) hydrate(app.stash);
  // L'échange du lien part le premier ; les chiffres demandés pendant ce temps l'attendent.
  if (token) app.connecting = api('connect', {token});
  // La structure de l'app est là tout de suite ; les chiffres prennent leur place.
  notice(''); $('locked').hidden = true; $('connected').hidden = false; render(); ensure();
  resumeSeance();
  if (token) {
    try { await app.connecting; } catch (error) { lock(error.message); return; } finally { app.connecting = null; }
  }
  await refresh();
}
