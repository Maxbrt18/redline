/* One render for the whole page, from the session and its persisted state. */
import {$, el} from '../shared/dom.js';
import {isDone, progressOf, session, state, timer} from './session.js';
import {tickTimer} from './timers.js';
import {paintSound} from './sound.js';
import {advance} from './actions.js';
import {control, stepHead} from './control.js';
import {applyGate, gateFired, renderOpening, renderPreparation, renderReview} from './review.js';

// "Vendredi 9 octobre", as under the notebook's titles; an unreadable date is left as is.
function inWords(date) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date || '')) return date;
  const words = new Date(date + 'T12:00:00').toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' });
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export const rendered = { live: -1 };   // which exercise the last render put on screen
export function renderSession() {
  if (state.sent || state.pending) state.review = true;
  paintSound();
  $('title').textContent = session.title || `Renfo ${session.type || ''}`.trim();
  const meta = [inWords(session.date), session.duration_min ? `~${session.duration_min} min` : '']
    .filter(Boolean).join(' · ');
  $('meta').textContent = meta;
  const note = $('note');
  note.textContent = session.note || '';
  note.hidden = !session.note;
  $('rule').textContent = session.rule || '';

  // The opening comes first: until it is answered or skipped there is no session to
  // run -- it is what arms the safeguard.
  const opening = !!session.gate && !state.opened;
  $('open').hidden = !opening;
  if (opening) {
    renderPreparation();
    renderOpening();
    $('steps').replaceChildren();
    $('review').hidden = true;
    return;
  }
  applyGate();
  renderPreparation();
  if (progressOf(state.current).gated && !state.review && !state.viewingRetired) advance();
  // Its OWN line, not the session note's: writing it into `#note` would silently
  // erase what the coach wrote in the morning.
  const gate = $('gate');
  gate.textContent = gateFired() ? session.gate.say : '';
  gate.hidden = !gateFired();

  const list = $('steps');
  const wasLive = rendered.live;
  list.replaceChildren();
  let liveNode = null;
  session.exercises.forEach((exercise, index) => {
    const live = !state.review && index === state.current;
    const done = isDone(index);
    const item = el('li', null, `p-step${live ? ' p-step--live' : ''}${done && !live ? ' p-step--done' : ''}`);
    item.appendChild(stepHead(index, exercise, live, done));
    if (live) {
      liveNode = item;
      const ctrl = control(index);
      if (ctrl) item.appendChild(ctrl);
    }
    list.appendChild(item);
  });
  // Moving on brings the new exercise under the thumb; re-rendering the same
  // one (a timer tick, a counter tap) must not yank the page around.
  rendered.live = state.review ? -1 : state.current;
  if (liveNode && wasLive !== rendered.live) {
    liveNode.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  if (state.review) renderReview();
  else $('review').hidden = true;
  if (timer) tickTimer();
}
