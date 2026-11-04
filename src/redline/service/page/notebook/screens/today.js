import {add, button, el} from '../../shared/dom.js';
import {remembered} from '../../shared/storage.js';
import {savedProgress} from '../../strength/session.js';
import {app} from '../state.js';
import {chevron, content, cursor, hero, installed, kindIcon, message, pastille, section, sessionLimits, zone} from '../ui.js';
import {api} from '../api.js';
import {capital, day, dayNumber, fmt, hm, isoDate, kindName, paceOf, plannedValue, rpeRange, shortTitle, startedAt, weekNumber} from '../format.js';
import {prep, weatherSlot} from './weather.js';
import {push, render, select} from '../nav.js';
import {loadToday} from '../data.js';
import {launchWorkout, openWorkout} from '../workout.js';

// ------------------------------------------------------------ Today
// The day's thread: what is done today, in order, then what is coming, then the landmarks.
// Detail decreases with distance: the current step has everything, tomorrow one line, the
// week a tile. A single red dot marks the current step; it moves from wake-up to evening.
// A solid disc, with no halo or pulse.
function tiles(cells) {
  const grid = el('div', null, 'n-facts' + (cells.length === 3 ? ' three' : ''));
  cells.slice(0, 4).forEach(([value, label, over]) => grid.append(add(el('div'), el('b', value, over ? 'n-over' : null), el('small', label))));
  return grid;
}
// A planned run: its limits as cursors, nothing done yet. Distance and duration first, the
// heart rate on its allowed band, the effort on its band.
function targetCursors(item) {
  const target = item.targets || {}, box = el('div', null, 'n-curs');
  if (target.distance_cap_km != null || target.distance_target_km != null) {
    const cap = target.distance_cap_km, aim = target.distance_target_km, top = Math.ceil((cap || aim) * 1.25);
    box.append(cursor({label: 'Distance', value: fmt(aim != null ? aim : cap), unit: aim != null ? 'km visés' : 'km au plus', lo: 0, hi: top,
      cap, capLabel: cap != null ? fmt(cap) : null, target: aim, targetLabel: aim != null && cap != null ? fmt(aim) : null, below: true, scale: ['0', String(top)]}));
  } else if (target.duration_cap_min != null) {
    const top = Math.ceil(target.duration_cap_min * 1.2 / 10) * 10;
    box.append(cursor({label: 'Durée', value: target.duration_cap_min + '′', unit: 'au plus', lo: 0, hi: top, cap: target.duration_cap_min,
      capLabel: target.duration_cap_min + '′', scale: ['0', top + '′']}));
  }
  if (target.hr_cap_bpm != null) {
    const lo = target.hr_cap_bpm - 47, hi = target.hr_cap_bpm + 13;
    box.append(cursor({label: 'FC moyenne', value: String(target.hr_cap_bpm), unit: 'bpm au plus', lo, hi, cap: target.hr_cap_bpm, scale: [String(lo), String(hi)]}));
  }
  return box.childElementCount ? box : null;
}
function factCells(item) {
  const fact = item.fact, target = item.targets || {}, over = new Set(fact.over || []), cells = [];
  if (fact.km != null) cells.push([fmt(fact.km, 2), 'km', over.has('km')]);
  if (fact.duration_min != null) cells.push([String(fact.duration_min), 'min' + (target.duration_cap_min ? ' · ≤' + target.duration_cap_min : ''), over.has('duration_min')]);
  if (fact.hr_avg != null) cells.push([fmt(fact.hr_avg, 0), 'FC' + (target.hr_cap_bpm ? ' · ≤' + target.hr_cap_bpm : ' moy'), over.has('hr_avg')]);
  if (fact.km && fact.duration_min) cells.push([paceOf(fact.duration_min * 60 / fact.km), '/km', false]);
  return cells;
}
function overLine(item) {
  const fact = item.fact, target = item.targets || {}, over = new Set(fact.over || []), parts = [];
  if (over.has('hr_avg')) parts.push('FC moyenne ' + fmt(fact.hr_avg - target.hr_cap_bpm, 0) + ' bpm au-dessus de ton plafond');
  if (over.has('rpe')) parts.push('RPE au-dessus de ' + target.rpe_max);
  if (over.has('km')) parts.push(fmt(fact.km - target.distance_cap_km, 1) + ' km au-delà du plafond');
  if (over.has('duration_min')) parts.push((fact.duration_min - target.duration_cap_min) + ' min au-delà du plafond');
  return parts.length ? el('p', capital(parts.join(' · ')) + '.', 'n-overline') : null;
}
const clockText = at => { const date = new Date(at); return String(date.getHours()).padStart(2, '0') + ':' + String(date.getMinutes()).padStart(2, '0'); };
// The day's strength session: the solid button, and the exercise list unfolded under it.
let showExercises = false;
function launcher(strength) {
  if (!strength || strength.status !== 'ready') return strength && strength.message ? el('p', strength.message, 'n-meta') : null;
  const exercises = strength.session.exercises || [];
  const list = el('ol', null, 'n-exercises');
  for (const exercise of exercises) {
    const item = el('li', exercise.name || exercise.slug);
    item.append(el('span', (exercise.dose || {}).text || '', 'n-dose'));
    list.append(item);
  }
  list.hidden = !showExercises;
  // A session started on this phone resumes where it stopped.
  const progress = savedProgress(strength.session);
  const go = button(progress ? 'Reprendre la séance' : 'Séance guidée', async node => {
    node.disabled = true;
    try { openWorkout(await launchWorkout(strength)); }
    catch (error) { message(error.message); }
    node.disabled = false;
  }, 'n-go');
  const row = add(el('div', null, 'n-acts'), go);
  const done = progress ? el('p', progress.done + ' exercice' + (progress.done > 1 ? 's' : '') + ' sur ' + progress.total, 'n-meta') : null;
  if (exercises.length) {
    const toggle = button(exercises.length + ' exercice' + (exercises.length > 1 ? 's' : ''), node => {
      showExercises = list.hidden; list.hidden = !showExercises;
      node.setAttribute('aria-expanded', String(showExercises));
    }, 'n-toggle');
    toggle.setAttribute('aria-expanded', String(showExercises));
    row.append(toggle);
  }
  return add(el('div'), done, row, list);
}
const traces = [['none', 'Aucune'], ['very-light', 'Très légère'], ['light', 'Légère'], ['clear', 'Nette']];
const dailyChoices = {
  sleep: ['Sommeil', [['good', 'Bon'], ['fair', 'Moyen'], ['poor', 'Mauvais']]],
  life_load: ['Charge de vie', [['light', 'Légère'], ['normal', 'Normale'], ['high', 'Élevée']]]
};
const hrChoices = [['sleep', 'Nuit courte'], ['cold', 'Début de rhume'], ['heat', 'Chaleur ou terrain'], ['none', 'Rien de spécial']];
export const fieldSpec = {
  pain: {label: 'Douleur, au pic', name: 'la douleur', values: [0, 0.5, 1, 1.5, 2, 2.5, 3]},
  rpe: {label: 'RPE, séance entière', name: 'le RPE', values: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
  enjoyment: {label: 'Plaisir', name: 'le plaisir', values: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
  carbs_g_h: {label: 'Glucides pris, par heure', name: 'les glucides', values: [0, 20, 30, 40, 50, 60, 70, 80, 90], unit: ' g'},
  fuel_formats: {label: 'Sous quelle forme ?', name: 'la forme', many: true,
    options: [['drink', 'Boisson'], ['gel', 'Gel'], ['chew', 'Pâte ou gomme'], ['solid', 'Solide']]},
  fuel_mix: {label: 'Les sucres', name: 'les sucres',
    options: [['glucose', 'Glucose seul'], ['glucose_fructose', 'Glucose + fructose'], ['unknown', 'Je ne sais pas']]},
  gut_symptoms: {label: 'Troubles digestifs, au pic', name: 'les troubles', values: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
  gut_symptom_kinds: {label: 'Lesquels ?', name: 'lesquels', many: true,
    options: [['nausea', 'Nausée'], ['reflux', 'Reflux'], ['cramps', 'Crampes'], ['bloating', 'Ballonnement'], ['urgency', 'Besoin pressant']]},
  gut_onset_min: {label: 'Apparus vers', name: 'le moment'}
};
// Quarter hours up to the session's duration, written as elapsed time.
const elapsed = minutes => minutes < 60 ? minutes + ' min' : (minutes / 60 | 0) + ' h' + (minutes % 60 ? ' ' + minutes % 60 : '');
const onsetValues = minutes => Array.from({length: Math.max(1, Math.ceil((minutes || 60) / 15))},
  (_, index) => [(index + 1) * 15, elapsed((index + 1) * 15)]);
// `pain:<zone>`: a zone's pain, named by its label.
export function specOf(field, labels) {
  if (!field.startsWith('pain:')) return fieldSpec[field];
  const label = (labels || {})[field] || field.slice(5);
  return {...fieldSpec.pain, label: 'Douleur ' + label + ', au pic', name: 'la douleur (' + label + ')'};
}
async function answer(ask, value, card, picked) {
  card.querySelectorAll('button').forEach(node => { node.disabled = true; });
  if (picked) picked.classList.add('n-picked');
  try {
    const result = await api('answer', {ask, value});
    message(result.alert ? 'Noté. Le coach te répond dans Messenger.' : 'Noté.');
    delete app.signatures.today;
    await loadToday();
  } catch (error) {
    message(error.message);
    card.querySelectorAll('button').forEach(node => { node.disabled = false; node.classList.remove('n-picked'); });
  }
}
// All the values of a scale at a glance, with no hidden scrolling: pain fits on one
// line, RPE and enjoyment on two.
function chips(values, ask, card, label = value => fmt(value), write = answer) {
  const row = el('div', null, 'n-chips');
  row.style.setProperty('--cols', String(values.length <= 7 ? values.length : Math.ceil(values.length / 2)));
  values.forEach(value => {
    const [sent, text] = Array.isArray(value) ? value : [value, label(value)];
    row.append(button(text, node => write(ask, sent, card, node), ''));
  });
  return row;
}
// Several answers to one question: each tap toggles a choice, one button sends them all.
function manyChoices(options, ask, card, write) {
  const picked = new Set();
  const confirm = button('Valider', node => write(ask, [...picked], card, node), 'n-action n-confirm');
  confirm.disabled = true;
  const row = el('div', null, 'n-choices two');
  options.forEach(([value, text]) => {
    const node = button(text, () => {
      if (picked.has(value)) picked.delete(value); else picked.add(value);
      node.classList.toggle('n-on', picked.has(value));
      node.setAttribute('aria-pressed', String(picked.has(value)));
      confirm.disabled = picked.size === 0;
    }, '');
    node.setAttribute('aria-pressed', 'false');
    row.append(node);
  });
  return add(el('div'), row, confirm);
}
function choices(label, hint, options, ask, card) {
  const head = el('span', label);
  if (hint) head.append(el('small', hint));
  const row = el('div', null, 'n-choices' + (options.length === 4 ? ' two' : ''));
  options.forEach(([value, text]) => row.append(button(text, node => answer(ask, value, card, node), '')));
  return add(el('div', null, 'n-field'), head, row);
}
function morning(list) {
  const wakes = list.filter(ask => ask.type === 'wake'), daily = list.find(ask => ask.type === 'daily');
  if (!wakes.length && !daily) return null;
  const card = el('div', null, 'n-check');
  // One trace per monitored zone, each with its own gesture.
  wakes.forEach(wake => card.append(choices('Trace · ' + wake.label, wake.after_effort ? 'après la séance d’hier' : 'sans séance hier', traces, wake.id, card)));
  if (daily) daily.fields.forEach(field => card.append(choices(dailyChoices[field][0], '', dailyChoices[field][1], daily.id + ':' + field, card)));
  card.append(el('p', 'Un geste par ligne. Le coach ne t’écrit que si ça sort de l’ordinaire.', 'n-note'));
  return card;
}
function hrCard(ask) {
  const card = el('div', null, 'n-check');
  const head = el('p', 'Ton cœur était plus haut que d’habitude', 'n-check-title');
  head.append(el('small', ask.hr + ' bpm à ' + paceOf(ask.pace_s) + '/km, contre ' + ask.reference + ' sur tes ' + ask.n + ' EF à allure voisine (+' + (ask.hr - ask.reference) + ').'));
  return add(card, head, choices('Une explication ?', '', hrChoices, ask.id, card));
}
// The debrief, one question at a time: the first missing one, its rank among the total,
// and what follows. Each answer recomputes the screen, which moves on to the next. The
// service counts the total: pains, RPE and enjoyment, then the fuel registry of a fuelled
// session. `write` sends one answer; the session detail passes its own.
export function cellsCard(ask, target, write = answer) {
  const card = el('div', null, 'n-check');
  const metric = ask.fields[0], spec = specOf(metric, ask.labels), label = el('span', spec.label);
  if (metric === 'rpe' && target && target.rpe_max != null) label.append(el('small', 'visé ' + rpeRange(target)));
  if (metric === 'carbs_g_h' && target && target.carbs_target_g_h != null) label.append(el('small', 'visé ' + target.carbs_target_g_h + ' g/h'));
  const total = ask.total;
  const head = add(el('p', null, 'n-check-head'), label, el('span', (total - ask.fields.length + 1) + ' sur ' + total, 'n-of'));
  const key = ask.id + ':' + metric;
  if (spec.many) add(card, head, manyChoices(spec.options, key, card, write));
  else if (spec.options) add(card, head, add(el('div', null, 'n-choices' + (spec.options.length === 3 ? ' one' : ' two')),
    ...spec.options.map(([value, text]) => button(text, node => write(key, value, card, node), ''))));
  else add(card, head, chips(metric === 'gut_onset_min' ? onsetValues(ask.duration_min) : spec.values, key, card,
    spec.unit ? value => fmt(value) + spec.unit : undefined, write));
  const next = ask.fields.slice(1).map(field => specOf(field, ask.labels).name);
  const notes = [];
  const listed = next.length > 1 ? next.slice(0, -1).join(', ') + ' et ' + next[next.length - 1] : next[0];
  if (next.length) notes.push('Puis ' + listed + (next.length > 1 ? ', un geste chacun.' : '.'));
  if (metric.startsWith('pain:')) notes.push('Plus de 3 ? Dis-le au coach dans Messenger.');
  if (notes.length) card.append(el('p', notes.join(' '), 'n-note'));
  return card;
}
// A step of the thread: its role, its title, preceded by its family pill when it is a
// session. The thread draws no dot: the phase reads in the kicker and the class.
export function feedStep(phase, kicker, title, small, icon) {
  const body = el('div', null, 'n-sbody');
  if (kicker) body.append(el('p', kicker, 'n-skick'));
  if (title) body.append(add(el('h2'), icon ? pastille(icon) : null, title));
  return [add(el('div', null, 'n-step ' + phase + (small ? ' small' : '') + (kicker ? '' : ' bare')), body), body];
}
// A day's thread: the morning point, overdue questions, what is done, then what remains.
// The first expected gesture takes the red dot.
function dayFeed(data, items, extras, options) {
  const feed = el('div', null, 'n-feed');
  const used = new Set();
  let red = false, compact = Boolean(options.compact);
  const take = () => { if (red) return 'next'; red = true; return 'now'; };
  if (options.today) {
    const wakes = data.asks.filter(ask => ask.type === 'wake' || ask.type === 'daily');
    const card = morning(wakes);
    if (card) {
      wakes.forEach(ask => used.add(ask.id)); compact = true;
      const [node, body] = feedStep(take(), 'Ce matin', 'Comment tu te réveilles ?', true);
      feed.append(add(node, add(body, card)));
    }
  }
  const runs = items.filter(item => item.kind !== 'strength'), strengths = items.filter(item => item.kind === 'strength');
  const done = [...runs.filter(item => item.status === 'done'), ...extras.map(fact => ({fact, targets: {}, extra: true, kind: 'run'})),
    ...strengths.filter(item => item.status === 'done')]
    .sort((a, b) => ((a.fact || {}).started_at || '~').localeCompare((b.fact || {}).started_at || '~'));
  const pending = [...runs, ...strengths].filter(item => item.status !== 'done');
  // Questions about a session done today go under it; the others come first.
  const ownCells = new Set(done.filter(item => item.fact && item.kind !== 'strength').map(item => 'cell:' + item.fact.observation_id));
  const ownHr = done.some(item => item.kind !== 'strength') && options.today;
  if (options.today) data.asks.filter(ask => !used.has(ask.id) && !ownCells.has(ask.id) && !(ownHr && ask.type === 'hr' && ask.date === data.date))
    .forEach(ask => {
      if (ask.type !== 'hr' && ask.type !== 'cells') return;
      used.add(ask.id);
      const what = ask.type === 'hr' ? kindName.easy : kindName[ask.kind] || ask.kind;
      const [node, body] = feedStep(take(), 'À compléter', what + ' de ' + dayNumber(ask.date) + (ask.km ? ' · ' + fmt(ask.km) + ' km' : ''), true, kindIcon(ask.type === 'hr' ? 'easy' : ask.kind));
      feed.append(add(node, add(body, ask.type === 'hr' ? hrCard(ask) : cellsCard(ask))));
    });
  done.forEach(item => {
    const fact = item.fact || {};
    if (item.kind === 'strength') {
      const [node] = feedStep('done', 'Fait' + (fact.duration_min != null ? ' · ' + fact.duration_min + ' min' : ''), item.title, true, 'str');
      feed.append(node); return;
    }
    const kicker = ['Fait', startedAt(fact), item.extra ? 'hors programme' : null].filter(Boolean).join(' · ');
    const [node, body] = feedStep('done', kicker, item === options.hero ? null : item.extra ? kindName[fact.kind] || fact.kind : item.title, false, item.extra ? kindIcon(fact.kind) : kindIcon(item.kind, item.workout));
    const limited = item.extra ? null : sessionLimits(item.targets || {}, fact);
    add(body, limited || tiles(factCells(item)), item.extra ? null : overLine(item));
    if (fact.observation_id) {
      const title = item.extra ? kindName[fact.kind] || fact.kind : item.title;
      body.append(add(button('', () => push({k: 'session', id: fact.observation_id, title}), 'n-dopen'), el('span', 'Détail de la séance'), chevron()));
    }
    const hr = ownHr && data.asks.find(ask => ask.type === 'hr' && ask.date === data.date && !used.has(ask.id));
    if (hr) { body.append(hrCard(hr)); used.add(hr.id); }
    const cells = data.asks.find(ask => ask.id === 'cell:' + fact.observation_id);
    if (cells && options.today) body.append(cellsCard(cells, item.targets));
    feed.append(node);
  });
  pending.forEach((item, index) => {
    const phase = options.today ? take() : 'next';
    const strength = item.kind === 'strength';
    const started = strength && options.today && data.strength && data.strength.status === 'ready' ? savedProgress(data.strength.session) : null;
    const role = started ? 'En cours' : phase === 'now' ? 'À faire' : index > 0 ? 'Puis' : options.today ? 'Ensuite' : null;
    const kicker = [role, started ? 'commencée à ' + hm(clockText(started.startedAt)) : item.start_time ? hm(item.start_time) : null, item.location].filter(Boolean).join(' · ');
    const [node, body] = feedStep(phase, kicker, item === options.hero ? null : item.title, strength || compact, kindIcon(item.kind, item.workout));
    if (item.status === 'held') body.append(el('p', 'Une question t’attend dans Messenger avant cette séance.', 'n-meta'));
    else if (strength) {
      if (item.note && !compact) body.append(el('p', item.note, 'n-meta'));
      if (options.today) body.append(launcher(data.strength));
    } else {
      body.append(targetCursors(item));
      if (item.note && !compact) body.append(el('p', item.note, 'n-meta'));
      body.append(prep(item, {today: options.today, compact}));
    }
    feed.append(node);
  });
  return feed;
}
// On a rest day, tomorrow is the screen's subject: one line for today, then its full
// card, weather included.
function restFeed(data, hero) {
  const feed = dayFeed(data, [], [], {today: true});
  const [node, body] = feedStep('rest', null, null);
  if (data.plan.status === 'invalid') add(body, el('h2', 'Programme en préparation'), el('p', data.plan.message, 'n-meta'));
  else body.append(add(el('p', null, 'n-restline'), el('b', 'Repos aujourd’hui.'), ' Rien à faire.'));
  feed.append(node);
  const box = add(el('div'), feed), next = data.tomorrow, real = next.sessions.filter(item => item.kind !== 'rest');
  const compact = data.asks.some(ask => ask.type === 'wake' || ask.type === 'daily');
  if (real.length) add(box, el('p', 'Demain · ' + dayNumber(next.date), 'n-day'), dayFeed(data, real, [], {compact, hero}));
  else if (next.plan === 'invalid') box.append(el('p', 'Le programme de la semaine arrive avec le bilan.', 'n-meta'));
  else if (next.next.length) add(box, el('p', capital(dayNumber(next.next[0].date)), 'n-day'), dayFeed(data, next.next, [], {compact: true, hero}));
  return [box, real.length ? next.date : next.next.length ? next.next[0].date : null];
}
// What is coming: one line per day, as in the Week tab; weather for tomorrow only.
function ahead(data, seen) {
  const items = (data.ahead || []).filter(item => !seen.has(item.date));
  if (!items.length) return null;
  const node = zone('À venir');
  const tomorrow = isoDate(new Date(day(data.date).getTime() + 86400000));
  [...new Set(items.map(item => item.date))].slice(0, 3).forEach(date => {
    const same = items.filter(item => item.date === date);
    const main = same.find(item => item.kind !== 'strength') || same[0];
    const title = same.length > 1 && main.kind !== 'strength' ? shortTitle(main.title) + ', puis renfo' : main.title;
    const target = main.targets || {};
    const sub = el('small', [main.start_time ? hm(main.start_time) : null, main.location,
      target.hr_cap_bpm != null ? '≤' + target.hr_cap_bpm + ' bpm' : null].filter(Boolean).join(' · '));
    if (date === tomorrow && main.kind !== 'strength') sub.append(weatherSlot(main, 'inline'));
    const d = day(date);
    node.append(add(el('div', null, 'n-arow'),
      add(el('span', null, 'n-kd'), el('b', String(d.getDate())), d.toLocaleDateString('fr-FR', {weekday: 'short'}).replace('.', '')),
      add(el('span', null, 'n-at'), title, sub), el('span', plannedValue(main), 'n-av')));
  });
  return node;
}
// The landmarks, on paper: the week with its bars, each watched zone against its ceiling,
// the race. Each one leads to its screen.
function landmark(label, onPress, icon) {
  return add(button('', onPress, 'n-lm'), pastille(icon, true), el('span', label), chevron());
}
function weekBars(days) {
  const height = 52, peak = Math.max(1, ...days.map(item => Math.max(item.done_km || 0, item.planned_km || 0)));
  const bars = el('div', null, 'n-mini');
  days.forEach(item => {
    const col = el('span'), bar = el('i');
    let value = '';
    if (item.done_km) { bar.style.height = Math.max(4, height * item.done_km / peak) + 'px'; value = fmt(item.done_km); }
    else if (item.planned_km || item.planned_label) {
      bar.className = 'plan' + (item.long ? ' long' : '');
      bar.style.height = (item.planned_km ? Math.max(4, height * item.planned_km / peak) : height / 2) + 'px';
      value = item.planned_km ? fmt(item.planned_km) : item.planned_label;
    } else bar.className = 'flat';
    bars.append(add(col, el('em', value, item.done_km ? null : 'plan'), bar, el('small', item.letter, item.today ? 'now' : null)));
  });
  return bars;
}
function landmarks(data) {
  const totals = data.totals, node = section('Repères');
  const runs = totals.runs_done + ' sortie' + (totals.runs_done > 1 ? 's' : '') + (totals.runs_planned ? ' sur ' + totals.runs_planned : '');
  const week = add(el('div', null, 'n-mark'), landmark('Semaine ' + weekNumber(data.week), () => select('week'), 'week'),
    add(el('p', null, 'n-big'), el('b', fmt(totals.km_done)), totals.km_target ? el('span', 'sur', 'of') : null,
      totals.km_target ? el('b', fmt(totals.km_target), 'n-plan-num') : null, el('span', 'km · ' + runs)), weekBars(data.strip));
  week.setAttribute('aria-label', 'Semaine ' + weekNumber(data.week) + ' : ' + fmt(totals.km_done) + ' kilomètres courus' + (totals.km_target ? ' sur environ ' + fmt(totals.km_target) : ''));
  node.append(week);
  // One block per watched zone; none when the instance watches none.
  data.pain.zones.forEach(item => {
    const peak = (item.weeks[item.weeks.length - 1] || {}).peak, top = Math.max(3, Math.ceil((peak || 0) + 0.5));
    node.append(add(el('div', null, 'n-mark pain'), landmark(item.label, () => push({k: 'injury', zone: item.zone}), 'pain'),
      cursor({label: 'Pic d’effort, cette semaine', value: peak == null ? '—' : fmt(peak), unit: 'sur 10', lo: 0, hi: top,
        fill: peak, cap: data.pain.cap, capLabel: 'plafond ' + fmt(data.pain.cap), scale: Array.from({length: top + 1}, (_, index) => String(index))})));
  });
  const race = data.race;
  if (race) {
    const date = day(race.date).toLocaleDateString('fr-FR', {weekday: 'short', day: 'numeric', month: 'long'});
    const facts = [date, race.start_time ? 'départ ' + hm(race.start_time) : null, race.distance_km ? fmt(race.distance_km) + ' km' : null,
      race.target ? 'cible ' + race.target : null].filter(Boolean).join(' · ');
    node.append(add(el('div', null, 'n-mark race'), landmark(race.name, () => select('course'), 'race'),
      add(el('p', null, 'n-big'), el('b', String(race.days)), el('span', race.days > 1 ? 'jours' : 'jour')), el('p', facts, 'n-meta')));
  }
  return node;
}
function installSection() {
  if (installed() || remembered('carnet-install-later')) return null;
  const later = button('Plus tard', () => { remembered('carnet-install-later', '1'); node.remove(); }, 'n-quiet n-aside');
  const node = section('Sur ton écran d’accueil', later);
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  node.append(el('p', app.installPrompt ? 'Ton carnet s’ouvre alors d’un geste, comme une app.'
    : ios ? 'Dans Safari : Partager, puis « Sur l’écran d’accueil ». Il s’ouvre alors d’un geste, comme une app.'
    : 'Menu du navigateur, puis « Installer l’application ». Il s’ouvre alors d’un geste, comme une app.', 'n-meta'));
  if (app.installPrompt) node.append(button('Installer', async () => {
    const prompt = app.installPrompt; app.installPrompt = null;
    await prompt.prompt();
    if ((await prompt.userChoice).outcome === 'accepted') remembered('carnet-install-later', '1');
    render();
  }, 'n-action'));
  return node;
}
// The head of Today always carries one session and its figure, as Week and Race carry
// theirs: the run still to do, outlined; else the run done today, solid; else the next run.
function todayHero(item, kicker) {
  const target = item.targets || {};
  let figure = null, side = [];
  if (target.distance_target_km != null) { figure = fmt(target.distance_target_km); side = ['km visés'].concat(target.distance_cap_km != null ? [fmt(target.distance_cap_km) + ' au plus'] : []); }
  else if (target.distance_cap_km != null) { figure = fmt(target.distance_cap_km); side = ['km au plus']; }
  else if (target.duration_cap_min != null) { figure = target.duration_cap_min + '′'; side = ['au plus']; }
  if (target.rpe_max != null) side.push('RPE ' + rpeRange(target));
  add(hero, el('p', [kicker, item.start_time ? hm(item.start_time) : null].filter(Boolean).join(' · '), 'n-hkick'),
    add(el('h2', null, 'n-htitle'), pastille(kindIcon(item.kind, item.workout)), item.title),
    figure ? add(el('p', null, 'n-hfig'), el('b', figure, 'n-plan-num'), add(el('span'), ...side.flatMap((text, index) => index ? [el('br'), text] : [text]))) : null);
}
function doneHero(item) {
  const fact = item.fact, figure = fact.km != null ? fmt(fact.km) : fact.duration_min != null ? fact.duration_min + '′' : null;
  const side = fact.km != null ? ['km'].concat(fact.duration_min != null ? [fact.duration_min + ' min' + (fact.km ? ' · ' + paceOf(fact.duration_min * 60 / fact.km) + '/km' : '')] : []) : [];
  add(hero, el('p', ['Fait', startedAt(fact)].filter(Boolean).join(' · '), 'n-hkick'),
    add(el('h2', null, 'n-htitle'), pastille(kindIcon(item.kind, item.workout)), item.title),
    figure ? add(el('p', null, 'n-hfig'), el('b', figure), add(el('span'), ...side.flatMap((text, index) => index ? [el('br'), text] : [text]))) : null);
}
// The next run after today, from tomorrow's sessions or the days ahead.
function nextRun(data) {
  const later = [...data.tomorrow.sessions, ...data.tomorrow.next, ...(data.ahead || [])];
  return later.find(item => item.date > data.date && item.kind !== 'strength' && item.kind !== 'rest' && item.status !== 'done') || null;
}
export function renderToday(data) {
  if (data.plan.status === 'awaiting') content.append(el('p', data.plan.message, 'n-banner'));
  if (data.plan.status === 'stale') content.append(el('p', data.plan.message, 'n-banner'));
  const items = data.sessions.filter(item => item.kind !== 'rest');
  const seen = new Set([data.date]);
  const main = items.find(item => item.kind !== 'strength' && item.status !== 'done' && item.status !== 'held');
  const ran = main ? null : items.find(item => item.kind !== 'strength' && item.status === 'done' && item.fact);
  const next = main || ran ? null : nextRun(data);
  if (main) todayHero(main, 'Au programme');
  else if (ran) doneHero(ran);
  else if (next) todayHero(next, next.date === data.tomorrow.date ? 'Demain' : capital(dayNumber(next.date)));
  if (items.length || data.extra.length) content.append(dayFeed(data, items, data.extra, {today: true, hero: main || ran}));
  else {
    const [box, heroDate] = restFeed(data, next);
    content.append(box);
    if (heroDate) seen.add(heroDate);
  }
  add(content, ahead(data, seen), landmarks(data), installSection());
}
