import {button, el} from '../shared/dom.js';
import {MAX_NOTE, SIDES, mmss, progressOf, save, session, slotsOf, state, timer} from './session.js';
import {prepareWork, stopTimer, tickTimer, timerSeconds} from './timers.js';
import {advance, focusStep, markStarted, redoStep, skipStep, undoButton, validateSlot} from './actions.js';
import {scoreScale} from './review.js';
import {renderSession} from './render.js';

/* The exercise list and the live control: what to do now, and the one button forward. */

export function stepHead(index, exercise, live, done) {
  const head = el(live ? 'div' : 'button', null, 'p-step__head');
  if (!live) {
    head.type = 'button';
    head.addEventListener('click', () => focusStep(index));
  }
  head.appendChild(el('span', String(index + 1).padStart(2, '0'), 'p-step__index p-num'));

  /* ONE column, not two. A dose in a right-hand cell breaks on a 375 px screen
   * ("3 min" wraps onto two lines, "40 contacts, in blocks of 10" onto three, next to
   * a name that already takes three). Two columns competing for a thumb's width cannot
   * win; under the name, the dose has the whole line and nothing to arbitrate. */
  const main = el('div', null, 'p-step__main');
  main.appendChild(el('p', exercise.name || '—', 'p-step__name'));
  // The target is context: it serves during the exercise, not in the list.
  if (live && exercise.target) main.appendChild(el('p', exercise.target, 'p-step__sub'));
  if (!live) {
    // A finished exercise stops showing what was prescribed and shows what was
    // actually done: that is the only number worth reading on the way back up.
    const progress = state.steps[index];
    let dose = exercise.dose.text;
    if (done && progress) {
      const banked = progress.slots.filter((slot) => slot != null);
      // "Removed" is not "not done": one is a coach decision the page applies, the
      // other an athlete's choice. Conflating them would make the coach read a
      // non-adherence where their own rule applied.
      if (progress.gated) dose = 'retiré — garde-fou';
      else if (progress.skipped) dose = 'pas fait';
      else if (!banked.length) { /* nothing counted: the dose stays */ }
      else if (exercise.dose.measure === 'contacts') {
        // Four blocks of ten read as "40 contacts", not "10 · 10 · 10 · 10".
        dose = `${banked.reduce((total, value) => total + value, 0)} contacts`;
      } else dose = banked.join(' · ');
      if (progress.stopped) dose = `partiel · ${dose}`;
    }
    main.appendChild(el('p', dose, 'p-step__dose p-num'));
  }
  head.appendChild(main);
  return head;
}

/* Where you are inside the exercise. Blocks say "Bloc", sets say "Série": they
 * are not the same unit and reading "Série 3/4" during plyometrics would send
 * you looking for a set that does not exist. */
const unit = (measure) => (measure === 'contacts' ? 'Bloc' : 'Série');

function slotLabel(slot, measure) {
  const parts = [];
  if (slot.of > 1) parts.push(`${unit(measure)} ${slot.n}/${slot.of}`);
  if (slot.s) parts.push(SIDES[slot.s]);
  return parts.join(' · ') || 'À faire';
}

/* ONE forward action, and it says where it goes.
 *
 * There used to be three buttons -- "Validé", "Repos", "Passer" -- and next to
 * "Validé" the "Repos" one read as if it might validate too. It did not. With
 * sets multiplied by sides, that was three ambiguous controls on top of an
 * already busy state. The rest is prescribed by the session anyway, so the
 * forward button owns it: it banks the set, runs the rest if there is one, and
 * lands on whatever comes next. */
function forwardLabel(index) {
  const progress = progressOf(index);
  const slots = slotsOf(index);
  const done = progress.slots.length;
  const next = slots[done + 1];
  if (!next) {
    return index === session.exercises.length - 1 ? 'Terminer' : 'Exercice suivant';
  }
  if (next.n === slots[done].n) return `Puis la ${SIDES[next.s]}`;
  const measure = session.exercises[index].dose.measure;
  return `${unit(measure)} ${next.n}${next.s ? ' · ' + SIDES[next.s] : ''}`;
}

export function bigFigure(value, unit, dim) {
  const node = el('p', null, `p-figure${dim ? ' p-figure--muted' : ''} p-num`);
  node.id = 'figure';
  node.appendChild(document.createTextNode(value));
  if (unit) node.appendChild(el('span', ` ${unit}`, 'p-figure__unit'));
  return node;
}

