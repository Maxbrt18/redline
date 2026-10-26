import {clampInt, firstUnfinished, localDate, progressOf, save, session, slotsOf, state, timer} from './session.js';
import {prepareWork, startTimer, stopTimer} from './timers.js';
import {btn} from './control.js';
import {renderSession} from './render.js';

// ------------------------------------------------------------------ actions

/* Le chrono de la séance démarre au premier geste réel -- lancer un
 * isométrique, compter un contact, valider une série -- et jamais avant. */
export function markStarted() {
  if (state.startedAt == null) {
    state.startedAt = Date.now();
    state.performedOn = localDate(state.startedAt);
    save();
  }
}

export function validateSlot(value) {
  const index = state.current;
  const progress = progressOf(index);
  if (state.sent || state.pending || progress.gated || progress.skipped || progress.stopped) return;
  const slots = slotsOf(index);
  if (progress.slots.length >= slots.length) return;
  markStarted();
  rememberAction(index);
  progress.slots.push(value == null ? null : clampInt(value, 0, 999, null));
  progress.draft = '';
  // Le compteur de contacts est celui du BLOC en cours : une fois le bloc
  // banqué, le suivant repart de zéro.
  progress.count = 0;
  progress.otherCount = false;
  stopTimer();
  save();

  const exercise = session.exercises[index];
  const finished = progress.slots.length >= slots.length;
  const rest = exercise.dose.rest_s || 0;
  // Le repos sépare les SÉRIES (et les BLOCS), pas les côtés : sur un exercice
  // par jambe une série est « droite puis gauche », et le repos ne tombe
  // qu'une fois les deux faites. Après la dernière il reste l'exercice suivant
  // à rejoindre -- sauf si c'est le dernier de la séance.
  const justDone = slots[progress.slots.length - 1];
  const next = slots[progress.slots.length];
  const sameSet = !!next && next.n === justDone.n;
  const autoNext = state.autoCurrent === index && !finished;
  if (rest && !sameSet && !(finished && index === session.exercises.length - 1)) {
    startTimer('rest', rest);
    timer.autoNext = autoNext;
  }
  if (autoNext && !timer) prepareWork(next.t, sameSet);
  if (finished) advance();
  if (finished) state.autoCurrent = null;
  if (finished) state.feedback = index;
  if (timer) timer.index = state.current;
  save();
  renderSession();
}

export function advance() {
  state.viewingRetired = false;
  const next = firstUnfinished();
  if (next < 0) { state.review = true; stopTimer(); }
  else state.current = next;
  save();
}

export function skipStep() {
  state.autoCurrent = null;
  rememberAction(state.current);
  const progress = progressOf(state.current);
  if (progress.slots.length) progress.stopped = true;
  else progress.skipped = true;
  stopTimer();
  advance();
  if (progress.stopped) state.feedback = state.undo.index;
  renderSession();
}

export function focusStep(index) {
  if (state.sent || state.pending) return;
  state.current = index;
  state.autoCurrent = null;
  state.viewingRetired = !!progressOf(index).gated;
  state.review = false;
  stopTimer();
  save();
  renderSession();
}

export function redoStep(index) {
  if (progressOf(index).gated || state.sent || state.pending) return;
  rememberAction(index);
  state.steps[index] = { slots: [], skipped: false, count: 0, score: null };
  state.review = false;
  save();
  renderSession();
}

function rememberAction(index) {
  state.undo = { index, progress: JSON.parse(JSON.stringify(progressOf(index))),
    review: state.review, minutes: state.minutes };
}

function undoAction() {
  if (!state.undo || state.sent || state.pending) return;
  const undo = state.undo;
  stopTimer();
  state.autoCurrent = null;
  state.steps[undo.index] = undo.progress;
  state.current = undo.index;
  state.review = false;
  state.minutes = undo.minutes;
  state.undo = null;
  state.feedback = null;
  save();
  renderSession();
}

export function undoButton() {
  return btn('Annuler la dernière validation', undoAction, 'p-btn--quiet');
}
