import {$} from '../shared/dom.js';
import {SIDES, mmss, progressOf, save, setTimer, slotsOf, state, timer} from './session.js';
import {COUNTDOWN_BEEP, announce, beep, playSignal} from './sound.js';
import {markStarted, validateSlot} from './actions.js';
import {renderSession} from './render.js';

// ------------------------------------------------------------------ timers

let tickId = 0;

export function startTimer(kind, seconds) {
  setTimer(seconds > 0
    ? { kind, index: state.current, endsAt: Date.now() + seconds * 1000, total: seconds, paused: null }
    : { kind, startedAt: Date.now() });   // no duration given: count up instead
  save();
  tickTimer();
}

export const stopTimer = () => { setTimer(null); save(); };

export function prepareWork(seconds, changingSide = false) {
  startTimer('prepare', 5);
  timer.workSeconds = seconds;
  if (changingSide) { playSignal('side'); announce('Change de côté'); }
  save();
  renderSession();
}

function beginWork(seconds) {
  markStarted();
  state.feedback = null;
  startTimer('work', seconds);
  playSignal('start');
  const slot = slotsOf(state.current)[progressOf(state.current).slots.length];
  if (slot?.s) announce(SIDES[slot.s]);
  renderSession();
}

export function timerSeconds() {
  if (!timer) return 0;
  if (timer.startedAt) return (Date.now() - timer.startedAt) / 1000;
  if (timer.paused != null) return timer.paused;
  return (timer.endsAt - Date.now()) / 1000;
}

export function tickTimer() {
  if (tickId) return;
  tickId = window.setInterval(() => {
    if (!timer) { window.clearInterval(tickId); tickId = 0; return; }
    if (timer.confirm) return;
    if (timer.endsAt && timer.paused == null) {
      const left = timerSeconds();
      if (left <= 0) {
        if (document.hidden && timer.kind !== 'rest') {
          timer.confirm = true;
          save();
          return;
        }
        // A countdown that runs out validates on its own: hands-free is the whole
        // point of a 45 s isometric.
        if (timer.kind === 'prepare') { beginWork(timer.workSeconds); return; }
        const autoNext = timer.autoNext && state.autoCurrent === state.current;
        playSignal(timer.kind === 'rest' ? 'restEnd' : 'workEnd');
        if (timer.kind === 'work') { validateSlot(null); return; }
        stopTimer();
        const editing = ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName);
        if (autoNext && !document.hidden && !editing) {
          const next = slotsOf(state.current)[progressOf(state.current).slots.length];
          if (next) { prepareWork(next.t); return; }
        }
        renderSession(); return;
      }
      // Les trois derniers secondes s'annoncent. L'intervalle bat à 250 ms : sans
      // ce garde, chaque seconde sonnerait quatre fois.
      const second = Math.ceil(left);
      if (second !== timer.spoken) {
        timer.spoken = second;
        if (second <= 3) beep(COUNTDOWN_BEEP, 0.22);
      }
    }
    paintTimer();
  }, 250);
}

export function stopTicking() {
  if (tickId) clearInterval(tickId);
  tickId = 0;
}

function paintTimer() {
  const clock = $('figure');
  if (clock && timer) clock.firstChild.textContent = mmss(timerSeconds());
}

/* L'écran reste allumé de l'ouverture à l'envoi. Une seule demande au chargement
 * ne tenait pas : iOS la refuse hors d'un geste, et le verrou tombe dès que la page
 * passe en arrière-plan. Sur les répétitions on tape l'écran, ce qui repousse la
 * veille ; sur un chrono on ne touche rien, et l'iPhone s'éteignait. D'où : on
 * garde le verrou, et TOUT geste le redemande tant qu'on ne le tient pas. */
let screenLock = null;
let screenLockPending = false;
export async function keepAwake() {
  if ((screenLock && !screenLock.released) || screenLockPending) return;
  if (!navigator.wakeLock || document.hidden) return;
  screenLockPending = true;
  try {
    screenLock = await navigator.wakeLock.request('screen');
    screenLock.addEventListener('release', () => { screenLock = null; });
  } catch (e) { screenLock = null; /* refusé : le prochain geste réessaie */ }
  screenLockPending = false;
}

// La séance fermée, l'écran peut de nouveau se mettre en veille.
export function releaseScreen() {
  screenLock?.release().catch(() => {});
  screenLock = null;
}
