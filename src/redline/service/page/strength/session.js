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
export let watchedZones = [];   // [{zone, label}]: the monitored zones, to ask about
export let credentials = null;  // {token}
export let state = null;        // progress, persisted
export let timer = null;        // {kind:'work'|'rest', endsAt|startedAt, paused}
// ------------------------------------------------------------------- model

/* THE SLOTS ARRIVE WALKED OUT. An exercise carries the list of validations it
 * asks for -- one per set, doubled when the dose is per leg, one per BLOCK for
 * ground contacts -- and this file does not derive any of it.
 *
 * The page neither parses the dose string nor rebuilds the slot list from `sets` and
 * `side`: the server computes and sends it (`Dose.slots`), and a client-side copy
 * would know nothing of blocks, so "40 contacts, in blocks of 10, 30 s apart" would
 * be walked as one flat run of 40.
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
  // startedAt stays NULL until something has been done: opening the link is not
  // starting the session. The link often sits on the phone for an hour or two before
  // the first exercise -- stamping at opening would inflate the duration.
  state = (restored && typeof restored === 'object' && restored.steps) ? restored : {
    startedAt: null, performedOn: null, steps: {}, current: 0, review: false,
    // `opened` tells "not answered yet" from "answered 0": without it, a perfectly
    // silent pain would reopen the opening screen on every return to the tab.
    // `pains`: {zone: {before, after}}, one reading before and one after per zone.
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

// Reading a zone at a given moment (`before` | `after`), and writing it.
export const painOf = (slug, moment) => (state.pains[slug] || {})[moment] ?? null;
export function setPain(slug, moment, value) {
  state.pains[slug] = {...(state.pains[slug] || {}), [moment]: value};
}

// What a started session has already done on this phone, read from its saved state:
// the notebook shows it ("3 exercises out of 7") and offers to resume it.
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
