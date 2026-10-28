/* The session being run and its progress: who owns them, how they persist, and
 * the slots an exercise asks for. Other modules read these bindings; only this
 * one assigns them (setTimer, adopt). */
import {$} from '../shared/dom.js';

export const MAX_NOTE = 300;
export const clampInt = (value, low, high, fallback) => {
  const number = parseInt(value, 10);
  return Number.isFinite(number) ? Math.max(low, Math.min(high, number)) : fallback;
};
export const mmss = (seconds) => {
  const whole = Math.max(0, Math.round(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
};

export let session = null;      // resolved from the fragment capability
export let previousSession = null;
export let watchedZones = [];   // [{zone, label}] : les zones surveillées, à demander
export let credentials = null;  // {token}
export let state = null;        // progress, persisted
export let timer = null;        // {kind:'work'|'rest', endsAt|startedAt, paused}
// ------------------------------------------------------------------- model

/* THE SLOTS ARRIVE WALKED OUT. An exercise carries the list of validations it
 * asks for -- one per set, doubled when the dose is per leg, one per BLOCK for
 * ground contacts -- and this file no longer derives any of it.
 *
 * Two deletions, same lesson. Until 2026-07-25 the page re-derived
 * measure/sets/side/seconds from the French dose STRING; the vault took the
 * dose back and that parser went. Until 2026-07-31 it still rebuilt the slot
 * list from `sets` and `side`, while the server computed and sent the very
 * same thing (`Dose.slots`) for nobody to read -- and that second copy knew
 * nothing of blocks, so the plyometrics prescribed as "40 contacts, in blocks
 * of 10, 30 s apart" were walked as one flat run of 40.
 *
 * A slot: {n, of} rank and total (of SETS or BLOCKS, never of slots), `s` the
 * side, `t` what it aims at. */
export const SIDES = { d: 'droite', g: 'gauche' };
export const slotsOf = (index) => session.exercises[index].dose.slots;

export const progressOf = (index) => state.steps[index] ||
  (state.steps[index] = { slots: [], skipped: false, count: 0, score: null });

const slotCount = (index) => slotsOf(index).length;
export const isDone = (index) => {
  const done = state.steps[index];
  return !!done && (done.skipped || done.gated || done.stopped
                    || done.slots.length >= slotCount(index));
};

export function firstUnfinished() {
  for (let i = 0; i < session.exercises.length; i++) if (!isDone(i)) return i;
  return -1;
}

// ------------------------------------------------------------------- state

const storageKey = () => `redline:strength:${session.sid || 'session'}`;

export function save() {
  if (!state) return false;
  state.timer = timer;
  try {
    localStorage.setItem(storageKey(), JSON.stringify(state));
    return true;
  } catch (e) {
    const warning = $('storage-status');
    if (warning) {
      warning.hidden = false;
      warning.textContent = 'La sauvegarde sur ce téléphone est indisponible. Garde la page ouverte jusqu’à l’envoi.';
    }
    return false;
  }
}

export function localDate(timestamp) {
  const value = new Date(timestamp);
  const two = (number) => String(number).padStart(2, '0');
  return `${value.getFullYear()}-${two(value.getMonth() + 1)}-${two(value.getDate())}`;
}

export function loadProgress() {
  let restored = null;
  try { restored = JSON.parse(localStorage.getItem(storageKey()) || 'null'); } catch (e) { /* ignore */ }
  // startedAt reste NULL tant que rien n'a été fait : ouvrir le lien n'est pas
  // commencer la séance. Le lien arrive avec le message du coach et traîne
  // souvent une heure ou deux dans le téléphone avant le premier exercice --
  // horodater à l'ouverture gonflait la durée d'autant.
  state = (restored && typeof restored === 'object' && restored.steps) ? restored : {
    startedAt: null, performedOn: null, steps: {}, current: 0, review: false,
    // `opened` distingue « pas encore répondu » de « répondu 0 » : sans lui, une
    // douleur parfaitement silencieuse rouvrirait l'écran d'ouverture à chaque
    // retour sur l'onglet.
    // `pains` : {zone: {before, after}}, une lecture avant et une après par zone.
    pains: {}, opened: false,
    note: '', minutes: null, sent: false, pending: false,
  };
  if (!state.pains || typeof state.pains !== 'object') state.pains = {};
  state.current = clampInt(state.current, 0, session.exercises.length - 1, 0);
  timer = state.timer || null;
  if (timer && timer.index !== state.current) timer = null;
  if (timer && timer.kind !== 'rest') timer.confirm = true;
}

export function setTimer(value) { timer = value; }

// The resolved link: the session, the previous one of the same type, the token that opened it.
export function adopt(decoded) {
  session = decoded.session;
  previousSession = decoded.previous_session || null;
  watchedZones = Array.isArray(decoded.zones) ? decoded.zones : [];
  credentials = { token: decoded.token };
}

// La lecture d'une zone à un moment (`before` | `after`), et son écriture.
export const painOf = (slug, moment) => (state.pains[slug] || {})[moment] ?? null;
export function setPain(slug, moment, value) {
  state.pains[slug] = {...(state.pains[slug] || {}), [moment]: value};
}

// Ce qu'une séance commencée a déjà fait sur ce téléphone, lu dans sa sauvegarde :
// le carnet l'affiche (« 3 exercices sur 7 ») et propose de la reprendre.
export function savedProgress(snapshot) {
  if (!snapshot || !snapshot.sid) return null;
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(`redline:strength:${snapshot.sid}`) || 'null'); } catch (e) { return null; }
  if (!saved || !saved.steps || saved.startedAt == null || saved.sent) return null;
  const exercises = snapshot.exercises || [];
  const done = exercises.filter((exercise, index) => {
    const step = saved.steps[index];
    return !!step && (step.skipped || step.gated || step.stopped
                      || step.slots.length >= ((exercise.dose || {}).slots || []).length);
  }).length;
  return { startedAt: saved.startedAt, done, total: exercises.length };
}