export function btn(label, onClick, cls) {
  return button(label, onClick, `p-btn${cls ? ' ' + cls : ''}`);
}

/* The rest names what it is a rest BEFORE, and clears itself at zero. Nothing
 * to validate here: the set was banked by the button that started it. */
function restControl(index) {
  const box = el('div', null, 'p-ctrl');
  box.appendChild(el('p', 'Repos', 'p-ctrl__label'));
  box.appendChild(bigFigure(mmss(timerSeconds()), null, true));
  const next = slotsOf(index)[progressOf(index).slots.length];
  const exercise = session.exercises[index];
  const measure = exercise.dose.measure;
  if (next) {
    box.appendChild(el('p', `Ensuite : ${exercise.name} · ${slotLabel(next, measure)}`, 'p-ctrl__value'));
    appendPrescription(box, exercise, next);
    const cues = cuesFor(exercise);
    if (cues.length) box.appendChild(howBlock(cues));
    appendLast(box, exercise);
  }
  if (state.feedback != null) box.appendChild(feedbackBlock(state.feedback));
  const actions = el('div', null, 'p-actions');
  actions.appendChild(btn('Reprendre', () => { stopTimer(); renderSession(); }, 'p-btn--wide'));
  if (state.undo) actions.appendChild(undoButton());
  box.appendChild(actions);
  return box;
}

/* THE MOVEMENT CUE, folded. Execution cues have a typed field (`how`) and a button:
 * nothing on screen until asked, everything under the thumb between two sets when asked.
 *
 * They arrive through the PAGE (`window.REDLINE_HOW`, by slug), not through the link: the
 * link fragment cannot carry both the cue and the "last time" line within its size
 * limit. See `redline/service/page/__init__.py`.
 *
 * It is a DISCLOSURE, not an action: its place is with what the exercise says about
 * itself, above the buttons -- tapping at the bottom to reveal text at the top does not
 * read well. The state is global: whoever wants the cues wants them on every exercise.
 *
 * The WHY is not here: it lives in the note body, which no code reads. */
const cuesFor = (exercise) => (window.REDLINE_HOW || {})[exercise.slug] || [];

function howBlock(cues) {
  const box = el('div', null, 'p-how');
  const open = !!state.how;
  box.appendChild(btn(open ? 'Le geste ▾' : 'Le geste ▸', () => {
    state.how = !open;
    save();
    renderSession();
  }, 'p-how__toggle'));
  if (open) {
    const list = el('ul', null, 'p-how__list');
    for (const cue of cues) list.appendChild(el('li', cue, 'p-how__cue'));
    box.appendChild(list);
  }
  return box;
}

