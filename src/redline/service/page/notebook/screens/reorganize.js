import {$, add, button, el} from '../../shared/dom.js';
import {app} from '../state.js';
import {chevron, content, kindIcon, message, pastille, section} from '../ui.js';
import {api} from '../api.js';
import {day, fmt} from '../format.js';
import {leave, render, screen} from '../nav.js';
import {refresh} from '../data.js';
import {present} from '../sheet.js';

// ------------------------------------------------------------ Reorganise
// The athlete says how each day presents itself, in words of what they can do; the
// variants follow at once, computed by the engine without the coach. A tap takes one:
// it becomes the next revision of the week. A filter is never shown, only what a variant
// bends, in one grey line.
const REORG_STATES = [
  ['as_planned', 'Comme prévu'], ['off', 'Pas de course'], ['short', 'Court seulement'],
  ['easy', 'Facile seulement'], ['more', 'Je peux en faire plus'],
  ['with_others', 'Avec d’autres'], ['other_sport', 'Autre sport'],
];
const REORG_LABEL = Object.fromEntries(REORG_STATES);
const REORG_PACE = {easy: 'Tranquille', mixed: 'Variable', workout: 'Séance'};
const REORG_BLOCKED = {
  awaiting: 'Une question du coach attend ta réponse dans Messenger : réponds-lui avant de réorganiser.',
  missing: 'Aucun programme enregistré pour cette semaine : rien à réorganiser.',
  closed: 'Cette semaine est terminée.',
};
const reorgWeekday = value => day(value).toLocaleDateString('fr-FR', {weekday: 'short'}).replace('.', '');
const reorgDay = value => reorgWeekday(value) + ' ' + day(value).getDate();
const reorgHours = minutes => minutes >= 60 ? Math.floor(minutes / 60) + ' h' + (minutes % 60 ? ' ' + String(minutes % 60).padStart(2, '0') : '') : minutes + ' min';

// The days stated for a week live in page memory while the screen is used.
function reorgPlan(week) {
  if (!app.reorg || app.reorg.week !== week) app.reorg = {week, days: {}, sheet: null};
  return app.reorg;
}
const reorgStated = plan => Object.values(plan.days).filter(item => item.kind !== 'as_planned');
async function reorgLoad(week) {
  const plan = reorgPlan(week), key = 'reorganize:' + week, asked = JSON.stringify(reorgStated(plan));
  plan.asked = asked;
  try {
    const data = await api('reorganize', {week, days: reorgStated(plan)});
    if (plan.asked !== asked) return;
    app.views[key] = data; delete app.failures[key];
  } catch (error) {
    if (plan.asked !== asked) return;
    if (app.views[key]) message(error.message); else app.failures[key] = error.message;
  }
  if (screen().key === key) render(scrollY);
}
function reorgSet(week, date, stated) {
  const plan = reorgPlan(week);
  plan.days[date] = {date, ...stated};
  reorgSheet(week, date);
  reorgLoad(week);
}

