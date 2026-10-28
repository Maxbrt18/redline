/* One render for the whole page, from the session and its persisted state. */
import {$, el} from '../shared/dom.js';
import {isDone, progressOf, session, state, timer} from './session.js';
import {tickTimer} from './timers.js';
import {paintSound} from './sound.js';
import {advance} from './actions.js';
import {control, stepHead} from './control.js';
import {applyGate, gateFired, renderOpening, renderPreparation, renderReview} from './review.js';

// « Vendredi 9 octobre », comme sous les titres du carnet ; une date illisible reste telle quelle.
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

  // L'ouverture passe avant tout : tant qu'elle n'est pas répondue ou passée, il
  // n'y a pas de séance à dérouler -- c'est elle qui arme le garde-fou.
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
  // Sa PROPRE ligne, pas celle de la note de séance : l'écrire dans `#note`
  // effaçait en silence ce que le coach avait écrit le matin, exactement le
  // jour où il a le plus de choses à dire.
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
