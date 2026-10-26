/* Around the exercises: the preparation, the opening shin reading and its gate,
 * the end-of-session review. */
import {$, el} from '../shared/dom.js';
import {MAX_NOTE, clampInt, previousSession, progressOf, save, session, state} from './session.js';
import {undoButton} from './actions.js';
import {btn, feedbackBlock, loadText} from './control.js';
import {renderSession} from './render.js';
import {send} from './send.js';

export function renderPreparation() {
  const box = $('prep-content');
  box.replaceChildren();
  const equipment = [...new Set(session.exercises.filter((exercise, index) => !state.steps[index]?.gated)
    .flatMap(exercise => [...(exercise.equipment || []), ...(exercise.load && exercise.load.kg > 0
      ? [`Haltère(s) : ${loadText(exercise.load)}`] : [])]))];
  box.appendChild(el('p', equipment.length
    ? `Matériel : ${equipment.join(' · ')}` : 'Aucun matériel renseigné pour cette séance.', 'p-review__hint'));
  if (!previousSession) {
    box.appendChild(el('p', 'Pas de prescription précédente comparable disponible.', 'p-review__hint'));
    return;
  }
  box.appendChild(el('p', 'Changements depuis la précédente prescription du même type :', 'p-review__hint'));
  const changes = el('ul', null, 'p-how__list');
  const old = new Map(previousSession.exercises.map(exercise => [exercise.slug, exercise]));
  for (const exercise of session.exercises) {
    const before = old.get(exercise.slug);
    let detail = '';
    if (!before) detail = 'nouveau dans cette séance';
    else {
      const bits = [];
      if (JSON.stringify(before.dose) !== JSON.stringify(exercise.dose)) bits.push(`dose : ${exercise.dose.text}`);
      if (JSON.stringify(before.load || null) !== JSON.stringify(exercise.load || null)) bits.push(`charge : ${loadText(exercise.load) || 'non renseignée'}`);
      if (before.cue !== exercise.cue) bits.push('repère du geste actualisé');
      if (JSON.stringify(before.equipment || []) !== JSON.stringify(exercise.equipment || [])) bits.push('matériel actualisé');
      detail = bits.join(' · ');
    }
    if (detail) changes.appendChild(el('li', `${exercise.name} : ${detail}`, 'p-how__cue'));
  }
  for (const before of previousSession.exercises) {
    if (!session.exercises.some(exercise => exercise.slug === before.slug)) {
      changes.appendChild(el('li', `${before.name} : absent de la prescription du jour`, 'p-how__cue'));
    }
  }
  if (!changes.children.length) changes.appendChild(el('li', 'Prescription identique.', 'p-how__cue'));
  box.appendChild(changes);
}

/* 0-10, repainted IN PLACE: re-rendering the whole review on every tap would
 * yank the cursor out of the note the athlete is typing. */
export function scoreScale(read, write, after) {
  const row = el('div', null, 'p-scale');
  row.setAttribute('role', 'radiogroup');
  const paint = () => {
    const current = read();
    for (const step of row.children) {
      step.setAttribute('aria-checked', String(+step.textContent === current));
    }
  };
  for (let value = 0; value <= 10; value++) {
    const step = el('button', String(value), 'p-scale__step p-num');
    step.type = 'button';
    step.setAttribute('role', 'radio');
    step.addEventListener('click', () => {
      write(read() === value ? null : value);
      save();
      paint();
      if (after) after();   // l'écran d'ouverture se referme sur le choix
    });
    row.appendChild(step);
  }
  paint();
  return row;
}

/* L'OUVERTURE. Le injury se demandait une fois, à la FIN — un chiffre sans
 * référence : « 2/10 après » ne dit rien si on ignore ce qu'il valait avant. Le
 * registre sépare d'ailleurs depuis le 2026-07-29 deux séries qui ne se
 * mélangent pas, le pic à l'effort et la lecture au repos ; la page n'en
 * fournissait qu'une.
 *
 * Et le coach a beau avoir demandé le matin, le lien arrive avec son message et
 * traîne souvent une heure ou deux dans le téléphone : l'état de 8 h n'est pas
 * celui de 18 h. C'est cette lecture-là qui arme le garde-fou.
 *
 * Sautable, comme tout le reste ici : un outil contre lequel il faut lutter est
 * un outil qu'on cesse d'ouvrir. */
export function renderOpening() {
  const box = $('open');
  box.hidden = false;
  box.replaceChildren();
  box.appendChild(el('h2', 'Le injury, là, avant de commencer', 'p-open__title'));
  box.appendChild(el('p', '0 = rien du tout, 10 = ça fait mal en marchant. Trois secondes, et le '
    + 'chiffre de la fin devient lisible : sans celui-ci, il n\'a rien à quoi '
    + 'se comparer.', 'p-open__hint'));
  box.appendChild(scoreScale(() => state.shinBefore, (value) => {
    state.shinBefore = value;
    state.opened = value != null;
  }, () => { save(); renderSession(); }));
  const actions = el('div', null, 'p-actions');
  actions.appendChild(btn('Je passe', () => {
    state.opened = true;
    state.shinBefore = null;
    save();
    renderSession();
  }, 'p-btn--quiet'));
  box.appendChild(actions);
}