// A day as the athlete stated it: the tag on the right, the details under the title.
function reorgStateLine(stated) {
  if (!stated || stated.kind === 'as_planned') return '';
  if (stated.kind === 'short') return '≤ ' + reorgHours(stated.minutes);
  if (stated.kind === 'with_others') return '~' + reorgHours(stated.minutes) + ', ' + REORG_PACE[stated.pace].toLowerCase() + (stated.required ? ' · obligatoire' : ' · si ça rentre');
  if (stated.kind === 'other_sport') return [stated.sport, stated.demanding ? 'exigeant' : 'léger', stated.run_too ? 'course possible' : null].filter(Boolean).join(' · ');
  return '';
}
function reorgDayRow(week, item, stated) {
  const sessions = item.sessions;
  const title = sessions.length ? sessions.map(planned => planned.title.split(',')[0]).join(' + ') : item.rest ? 'Repos' : 'Rien de prévu';
  const line = [sessions.length ? sessions[0].figure : '', reorgStateLine(stated)].filter(Boolean).join(' · ');
  const set = stated && stated.kind !== 'as_planned';
  const row = el(item.past ? 'div' : 'button', null, 'n-rg-day' + (set ? ' set' : '') + (item.past ? ' past' : '') + (item.date === (app.today && app.today.date) ? ' today' : ''));
  const icon = sessions.length ? pastille(kindIcon(sessions[0].kind, sessions[0].workout)) : pastille('rest');
  add(row, add(el('span', null, 'n-kd'), el('b', String(day(item.date).getDate())), reorgWeekday(item.date)),
    add(el('span', null, 'n-rg-what' + (sessions.length ? '' : ' blank')), icon, add(el('span', title), line ? el('small', line) : null)),
    set ? el('span', REORG_LABEL[stated.kind], 'n-rg-tag') : el('span'), item.past ? el('span') : chevron());
  if (!item.past) {
    row.type = 'button';
    row.addEventListener('click', () => { reorgPlan(week).sheet = item; reorgSheet(week, item.date); present($('day-sheet')); });
  }
  return row;
}

// ------------------------------------------------------------ the day sheet
function reorgSeg(items, chosen, pick) {
  const seg = el('div', null, 'n-seg');
  items.forEach(([value, label]) => {
    const choice = button(label, () => pick(value));
    choice.setAttribute('aria-pressed', String(value === chosen));
    seg.append(choice);
  });
  return seg;
}
const reorgCell = (label, right) => add(el('div', null, 'n-cell'), el('span', label), right);
const reorgHead = text => el('p', text, 'n-rg-gh');
function reorgCheck() {
  const mark = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  mark.setAttribute('viewBox', '0 0 24 24'); mark.setAttribute('class', 'n-rg-check'); mark.setAttribute('aria-hidden', 'true');
  mark.innerHTML = '<path d="M5 12.5l4.5 4.5L19 7.5"/>';
  return mark;
}
const REORG_DEFAULTS = {
  short: {minutes: 45},
  with_others: {minutes: 60, pace: 'easy', required: false},
  other_sport: {sport: 'Autre sport', demanding: false, run_too: false},
};
function reorgSheet(week, date) {
  const plan = reorgPlan(week), item = plan.sheet, panel = $('day-panel');
  if (!item || item.date !== date) return;
  const stated = plan.days[date] || {date, kind: 'as_planned'};
  const set = change => reorgSet(week, date, {...stated, ...change});
  $('day-title').textContent = day(date).toLocaleDateString('fr-FR', {weekday: 'long', day: 'numeric'}).replace(/^./, c => c.toUpperCase());
  panel.replaceChildren();
  if (item.sessions.length) {
    panel.append(reorgHead('Prévu'));
    panel.append(add(el('div', null, 'n-group'), ...item.sessions.map(planned => add(el('div', null, 'n-cell'),
      add(el('span', null, 'n-rg-pl'), pastille(kindIcon(planned.kind, planned.workout)), el('span', planned.title)),
      el('small', planned.figure)))));
  }
  panel.append(reorgHead('Ce jour-là'));
  panel.append(add(el('div', null, 'n-group'), ...REORG_STATES.map(([kind, label]) => {
    const row = button('', () => reorgSet(week, date, {kind, ...(REORG_DEFAULTS[kind] || {})}), 'n-cell n-rg-stated');
    add(row, el('span', label), kind === stated.kind ? reorgCheck() : null);
    row.setAttribute('aria-pressed', String(kind === stated.kind));
    return row;
  })));
  if (stated.kind === 'short') {
    panel.append(reorgHead('Le temps'));
    panel.append(add(el('div', null, 'n-group'), reorgCell('Au plus', reorgSeg([[30, '30 min'], [45, '45 min'], [60, '1 h']], stated.minutes, minutes => set({minutes})))));
    panel.append(el('p', 'Seule une séance qui tient dans ce temps s’y place.', 'n-small n-rg-foot'));
  }
  if (stated.kind === 'with_others') {
    panel.append(reorgHead('Le groupe'));
    panel.append(add(el('div', null, 'n-group'),
      reorgCell('Durée', reorgSeg([[45, '45 min'], [60, '1 h'], [90, '1 h 30'], [120, '2 h +']], stated.minutes, minutes => set({minutes}))),
      reorgCell('Allure', reorgSeg(Object.entries(REORG_PACE), stated.pace, groupPace => set({pace: groupPace}))),
      reorgCell('Rendez-vous', reorgSeg([[false, 'Si ça rentre'], [true, 'Obligatoire']], stated.required, required => set({required})))));
    panel.append(el('p', 'Une séance qui va avec le groupe s’y place : une EF ou une SL pour une allure tranquille, une séance de qualité seulement pour une séance de club.', 'n-small n-rg-foot'));
  }
  if (stated.kind === 'other_sport') {
    panel.append(reorgHead('Le sport'));
    const name = el('input', null, 'n-rg-name');
    name.type = 'text'; name.maxLength = 40; name.value = stated.sport; name.setAttribute('aria-label', 'Nom du sport');
    name.addEventListener('change', () => { if (name.value.trim()) set({sport: name.value.trim()}); });
    const runToo = el('input', null, 'n-switch');
    runToo.type = 'checkbox'; runToo.checked = stated.run_too; runToo.setAttribute('aria-label', 'Courir aussi ce jour-là');
    runToo.addEventListener('change', () => set({run_too: runToo.checked}));
    panel.append(add(el('div', null, 'n-group'), reorgCell('Nom', name),
      reorgCell('Effort', reorgSeg([[false, 'Léger'], [true, 'Exigeant']], stated.demanding, demanding => set({demanding}))),
      reorgCell('Courir aussi ce jour-là', runToo)));
    panel.append(el('p', 'Exigeant : il compte comme un jour dur, donc pas de séance exigeante la veille ni le lendemain. Léger : le jour est seulement pris.', 'n-small n-rg-foot'));
  }
}

