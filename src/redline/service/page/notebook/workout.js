// ------------------------------------------------------------ guided session
// Strength opens full screen over the notebook: the notebook bar with a close button on
// the left and sound on the right, the large title of the other screens, no tabs. During
// the session, no swipe back and no pull to refresh: close is the only exit, and a started
// session asks for confirmation, saying nothing is lost. A link received in Messenger
// (/s#t=…) opens the same app directly on the session; close then leads to the notebook.
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

const KEPT = 'notebook-workout';  // the open session, found again if iOS reloads the page
const RISE = 420;  // ms, a bit longer than a pushed screen: the session comes from further
const cover = $('workout');

// The link of today's session: requested from the service, kept to reopen it offline.
export async function launchWorkout(strength) {
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

// The screen being left stays visible underneath while the session rises.
export function openWorkout(token, motion = true) {
  land(); remember();
  const under = motion && !calm.matches ? freeze(true) : null;
  if (under) under.append(document.querySelector('.n-nav').cloneNode(true));
  app.workout = token;
  if (!app.standalone) { try { sessionStorage.setItem(KEPT, token); } catch { /* no storage: no resume on reload */ } }
  document.documentElement.classList.add('n-workout');
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
  if (!app.workout) return;
  if (app.standalone) { stopSession(); location.replace('/carnet'); return; }
  // The descending session is a frozen copy; the notebook is already in place beneath.
  let leaving = null;
  if (!calm.matches) {
    const rect = cover.getBoundingClientRect(), copy = cover.cloneNode(true);
    copy.removeAttribute('id'); copy.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
    copy.style.cssText = 'position:absolute;margin:0;left:' + rect.left + 'px;top:' + rect.top + 'px;width:' + rect.width + 'px';
    leaving = el('div', null, 'n-move n-falling');
    leaving.setAttribute('aria-hidden', 'true');
    leaving.append(copy);
  }
  dropWorkout();
  render(app.scrolls[screen().key] || 0);
  if (leaving) {
    pageNode.after(leaving);
    leaving.animate([{transform: 'none'}, {transform: 'translateY(100%)'}], {duration: RISE * 0.8, easing: EASE})
      .finished.catch(() => {}).finally(() => leaving.remove());
  }
  loadToday().catch(() => {});  // strength may have been sent: Today ticks it
}

// No animation, no return to the notebook: the session is removed (also on sign-out).
export function dropWorkout() {
  if (!app.workout) return;
  stopSession();
  app.workout = null;
  try { sessionStorage.removeItem(KEPT); } catch { /* nothing to forget */ }
  document.documentElement.classList.remove('n-workout');
  cover.hidden = true;
  document.title = 'Mon carnet — Redline';
}

// After iOS reloads the page, the open session comes back as it was.
export function resumeWorkout() {
  let kept = null;
  try { kept = sessionStorage.getItem(KEPT); } catch { return; }
  if (kept) openWorkout(kept, false);
}

// The Messenger link: the session alone, without going through the notebook sign-in.
export function openLink() {
  app.standalone = true;
  $('notice').hidden = true; $('connected').hidden = false;
  openWorkout(linkToken(location.hash), false);
}
// Re-tapping the button in Messenger only changes the fragment: the session reopens.
addEventListener('hashchange', () => { if (app.standalone) startSession(linkToken(location.hash)); });

$('workout-close').addEventListener('click', () => {
  if (sessionUnfinished()) present($('leave-sheet'), '#leave-stay');
  else closeSeance();
});
$('leave-confirm').addEventListener('click', () => { dismiss(true); closeSeance(); });
$('workout-sound').addEventListener('click', () => { renderSoundSettings(); present($('sound-sheet')); });