export function control(index) {
  const exercise = session.exercises[index];
  const progress = progressOf(index);
  if (progress.gated) {
    const box = el('div', null, 'p-ctrl');
    box.appendChild(el('p', 'Retiré par le garde-fou de douleur', 'p-ctrl__label'));
    box.appendChild(el('p', session.gate.say, 'p-ctrl__value'));
    const cues = cuesFor(exercise);
    if (cues.length) box.appendChild(howBlock(cues));
    box.appendChild(btn('Revenir à la séance', () => { advance(); renderSession(); }, 'p-btn--wide'));
    return box;
  }
  const slots = slotsOf(index);
  const slot = slots[progress.slots.length];
  if (!slot || progress.skipped || progress.stopped) {
    // Tapped back onto something already finished: the only useful offer is to
    // wipe it and do it again.
    const done = el('div', null, 'p-ctrl');
    done.appendChild(el('p', progress.skipped ? 'Pas fait' : progress.stopped ? 'Partiel' : 'Terminé', 'p-ctrl__label'));
    const actions = el('div', null, 'p-actions');
    actions.appendChild(btn('Refaire', () => redoStep(index)));
    if (progress.stopped) actions.appendChild(btn('Reprendre l’exercice', () => {
      progress.stopped = false;
      save(); renderSession();
    }));
    actions.appendChild(btn('Revenir à la séance', () => { advance(); renderSession(); }, 'p-btn--quiet'));
    if (state.undo) actions.appendChild(undoButton());
    done.appendChild(actions);
    return done;
  }
  if (timer && timer.kind === 'rest') return restControl(index);
  if (timer && timer.confirm) {
    const box = el('div', null, 'p-ctrl');
    box.appendChild(el('p', 'Reprise après interruption', 'p-ctrl__label'));
    box.appendChild(bigFigure(mmss(timerSeconds()), null, true));
    box.appendChild(el('p', 'Confirme ce que tu as fait avant de poursuivre.', 'p-review__hint'));
    if (timer.kind === 'work') box.appendChild(btn('Série réalisée', () => validateSlot(null), 'p-btn--wide'));
    box.appendChild(btn('Reprendre le chrono', () => {
      timer.endsAt = Date.now() + Math.max(1, timerSeconds()) * 1000;
      timer.confirm = false;
      timer.paused = null;
      save(); renderSession(); tickTimer();
    }, 'p-btn--quiet'));
    box.appendChild(btn('Recommencer le chrono', () => { stopTimer(); renderSession(); }, 'p-btn--quiet'));
    return box;
  }
  if (timer && timer.kind === 'prepare') {
    const box = el('div', null, 'p-ctrl');
    box.appendChild(el('p', 'Prépare-toi', 'p-ctrl__label'));
    appendPrescription(box, exercise, slot);
    box.appendChild(bigFigure(mmss(timerSeconds()), null, false));
    box.appendChild(btn('Annuler le départ', () => { stopTimer(); renderSession(); }, 'p-btn--quiet'));
    return box;
  }

  const box = el('div', null, 'p-ctrl');
  const dose = exercise.dose;
  const mode = dose.measure;
  box.appendChild(el('p', slotLabel(slot, mode), 'p-ctrl__label'));
  appendPrescription(box, exercise, slot);
  const actions = el('div', null, 'p-actions');
  const forward = (value) => btn(forwardLabel(index), () => validateSlot(value()),
                                    'p-btn--wide');

  if (mode === 'time') {
    const seconds = slot.t || 60;
    const running = timer && timer.kind === 'work';
    box.appendChild(bigFigure(mmss(running ? timerSeconds() : seconds), null, !running));
    if (!running) {
      actions.appendChild(btn('Démarrer', () => {
        state.autoCurrent = state.autoEnabled ? index : null;
        prepareWork(seconds);
      }, 'p-btn--wide'));
      const auto = el('label', null, 'p-auto');
      const check = el('input');
      check.type = 'checkbox'; check.checked = !!state.autoEnabled;
      check.addEventListener('change', () => { state.autoEnabled = check.checked; save(); });
      auto.appendChild(check);
      auto.appendChild(document.createTextNode('Enchaîner les séries de cet exercice'));
      box.appendChild(auto);
    } else if (timer.paused != null) {
      actions.appendChild(btn('Reprendre', () => {
        timer.endsAt = Date.now() + timer.paused * 1000;
        timer.paused = null;
        save();
        renderSession();
      }, 'p-btn--wide'));
    } else {
      actions.appendChild(btn('Pause', () => { timer.paused = timerSeconds(); save(); renderSession(); },
                                 'p-btn--wide'));
    }
    // The countdown validates on its own at zero; this is for the times you
    // did it without the clock.
    actions.appendChild(forward(() => null));
  } else if (mode === 'contacts') {
    const target = slot.t || 40;
    box.appendChild(bigFigure(`${progress.count || 0}`, `/ ${target} contacts`, false));
    const counter = el('div', null, 'p-count');
    const bump = (step) => () => {
      markStarted();
      progress.count = Math.max(0, Math.min(999, (progress.count || 0) + step));
      progress.otherCount = true;
      save();
      renderSession();
    };
    counter.appendChild(btn('−1', bump(-1)));
    counter.appendChild(btn('+1', bump(1)));
    counter.appendChild(btn('+10', bump(10)));
    if (progress.otherCount) box.appendChild(counter);
    if (!progress.otherCount) {
      actions.appendChild(btn(`${target} contacts faits → repos`, () => validateSlot(target), 'p-btn--wide'));
      actions.appendChild(btn('Autre nombre', () => { progress.otherCount = true; save(); renderSession(); }, 'p-btn--quiet'));
    } else actions.appendChild(forward(() => progress.count || null));
  } else if (mode === 'amrap') {
    box.appendChild(el('p', dose.text, 'p-ctrl__value'));
    const input = el('input', null, 'p-input p-num');
    input.type = 'number';
    input.inputMode = 'numeric';
    input.min = '0';
    input.placeholder = 'répétitions';
    input.id = 'amrap';
    input.setAttribute('aria-label', 'Répétitions réalisées');
    input.value = progress.draft || '';
    input.addEventListener('input', () => { progress.draft = input.value; save(); });
    box.appendChild(input);
    actions.appendChild(forward(() => (input.value === '' ? null : input.value)));
  } else {
    box.appendChild(el('p', dose.text, 'p-ctrl__value'));
    actions.appendChild(forward(() => null));
  }

  actions.appendChild(btn(progress.slots.length ? 'Arrêter ici — garder les séries faites' : 'Passer l\'exercice', skipStep, 'p-btn--quiet'));
  if (state.undo) actions.appendChild(undoButton());

  /* What was done last time, right before deciding what to do today. Progression
   * hinges on "more than last time, or not". One line, already formatted on the
   * vault side -- not a curve: permanent numeric tracking is not wanted here. */
  const cues = cuesFor(exercise);
  if (cues.length) box.appendChild(howBlock(cues));

  // The action stays close to the timer; going back to the previous exercise remains
  // available without pushing the start button off screen.
  box.appendChild(actions);
  appendLast(box, exercise);
  if (state.feedback != null) {
    const feedback = el('details', null, 'p-feedback-disclosure');
    feedback.appendChild(el('summary', `Noter : ${session.exercises[state.feedback].name}`, ''));
    feedback.appendChild(feedbackBlock(state.feedback));
    box.appendChild(feedback);
  }

  const banked = progress.slots.filter((value) => value != null);
  if (banked.length) box.appendChild(el('p', `Déjà fait : ${banked.join(' · ')}`, 'p-tally'));
  return box;
}