// ------------------------------------------------------------ the variants
function reorgNote(note) {
  const when = note.date ? reorgDay(note.date) : '';
  if (note.remark === 'impact_near_hard') return note.title + ' (avec sauts) tombe la veille ou le jour d’une séance exigeante.';
  if (note.remark === 'load_above_plan') return 'Trois jours plus chargés que prévu (+' + note.value + ' %).';
  if (note.remark === 'long_off_days') return note.title + ' quitte ses jours habituels (' + when + ').';
  if (note.remark === 'group_uncarried') return 'Aucune séance ne va avec le groupe du ' + when + '.';
  return note.title + ' tombe sur un jour de repos prévu (' + when + ').';
}
function reorgSummary(variant) {
  const parts = [];
  if (variant.moves.length) parts.push(variant.moves.length === 1 ? '1 date change' : variant.moves.length + ' dates changent');
  if (variant.dropped.length) parts.push(variant.dropped.length === 1 ? '1 saute' : variant.dropped.length + ' sautent');
  return parts.join(' · ') || 'comme prévu';
}
function reorgCard(data, variant, index) {
  const card = add(el('div', null, 'n-rg-var'), add(el('p', null, 'n-rg-head'), el('span', 'Variante ' + (index + 1)), el('small', reorgSummary(variant))));
  const top = Math.max(1, ...variant.days.map(item => item.load));
  const grid = el('div', null, 'n-rg-grid');
  variant.days.forEach(item => {
    const past = data.days.find(other => other.date === item.date).past;
    const cell = el('div', null, 'n-rg-cell' + (item.moved ? ' moved' : '') + (!item.labels.length ? ' off' : '') + (past ? ' past' : ''));
    const bar = el('div', null, 'n-bar' + (!item.labels.length ? ' flat' : item.moved ? '' : ' plan'));
    if (item.labels.length) bar.style.height = (10 + 62 * item.load / top) + 'px';
    add(cell, el('em', item.labels.join('+') || '—'), item.carried ? el('i', 'groupe', 'grp') : null, bar,
      el('b', String(day(item.date).getDate())), el('span', reorgWeekday(item.date)));
    grid.append(cell);
  });
  const lines = el('div', null, 'n-rg-lines');
  variant.moves.forEach(move => lines.append(add(el('p'), el('b', move.title.split(',')[0]), el('span', reorgDay(move.before) + ' → ' + reorgDay(move.after)))));
  variant.dropped.forEach(drop => lines.append(add(el('p', null, 'left'), el('b', drop.title.split(',')[0]), el('span', 'saute' + (drop.km ? ' · −' + fmt(drop.km) + ' km' : '')))));
  add(card, grid, lines.childNodes.length ? lines : null);
  if (variant.around_min) card.append(el('p', 'Environ ' + reorgHours(variant.around_min) + ' à courir autour du groupe, en allant le rejoindre et en rentrant.', 'n-rg-why'));
  variant.notes.forEach(note => card.append(el('p', reorgNote(note), 'n-rg-why')));
  const take = button('Prendre cette variante', () => reorgTake(data, index, take), 'n-action');
  card.append(take);
  return card;
}
async function reorgTake(data, index, pressed) {
  pressed.disabled = true;
  try {
    await api('reorganize/take', {week: data.week, days: reorgStated(reorgPlan(data.week)), revision: data.revision, choice: index});
    app.reorg = null; delete app.views['reorganize:' + data.week];
    message('Semaine réorganisée. Le coach la voit à ton prochain message.');
    leave(); refresh(true);
  } catch (error) {
    pressed.disabled = false; message(error.message); reorgLoad(data.week);
  }
}

