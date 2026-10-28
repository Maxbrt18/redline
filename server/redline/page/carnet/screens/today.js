import {add, button, el} from '../../shared/dom.js';
import {remembered} from '../../shared/storage.js';
import {savedProgress} from '../../strength/session.js';
import {app} from '../state.js';
import {SVG, chevron, content, installed, kindIcon, message, pastille, section, shape, zone} from '../ui.js';
import {api} from '../api.js';
import {capital, day, dayNumber, fmt, hm, isoDate, kindName, paceOf, plannedValue, rpeRange, shortTitle, startedAt, weekNumber} from '../format.js';
import {prep, weatherSlot} from './weather.js';
import {push, render, select} from '../nav.js';
import {loadToday} from '../data.js';
import {launchSeance, openSeance} from '../seance.js';

// ------------------------------------------------------------ Aujourd'hui
// Le fil du jour (maquette A validée par l'athlète le 2026-10-09) : ce qui se fait
// aujourd'hui, dans l'ordre, puis ce qui vient, puis les repères. Le détail décroît avec
// la distance : l'étape en cours a tout, demain une ligne, la semaine une tuile. Un seul
// point rouge, l'étape en cours ; il avance du réveil au soir. Un disque plein, sans halo
// ni pulsation (l'athlète, 2026-10-09 : « ça fait très AI generated »).
function tiles(cells) {
  const grid = el('div', null, 'n-facts' + (cells.length === 3 ? ' three' : ''));
  cells.slice(0, 4).forEach(([value, label, over]) => grid.append(add(el('div'), el('b', value, over ? 'n-over' : null), el('small', label))));
  return grid;
}
function targetCells(item) {
  const target = item.targets || {}, cells = [];
  if (target.distance_target_km != null) cells.push([fmt(target.distance_target_km), 'km' + (target.distance_cap_km && target.distance_cap_km !== target.distance_target_km ? ' · ≤' + fmt(target.distance_cap_km) : '')]);
  else if (target.distance_cap_km != null) cells.push(['≤' + fmt(target.distance_cap_km), 'km']);
  if (target.duration_cap_min != null && cells.length === 0) cells.push(['≤' + target.duration_cap_min, 'min']);
  if (target.hr_cap_bpm != null) cells.push(['≤' + target.hr_cap_bpm, 'FC moy']);
  if (target.rpe_max != null) cells.push([rpeRange(target), 'RPE']);
  return cells;
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
// Le renfo du jour : le bouton plein, et la liste des exercices dépliée sous lui.
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
  // Une séance commencée sur ce téléphone se reprend là où elle en est.
  const progress = savedProgress(strength.session);
  const go = button(progress ? 'Reprendre la séance →' : 'Séance guidée →', async node => {
    node.disabled = true;
    try { openSeance(await launchSeance(strength)); }
    catch (error) { message(error.message); }
    node.disabled = false;
  }, 'n-action');
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
const traces = [['aucune', 'Aucune'], ['tres-legere', 'Très légère'], ['legere', 'Légère'], ['nette', 'Nette']];
const dailyChoices = {
  sleep: ['Sommeil', [['bonne', 'Bon'], ['moyenne', 'Moyen'], ['mauvaise', 'Mauvais']]],
  life_load: ['Charge de vie', [['legere', 'Légère'], ['normale', 'Normale'], ['elevee', 'Élevée']]]
};
const hrChoices = [['sommeil', 'Nuit courte'], ['rhume', 'Début de rhume'], ['chaleur', 'Chaleur ou terrain'], ['rien', 'Rien de spécial']];
export const fieldSpec = {
  pain: {label: 'Douleur, au pic', name: 'la douleur', values: [0, 0.5, 1, 1.5, 2, 2.5, 3]},
  rpe: {label: 'RPE, séance entière', name: 'le RPE', values: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
  enjoyment: {label: 'Plaisir', name: 'le plaisir', values: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}
};
// « pain:genou » : la douleur d'une zone, nommée par son libellé.
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
// Toutes les valeurs d'une échelle d'un coup d'œil, sans défilement caché : la
// douleur tient sur une ligne, le RPE et le plaisir sur deux.
function chips(values, ask, card, label = value => fmt(value)) {
  const row = el('div', null, 'n-chips');
  row.style.setProperty('--cols', String(values.length <= 7 ? values.length : Math.ceil(values.length / 2)));
  values.forEach(value => {
    const [sent, text] = Array.isArray(value) ? value : [value, label(value)];
    row.append(button(text, node => answer(ask, sent, card, node), ''));
  });
  return row;
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
  // Une trace par zone surveillée, chacune son geste.
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
// Le débrief, une question à la fois : la première qui manque, son rang parmi les trois,
// et ce qui suivra. Chaque réponse recalcule l'écran, qui passe à la suivante.
// Le total : une douleur par zone surveillée, puis le RPE et le plaisir.
export const cellCount = ask => Object.keys(ask.labels || {}).length + 2;
function cellsCard(ask, target) {
  const card = el('div', null, 'n-check');
  const metric = ask.fields[0], spec = specOf(metric, ask.labels), label = el('span', spec.label);
  if (metric === 'rpe' && target && target.rpe_max != null) label.append(el('small', 'visé ' + rpeRange(target)));
  const total = cellCount(ask);
  const head = add(el('p', null, 'n-check-head'), label, el('span', (total - ask.fields.length + 1) + ' sur ' + total, 'n-of'));
  add(card, head, chips(spec.values, ask.id + ':' + metric, card));
  const next = ask.fields.slice(1).map(field => specOf(field, ask.labels).name);
  const notes = [];
  if (next.length) notes.push('Puis ' + next.join(' et ') + (next.length > 1 ? ', un geste chacun.' : '.'));
  if (metric.startsWith('pain:')) notes.push('Plus de 3 ? Dis-le au coach dans Messenger.');
  if (notes.length) card.append(el('p', notes.join(' '), 'n-note'));
  return card;
}
// Une étape du fil : son point, son rôle en petites capitales, son titre, précédé de la
// pastille de sa famille quand c'est une séance.
const CHECK = '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2 5.2l2 2 4-4.4"/></svg>';
export function filStep(phase, kicker, title, small, icon) {
  const node = el('i', null, 'n-node');
  if (phase === 'done') node.innerHTML = CHECK;
  const body = el('div', null, 'n-sbody');
  if (kicker) body.append(el('p', kicker, 'n-skick'));
  if (title) body.append(add(el('h2'), icon ? pastille(icon) : null, title));
  return [add(el('div', null, 'n-step ' + phase + (small ? ' small' : '') + (kicker ? '' : ' bare')), node, body), body];
}
// Le fil d'un jour : le point du matin, les questions en retard, ce qui est fait, puis
// ce qui reste. Le premier geste attendu prend le point rouge.
function dayFil(data, items, extras, options) {
  const fil = el('div', null, 'n-fil');
  const used = new Set();
  let red = false, compact = Boolean(options.compact);
  const take = () => { if (red) return 'next'; red = true; return 'now'; };
  if (options.today) {
    const wakes = data.asks.filter(ask => ask.type === 'wake' || ask.type === 'daily');
    const card = morning(wakes);
    if (card) {
      wakes.forEach(ask => used.add(ask.id)); compact = true;
      const [node, body] = filStep(take(), 'Ce matin', 'Comment tu te réveilles ?', true);
      fil.append(add(node, add(body, card)));
    }
  }
  const runs = items.filter(item => item.kind !== 'strength'), strengths = items.filter(item => item.kind === 'strength');
  const done = [...runs.filter(item => item.status === 'done'), ...extras.map(fact => ({fact, targets: {}, extra: true, kind: 'run'})),
    ...strengths.filter(item => item.status === 'done')]
    .sort((a, b) => ((a.fact || {}).started_at || '~').localeCompare((b.fact || {}).started_at || '~'));
  const pending = [...runs, ...strengths].filter(item => item.status !== 'done');
  // Les questions sur une séance faite aujourd'hui vont sous elle ; les autres passent devant.
  const ownCells = new Set(done.filter(item => item.fact && item.kind !== 'strength').map(item => 'cell:' + item.fact.observation_id));
  const ownHr = done.some(item => item.kind !== 'strength') && options.today;
  if (options.today) data.asks.filter(ask => !used.has(ask.id) && !ownCells.has(ask.id) && !(ownHr && ask.type === 'hr' && ask.date === data.date))
    .forEach(ask => {
      if (ask.type !== 'hr' && ask.type !== 'cells') return;
      used.add(ask.id);
      const what = ask.type === 'hr' ? 'EF' : kindName[ask.kind] || ask.kind;
      const [node, body] = filStep(take(), 'À compléter', what + ' de ' + dayNumber(ask.date) + (ask.km ? ' · ' + fmt(ask.km) + ' km' : ''), true, kindIcon(ask.type === 'hr' ? 'EF' : ask.kind));
      fil.append(add(node, add(body, ask.type === 'hr' ? hrCard(ask) : cellsCard(ask))));
    });
  done.forEach(item => {
    const fact = item.fact || {};
    if (item.kind === 'strength') {
      const [node] = filStep('done', 'Fait' + (fact.duration_min != null ? ' · ' + fact.duration_min + ' min' : ''), item.title, true, 'str');
      fil.append(node); return;
    }
    const kicker = ['Fait', startedAt(fact), item.extra ? 'hors programme' : null].filter(Boolean).join(' · ');
    const [node, body] = filStep('done', kicker, item.extra ? kindName[fact.kind] || fact.kind : item.title, false, item.extra ? kindIcon(fact.kind) : kindIcon(item.kind, item.workout));
    add(body, tiles(factCells(item)), item.extra ? null : overLine(item));
    if (fact.observation_id) {
      const title = item.extra ? kindName[fact.kind] || fact.kind : item.title;
      body.append(add(button('', () => push({k: 'session', id: fact.observation_id, title}), 'n-dopen'), el('span', 'Détail de la séance'), chevron()));
    }
    const hr = ownHr && data.asks.find(ask => ask.type === 'hr' && ask.date === data.date && !used.has(ask.id));
    if (hr) { body.append(hrCard(hr)); used.add(hr.id); }
    const cells = data.asks.find(ask => ask.id === 'cell:' + fact.observation_id);
    if (cells && options.today) body.append(cellsCard(cells, item.targets));
    fil.append(node);
  });
  pending.forEach((item, index) => {
    const phase = options.today ? take() : 'next';
    const strength = item.kind === 'strength';
    const started = strength && options.today && data.strength && data.strength.status === 'ready' ? savedProgress(data.strength.session) : null;
    const role = started ? 'En cours' : phase === 'now' ? 'À faire' : index > 0 ? 'Puis' : options.today ? 'Ensuite' : null;
    const kicker = [role, started ? 'commencée à ' + hm(clockText(started.startedAt)) : item.start_time ? hm(item.start_time) : null, item.location].filter(Boolean).join(' · ');
    const [node, body] = filStep(phase, kicker, item.title, strength || compact, kindIcon(item.kind, item.workout));
    if (item.status === 'held') body.append(el('p', 'Une question t’attend dans Messenger avant cette séance.', 'n-meta'));
    else if (strength) {
      if (item.note && !compact) body.append(el('p', item.note, 'n-meta'));
      if (options.today) body.append(launcher(data.strength));
    } else {
      const cells = targetCells(item);
      if (cells.length) body.append(tiles(cells));
      if (item.note && !compact) body.append(el('p', item.note, 'n-meta'));
      body.append(prep(item, {today: options.today, compact}));
    }
    fil.append(node);
  });
  return fil;
}
// Un jour de repos, demain est le sujet de l'écran : une ligne pour aujourd'hui, puis
// sa fiche entière, météo comprise.
function restFil(data) {
  const fil = dayFil(data, [], [], {today: true});
  const [node, body] = filStep('rest', null, null);
  if (data.plan.status === 'invalid') add(body, el('h2', 'Programme en préparation'), el('p', data.plan.message, 'n-meta'));
  else body.append(add(el('p', null, 'n-restline'), el('b', 'Repos aujourd’hui.'), ' Rien à faire.'));
  fil.append(node);
  const box = add(el('div'), fil), next = data.tomorrow, real = next.sessions.filter(item => item.kind !== 'rest');
  const compact = data.asks.some(ask => ask.type === 'wake' || ask.type === 'daily');
  if (real.length) add(box, el('p', 'Demain · ' + dayNumber(next.date), 'n-day'), dayFil(data, real, [], {compact}));
  else if (next.plan === 'invalid') box.append(el('p', 'Le programme de la semaine arrive avec le bilan.', 'n-meta'));
  else if (next.next.length) add(box, el('p', capital(dayNumber(next.next[0].date)), 'n-day'), dayFil(data, next.next, [], {compact: true}));
  return [box, real.length ? next.date : next.next.length ? next.next[0].date : null];
}
// Ce qui vient : une ligne par jour, comme dans l'onglet Semaine ; la météo pour demain seulement.
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
// Les repères (maquette « une large, deux demi » validée le 2026-10-10) : la semaine en
// pleine largeur avec ses barres chiffrées, puis les douleurs contre leur plafond et la
// course côte à côte. Chaque tuile mène à son onglet ; J-N reste à l'encre, le rouge
// appartient à l'étape en cours.
function tile(label, onPress, cls, icon) {
  const name = add(el('span', null, 'l'), pastille(icon, true), el('span', label));
  return add(button('', onPress, 'n-tile ' + cls), add(el('span', null, 'n-tlab'), name, chevron()));
}
function tvalue(value, suffix) {
  return add(el('b', value, 'n-tvalue'), suffix ? el('span', suffix) : null);
}
function weekBars(days) {
  const height = 40, peak = Math.max(1, ...days.map(item => Math.max(item.done_km || 0, item.planned_km || 0)));
  const bars = el('span', null, 'n-mini');
  days.forEach(item => {
    const col = el('span'), bar = el('i');
    let value = '';
    if (item.done_km) { bar.style.height = Math.max(4, height * item.done_km / peak) + 'px'; value = fmt(item.done_km); }
    else if (item.planned_km || item.planned_label) {
      bar.className = 'plan' + (item.long ? ' long' : '');
      bar.style.height = (item.planned_km ? Math.max(4, height * item.planned_km / peak) : height / 2) + 'px';
      value = item.planned_km ? fmt(item.planned_km) : item.planned_label;
    } else bar.className = 'flat';
    bars.append(add(col, el('em', value, item.done_km ? null : 'plan'), bar, el(item.today ? 'b' : 'small', item.letter)));
  });
  return bars;
}
function painPlot(pain) {
  const width = 144, height = 62, lo = height - 18, hi = 6;
  const top = Math.max(2.5, pain.cap + 0.5, ...pain.weeks.map(item => (item.peak || 0) + 0.5));
  const y = value => (lo - (lo - hi) * value / top).toFixed(1);
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height); svg.setAttribute('class', 'n-plot'); svg.setAttribute('aria-hidden', 'true');
  shape('line', {x1: 0, x2: width, y1: lo + 4, y2: lo + 4, class: 'base'}, svg);
  shape('line', {x1: 0, x2: width, y1: y(pain.cap), y2: y(pain.cap), class: 'cap'}, svg);
  shape('text', {x: width, y: y(pain.cap) - 4, 'text-anchor': 'end', class: 'capl'}, svg, 'plafond ' + fmt(pain.cap));
  // Les étiquettes de semaine sont centrées sur leur point : 12 px de marge les gardent
  // dans la tuile, quel que soit le nombre de semaines.
  const step = (width - 30) / Math.max(1, pain.weeks.length - 1);
  pain.weeks.forEach((item, index) => {
    const x = 12 + step * index, now = index === pain.weeks.length - 1 ? ' now' : '';
    shape('text', {x, y: height - 1, 'text-anchor': 'middle', class: 'w' + now}, svg, 'W' + item.week.slice(-2));
    if (item.peak == null) return;
    const out = item.peak > pain.cap ? ' out' : '';
    shape('circle', {cx: x, cy: y(item.peak), r: 3.6, class: now + out}, svg);
    shape('text', {x: x + 7, y: Number(y(item.peak)) + 4, class: 'v' + now + out}, svg, fmt(item.peak));
  });
  return svg;
}
function landmarks(data) {
  const totals = data.totals, grid = el('div', null, 'n-tiles');
  const runs = totals.runs_done + ' sortie' + (totals.runs_done > 1 ? 's' : '') + (totals.runs_planned ? ' sur ' + totals.runs_planned : '');
  const week = tile('Semaine ' + weekNumber(data.week), () => select('week'), 'wide week', 'week');
  week.setAttribute('aria-label', 'Semaine ' + weekNumber(data.week) + ' : ' + fmt(totals.km_done) + ' kilomètres courus' + (totals.km_target ? ' sur environ ' + fmt(totals.km_target) : ''));
  grid.append(add(week, add(el('span'), tvalue(fmt(totals.km_done), totals.km_target ? '/' + fmt(totals.km_target) + ' km' : 'km'),
    el('small', runs)), weekBars(data.strip)));
  // Une tuile par zone surveillée ; aucune quand l'instance n'en surveille pas.
  data.pain.zones.forEach(item => {
    const peak = (item.weeks[item.weeks.length - 1] || {}).peak;
    const pain = tvalue(peak == null ? '—' : fmt(peak), '/10');
    if (peak > data.pain.cap) pain.classList.add('n-over');
    grid.append(add(tile(item.label, () => push({k: 'injury', zone: item.zone}), 'tib', 'tib'), pain, el('small', 'pic de la semaine'),
      add(el('span', null, 'n-tfoot'), painPlot({cap: data.pain.cap, weeks: item.weeks}))));
  });
  const race = data.race;
  if (race) {
    const date = day(race.date).toLocaleDateString('fr-FR', {weekday: 'short', day: 'numeric', month: 'long'});
    const start = [race.start_time ? 'départ ' : null, race.start_time ? el('b', hm(race.start_time)) : null,
      race.distance_km ? (race.start_time ? ' · ' : '') + fmt(race.distance_km) + ' km' : null].filter(Boolean);
    grid.append(add(tile(race.name, () => select('course'), 'race', 'race'), tvalue('J-' + race.days), el('small', date),
      add(el('span', null, 'n-tfoot'), start.length ? add(el('small'), ...start) : null,
        race.target ? add(el('small', 'cible '), el('b', race.target)) : null)));
  }
  return add(zone('Repères'), grid);
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
export function renderToday(data) {
  if (data.plan.status === 'awaiting') content.append(el('p', data.plan.message, 'n-banner'));
  if (data.plan.status === 'stale') content.append(el('p', data.plan.message, 'n-banner'));
  const items = data.sessions.filter(item => item.kind !== 'rest');
  const seen = new Set([data.date]);
  if (items.length || data.extra.length) content.append(dayFil(data, items, data.extra, {today: true}));
  else {
    const [box, heroDate] = restFil(data);
    content.append(box);
    if (heroDate) seen.add(heroDate);
  }
  add(content, ahead(data, seen), landmarks(data), installSection());
}