export function loadText(weight) {
  if (!weight) return '';
  return weight.kg === 0 ? 'Poids du corps' : `${weight.count} × ${weight.kg} kg`;
}

function appendPrescription(box, exercise, slot) {
  const mode = exercise.dose.measure;
  const amount = exercise.dose.current_text || (mode === 'reps' ? `${slot.t} répétitions`
    : mode === 'time' ? `${slot.t} s`
    : mode === 'contacts' ? `${slot.t} contacts` : 'Jusqu’à échec technique');
  box.appendChild(el('p', `${slot.s ? SIDES[slot.s].toUpperCase() + ' · ' : ''}${amount}`, 'p-current'));
  if (exercise.load) box.appendChild(el('p', `Charge prévue : ${loadText(exercise.load)}`, 'p-setup'));
  if ((exercise.equipment || []).length) {
    box.appendChild(el('p', `À préparer : ${exercise.equipment.join(' · ')}`, 'p-setup'));
  }
  if (exercise.cue) box.appendChild(el('p', exercise.cue, 'p-cue'));
}

function appendLast(box, exercise) {
  if (!exercise.last) return;
  const last = el('div', null, 'p-last');
  last.appendChild(el('p', 'Réalisé la dernière fois', 'p-last__label'));
  last.appendChild(el('p', exercise.last, 'p-last__value p-num'));
  box.appendChild(last);
}

export function feedbackBlock(index) {
  const exercise = session.exercises[index];
  const progress = progressOf(index);
  const box = el('div', null, 'p-feedback');
  box.appendChild(el('p', exercise.name, 'p-score__name'));
  box.appendChild(el('p', 'Difficulté ressentie · facile ← → trop dur · facultatif', 'p-score__legend'));
  const scores = scoreScale(() => progress.score, (value) => { progress.score = value; });
  scores.setAttribute('aria-label', `Difficulté ressentie : ${exercise.name}`);
  box.appendChild(scores);
  const note = el('textarea', null, 'p-input p-exercise-note');
  note.maxLength = MAX_NOTE;
  note.placeholder = 'Une remarque sur cet exercice ?';
  note.setAttribute('aria-label', `Remarque sur ${exercise.name}`);
  note.value = progress.note || '';
  note.addEventListener('input', () => { progress.note = note.value; save(); });
  box.appendChild(note);
  return box;
}
