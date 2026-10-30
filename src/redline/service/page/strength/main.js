import {$} from '../shared/dom.js';
import {adopt, loadProgress, save, session, setTimer, state, timer} from './session.js';
import {resolveLink} from './link.js';
import {keepAwake, releaseScreen, stopTicking, timerSeconds} from './timers.js';
import {dropAudio, wakeAudio} from './sound.js';
import {renderSession, rendered} from './render.js';
import {cancelRetry, retry} from './send.js';

/* The interactive strength session.
 *
 * A screen of the notebook (notebook/workout.js opens and closes
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

// The notebook opens the session with a token, and closes it; `open` says whether it is
// on screen (sound, screen lock and gestures only matter then).
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
  // Not yet started, the session opens on its title; resumed, on the current exercise.
  rendered.live = state.startedAt == null ? state.current : -1;
  renderSession();
  keepAwake();
  retry();       // a report left pending is retried when the session reopens
}

// Closed, the session keeps everything on the phone (it resumes there); a report waiting
// for the network keeps retrying.
export function stopSession() {
  onScreen = false;
  if (session && state) save();
  stopTicking();
  releaseScreen();
  window.speechSynthesis?.cancel();
  dropAudio();
}

// Started and not yet sent: leaving it asks for confirmation.
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

/* Any gesture, anywhere on the page, wakes the sound and the screen: iOS only starts an
 * AudioContext or grants the screen lock inside a gesture, and the first "Démarrer" must
 * not be the only one that counts. Captured, to run before the button handler that may
 * play a beep. */
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
