// ------------------------------------------------------------ séance guidée
// Le renfo s'ouvre en plein écran par-dessus le carnet (maquette validée par l'athlète le
// 2026-10-09) : la barre du carnet avec ✕ à gauche et le son à droite, le grand titre
// des autres écrans, pas d'onglets. Pendant la séance, ni glissement retour ni tirer
// pour actualiser : ✕ est la seule sortie, et une séance commencée demande confirmation,
// en disant que rien n'est perdu. Un lien reçu dans Messenger (/s#t=…) ouvre la même
// app directement sur la séance ; ✕ mène alors au carnet.
import {$, el} from '../shared/dom.js';
import {remembered} from '../shared/storage.js';
import {linkToken} from '../strength/link.js';
import {paintSound, renderSoundSettings} from '../strength/sound.js';
import {sessionUnfinished, startSession, stopSession} from '../strength/main.js';
import {app, calm} from './state.js';
import {api} from './api.js';
import {EASE, barState, freeze, land, pageNode, remember, render, screen} from './nav.js';
import {loadToday} from './data.js';
import {dismiss, present} from './sheet.js';

const KEPT = 'carnet-seance';  // la séance ouverte, retrouvée si iOS recharge la page
const RISE = 420;  // ms, un peu plus long qu'un écran poussé : la séance vient de plus loin
const cover = $('seance');

// Le lien de la séance du jour : demandé au service, gardé pour la rouvrir sans réseau.
export async function launchSeance(strength) {
  const key = 'redline:strength:lien:' + strength.session.sid;
  try {
    const result = await api('launch', {signature: strength.signature});
    const destination = new URL(result.url, location.origin), token = linkToken(destination.hash);
    if (destination.origin !== location.origin || destination.pathname !== '/s' || !token) throw new Error('Lien de séance invalide.');
    remembered(key, token);
    return token;
  } catch (error) {
    const kept = remembered(key);
    if (kept && (error instanceof TypeError || !navigator.onLine)) return kept;
    throw error;
  }
}

// L'écran qu'on quitte reste visible dessous pendant que la séance monte.
export function openSeance(token, motion = true) {
  land(); remember();
  const under = motion && !calm.matches ? freeze(true) : null;
  if (under) under.append(document.querySelector('.n-nav').cloneNode(true));
  app.seance = token;
  if (!app.standalone) { try { sessionStorage.setItem(KEPT, token); } catch { /* sans mémoire : pas de reprise au rechargement */ } }
  document.documentElement.classList.add('n-seance');
  cover.hidden = false;
  $('bar-title').textContent = '';
  scrollTo(0, 0);
  paintSound();
  barState(true);
  if (under) {
    cover.classList.add('n-rising');
    cover.animate([{transform: 'translateY(100%)'}, {transform: 'none'}], {duration: RISE, easing: EASE})
      .finished.catch(() => {}).finally(() => { under.remove(); cover.classList.remove('n-rising'); });
  }
  startSession(token).then(() => { $('bar-title').textContent = $('title').textContent; barState(true); });
}

function closeSeance() {
  if (!app.seance) return;
  if (app.standalone) { stopSession(); location.replace('/carnet'); return; }
  // La séance qui descend est une copie figée ; le carnet est déjà en place dessous.
  let leaving = null;
  if (!calm.matches) {
    const rect = cover.getBoundingClientRect(), copy = cover.cloneNode(true);
    copy.removeAttribute('id'); copy.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
    copy.style.cssText = 'position:absolute;margin:0;left:' + rect.left + 'px;top:' + rect.top + 'px;width:' + rect.width + 'px';
    leaving = el('div', null, 'n-move n-falling');
    leaving.setAttribute('aria-hidden', 'true');
    leaving.append(copy);
  }
  dropSeance();
  render(app.scrolls[screen().key] || 0);
  if (leaving) {
    pageNode.after(leaving);
    leaving.animate([{transform: 'none'}, {transform: 'translateY(100%)'}], {duration: RISE * 0.8, easing: EASE})
      .finished.catch(() => {}).finally(() => leaving.remove());
  }
  loadToday().catch(() => {});  // le renfo a pu être envoyé : Aujourd'hui le coche
}

// Sans animation ni retour au carnet : la séance s'efface (aussi quand l'appareil est déconnecté).
export function dropSeance() {
  if (!app.seance) return;
  stopSession();
  app.seance = null;
  try { sessionStorage.removeItem(KEPT); } catch { /* rien à oublier */ }
  document.documentElement.classList.remove('n-seance');
  cover.hidden = true;
  document.title = 'Mon carnet — Redline';
}

// Après un rechargement de la page par iOS, la séance ouverte revient telle quelle.
export function resumeSeance() {
  let kept = null;
  try { kept = sessionStorage.getItem(KEPT); } catch { return; }
  if (kept) openSeance(kept, false);
}

// Le lien de Messenger : la séance seule, sans passer par la connexion du carnet.
export function openLink() {
  app.standalone = true;
  $('notice').hidden = true; $('connected').hidden = false;
  openSeance(linkToken(location.hash), false);
}
// Retoucher le bouton dans Messenger ne change que le fragment : la séance se rouvre.
addEventListener('hashchange', () => { if (app.standalone) startSession(linkToken(location.hash)); });

$('seance-close').addEventListener('click', () => {
  if (sessionUnfinished()) present($('leave-sheet'), '#leave-stay');
  else closeSeance();
});
$('leave-confirm').addEventListener('click', () => { dismiss(true); closeSeance(); });
$('seance-sound').addEventListener('click', () => { renderSoundSettings(); present($('sound-sheet')); });
