import {$} from '../shared/dom.js';
import {adopt, loadProgress, save, session, setTimer, state, timer} from './session.js';
import {resolveLink} from './link.js';
import {keepAwake, releaseScreen, stopTicking, timerSeconds} from './timers.js';
import {dropAudio, wakeAudio} from './sound.js';
import {renderSession, rendered} from './render.js';
import {cancelRetry, retry} from './send.js';

/* The interactive strength session.
 *
 * A screen of the notebook since 2026-10-09 (carnet/seance.js opens and closes
 * it). Resolves an opaque capability into its immutable session snapshot, runs it
 * down one exercise at a time, and posts a report back with the same capability.
 *
 * Three things the page is careful about, all learned from the phone it runs on:
 *  - every timer is derived from a TIMESTAMP, never from a tick counter: iOS
 *    freezes intervals in a backgrounded tab, and a set counted by ticks would
 *    come back thirty seconds short;
 *  - every mutation is persisted to localStorage immediately, because the screen
 *    locks between two sets and coming back to a blank page mid-session would
 *    make the whole thing useless;
 *  - nothing is required. Any exercise can be skipped, any figure can be left
 *    empty. A tool you have to fight in the middle of a set is a tool you stop
 *    opening.
 */

// --------------------------------------------------------------------- boot

function fail(title, text) {
  $('boot-title').textContent = title;
  $('boot-msg').textContent = text;
  $('boot').hidden = false;
  $('app').hidden = true;
}

// Le carnet ouvre la séance avec un jeton, et la referme ; `open` dit si elle est à
// l'écran (le son, le verrou d'écran et les gestes ne servent qu'à ce moment).
let onScreen = false;

export async function startSession(token) {
  onScreen = true;
  setTimer(null);
  stopTicking();
  cancelRetry();
  let decoded;
  try {
    decoded = await resolveLink(token);
  } catch (error) {
    if (error && error.message === 'expired') {
      return fail('Lien expiré', 'Cette séance a expiré ou a été révoquée. Redemande-la au coach.');
    }
    if (error && error.message === 'network') {
      return fail('Réseau indisponible',
        'Cette séance n’a pas encore été ouverte sur ce téléphone. Reviens avec du réseau pour la charger une première fois.');
    }
    if (error && error.message === 'forbidden') {
      return fail('Lien invalide', 'Cette séance est inconnue. Redemande-la au coach.');
    }
    return fail('Lien incomplet',
      'Ce lien ne contient pas de séance lisible. Il a probablement été tronqué en chemin : redemande-le au coach.');
  }
  adopt(decoded);
  if (!Number.isInteger(session.expires_at)) {
    return fail('Lien obsolète', 'Ce lien n’a pas de date de validité. Redemande la séance au coach.');
  }
  if (session.expires_at * 1000 < Date.now()) {
    return fail('Lien expiré', 'Cette séance a plus de sept jours. Redemande-la au coach.');
  }
  if (!Array.isArray(session.exercises) || !session.exercises.length) {
    return fail('Séance vide', 'Ce lien ne porte aucun exercice. Redemande-le au coach.');
  }
  loadProgress();
  $('prep').open = !!state.prepOpen;
  $('boot').hidden = true;
  $('app').hidden = false;
  document.title = session.title || 'Renfo';
  // Pas encore commencée, la séance s'ouvre sur son titre ; reprise, sur l'exercice en cours.
  rendered.live = state.startedAt == null ? state.current : -1;
  renderSession();
  keepAwake();
  retry();       // un compte-rendu resté en rade repart à la réouverture de la séance
}

// Fermée, la séance garde tout sur le téléphone (elle reprendra là) ; un compte rendu
// en attente de réseau continue de réessayer.
export function stopSession() {
  onScreen = false;
  if (session && state) save();
  stopTicking();
  releaseScreen();
  window.speechSynthesis?.cancel();
  dropAudio();
}

// Commencée et pas encore envoyée : la quitter demande confirmation.
export const sessionUnfinished = () => !!(onScreen && state && state.startedAt != null && !state.sent && !state.pending);

document.addEventListener('visibilitychange', () => {
  if (!session) return;
  if (!onScreen) { if (!document.hidden) retry(); return; }
  if (document.hidden) {
    state.hiddenAt = Date.now();
    if (timer?.kind === 'prepare') { timer.paused = Math.max(1, timerSeconds()); timer.confirm = true; }
    save();
    window.speechSynthesis?.cancel();
    dropAudio();
    return;
  }
  if (timer?.kind === 'work' && timer.endsAt <= Date.now() && state.hiddenAt < timer.endsAt) timer.confirm = true;
  if (timer?.kind === 'rest' && timer.endsAt <= Date.now()) timer.autoNext = false;
  save();
  keepAwake();   // the lock is dropped whenever the page goes to the background
  renderSession();      // ...and a timer left running has moved on since
  retry();       // ...and the network may have come back while we were away
});

window.addEventListener('online', retry);

/* Tout geste, n'importe où dans la page, réveille le son et l'écran : iOS ne
 * démarre un AudioContext ni n'accorde le verrou d'écran que dans un geste, et
 * le premier « Démarrer » ne doit pas être le seul à compter. En capture, pour
 * passer avant le gestionnaire du bouton qui peut lancer un bip. */
function onGesture() {
  if (!session || !onScreen) return;
  wakeAudio();
  keepAwake();
}
document.addEventListener('click', onGesture, true);
document.addEventListener('touchend', onGesture, true);

$('prep').addEventListener('toggle', () => {
  if (state) { state.prepOpen = $('prep').open; save(); }
});