export function renderReorganize(view) {
  const data = app.views[view.key], plan = reorgPlan(view.week);
  if (!data) {
    if (!app.failures[view.key]) reorgLoad(view.week);
    const wait = el('p', app.failures[view.key] || 'Calcul des variantes…', 'n-meta');
    content.append(wait);
    return;
  }
  content.append(el('p', 'Touche un jour pour dire comment il se présente. Seules les dates changent ; pour changer une séance, c’est le coach.', 'n-rg-intro'));
  if (data.blocked) { content.append(el('p', REORG_BLOCKED[data.blocked], 'n-banner')); return; }
  const list = el('div', null, 'n-rg-days');
  data.days.forEach(item => list.append(reorgDayRow(view.week, item, plan.days[item.date])));
  content.append(list);
  if (!reorgStated(plan).length) return;
  const best = data.variants[0];
  if (best && !best.moves.length && !best.dropped.length) {
    // The days stated leave the plan as it is: any other placement would only shuffle it.
    content.append(el('p', 'Ta semaine tient déjà avec ces jours : rien à déplacer.', 'n-banner'));
    return;
  }
  if (!data.variants.length) {
    content.append(el('p', 'Aucune variante ne respecte les règles avec ces jours. Dis-le au coach dans Messenger : lui seul peut changer une séance.', 'n-banner'));
  } else {
    const node = section(data.variants.length === 1 ? '1 variante' : data.variants.length + ' variantes');
    data.variants.forEach((variant, index) => node.append(reorgCard(data, variant, index)));
    content.append(node);
  }
  if (data.held_back.length) content.append(el('p', data.held_back.join(', ') + ' ne peut pas être avancé : un signal de douleur est actif. Le reculer reste possible ; l’avancer demande l’avis du coach.', 'n-banner'));
  if (data.others) content.append(el('p', data.others + (data.others === 1 ? ' autre placement respecte' : ' autres placements respectent') + ' les règles ; ils changent plus de dates ou plient plus de préférences.', 'n-rg-more'));
}