/* Le garde-fou : une DONNÉE écrite par le coach le matin, jamais une règle que
 * la page invente. Elle lit un seuil, une liste et une phrase, et n'interprète
 * rien -- `strength.md` dit « si le injury est >2/10 : version dégradée de A, B
 * annulée », mais ce que ça veut dire aujourd'hui appartient au coach. */
export const gateFired = () => !!(session.gate && state.shinBefore != null
                           && state.shinBefore > session.gate.shin_max);

export function applyGate() {
  if (!gateFired()) return;
  for (const [index, exercise] of session.exercises.entries()) {
    if ((session.gate.drop || []).includes(exercise.slug)) {
      const progress = progressOf(index);
      if (!progress.slots.length) progress.gated = true;
    }
  }
  save();
}

export function renderReview() {
  const box = $('review');
  box.hidden = false;
  box.replaceChildren();
  box.appendChild(el('h2', 'Séance terminée', 'p-review__title'));
  // Not a performance mark and not a pain score: how HARD the exercise was to
  // do properly. Saying only "note de 0 à 10" is what made the scale unreadable.
  box.appendChild(el('p', 'Pour chaque exercice : à quel point c\'était dur de le faire correctement ? '
    + '0 = facile, 10 = trop dur ou ça tirait. Sous 6 on durcit la semaine prochaine, '
    + '6-7 on garde le palier, au-dessus de 7 on redescend d\'un cran.', 'p-review__hint'));

  session.exercises.forEach((exercise, index) => {
    const progress = state.steps[index];
    if (!progress || progress.skipped || progress.gated || !progress.slots.length) return;
    const group = el('div', null, 'p-score');
    group.appendChild(feedbackBlock(index));
    box.appendChild(group);
  });

  const shin = el('div', null, 'p-score');
  shin.appendChild(el('p', 'Injury, maintenant', 'p-score__name'));
  shin.appendChild(scoreScale(() => state.shin, (value) => { state.shin = value; }));
  box.appendChild(shin);

  // Séance entièrement passée : aucun geste, donc aucune durée à proposer. Le
  // champ reste vide plutôt que d'inventer un chiffre.
  if (state.minutes == null && state.startedAt != null) {
    state.minutes = Math.max(1, Math.round((Date.now() - state.startedAt) / 60000));
    save();
  }
  const duration = el('div', null, 'p-score');
  duration.appendChild(el('p', 'Durée (min)', 'p-score__name'));
  const minutes = el('input', null, 'p-input p-num');
  minutes.type = 'number';
  minutes.inputMode = 'numeric';
  minutes.value = state.minutes == null ? '' : state.minutes;
  minutes.addEventListener('change', () => {
    state.minutes = clampInt(minutes.value, 0, 300, null);
    save();
  });
  duration.appendChild(minutes);
  box.appendChild(duration);

  const note = el('div', null, 'p-score');
  note.appendChild(el('p', 'À dire au coach (facultatif)', 'p-score__name'));
  const field = el('textarea', null, 'p-input');
  field.maxLength = MAX_NOTE;
  field.value = state.note || '';
  field.addEventListener('input', () => { state.note = field.value; save(); });
  note.appendChild(field);
  box.appendChild(note);

  const actions = el('div', null, 'p-actions');
  if (!state.sent) {
    actions.appendChild(btn('Envoyer au coach', send, 'p-btn--wide'));
    if (state.undo) actions.appendChild(undoButton());
  } else {
    actions.appendChild(btn('Ajouter une correction', () => {
      const text = `Correction du compte-rendu ${session.title} (${state.performedOn}) : `;
      const correction = el('textarea', null, 'p-input');
      correction.value = text;
      correction.setAttribute('aria-label', 'Correction à transmettre au coach');
      box.appendChild(el('p', 'Complète puis copie ce texte dans Messenger pour le transmettre au coach.', 'p-review__hint'));
      box.appendChild(correction);
      correction.focus();
      correction.setSelectionRange(text.length, text.length);
    }, 'p-btn--wide'));
  }
  box.appendChild(actions);
  const status = el('p', null, 'p-status');
  status.id = 'status';
  if (state.sent) status.textContent = state.duplicate
    ? 'Déjà reçu pour ce lien. Aucun nouvel enregistrement.' : 'Envoyé. Le coach a la séance.';
  else if (state.pending) {
    status.textContent = 'En attente de réseau. C\'est gardé, ça repartira tout seul.';
  }
  box.appendChild(status);
  if (state.sent || state.pending) {
    for (const field of box.querySelectorAll('input, textarea, .p-scale__step')) field.disabled = true;
  }
}
