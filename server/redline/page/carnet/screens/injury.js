import {add, button, el} from '../../shared/dom.js';
import {SVG, content, shape, zone} from '../ui.js';
import {day, fmt, weekNumber} from '../format.js';
import {swap} from '../nav.js';

// ------------------------------------------------------------ Injury
// Poussé depuis la tuile des repères (maquette validée le 2026-10-09) : un point par
// séance notée et chaque réveil chiffré sur une même échelle graduée (2026-10-10 : les
// réveils posés sous l'axe, sans valeur, ne se lisaient pas), le plafond en pointillé,
// l'échelle de Sam en ses mots, puis le pic par semaine et les réveils sans note. Des
// faits, aucun verdict.
function painChart(data) {
  // `left` : la gouttière des graduations ; le tracé commence après elle.
  const width = Math.min(content.clientWidth || 353, 580), base = 128, days = 7 * data.weeks.length - 1, left = 18;
  const start = day(data.start).getTime();
  const span = width - left - 8;
  const x = value => left + 4 + (day(value).getTime() - start) / 86400000 * span / days;
  const y = value => 8 + (3 - Math.min(3, value)) / 3 * 120;
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 ' + width + ' ' + (base + 24)); svg.setAttribute('class', 'n-pchart');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Douleur au pic de chaque séance sur ' + data.weeks.length + ' semaines, plafond ' + fmt(data.cap));
  const future = x(data.today) + 5;
  if (future < width) shape('rect', {x: future, y: 8, width: width - future, height: 120, class: 'future'}, svg);
  [1, 3].forEach(value => shape('line', {x1: left, x2: width, y1: y(value), y2: y(value), class: 'grid'}, svg));
  [0, 1, 2, 3].forEach(value => shape('text', {x: left - 8, y: y(value) + 4, 'text-anchor': 'end', class: 'yl'}, svg, String(value)));
  shape('line', {x1: left, x2: width, y1: y(0), y2: y(0), class: 'axis'}, svg);
  shape('line', {x1: left, x2: width, y1: y(data.cap), y2: y(data.cap), class: 'cap'}, svg);
  shape('text', {x: width, y: y(data.cap) - 5, 'text-anchor': 'end', class: 'capl'}, svg).textContent = 'plafond ' + fmt(data.cap);
  data.weeks.forEach((item, index) => {
    const from = left + 4 + index * 7 * span / days - 3;
    if (index) shape('line', {x1: from, x2: from, y1: 8, y2: y(0), class: 'sep'}, svg);
    shape('text', {x: from + 3.5 * span / days, y: y(0) + 16, 'text-anchor': 'middle', class: 'wl'}, svg, 'W' + item.week.slice(-2));
  });
  data.points.forEach(item => shape('circle', {cx: x(item.date), cy: y(item.pain), r: item.long ? 6 : 4.2,
    class: (item.long ? 'sl' : 'run') + (item.pain > data.cap ? ' out' : '')}, svg));
  // Le réveil d'un jour, juste avant la séance de ce jour : le matin vient d'abord.
  data.wakes.forEach(item => shape('rect', {x: x(item.date) - 8.5, y: y(item.pain) - 3.5, width: 7, height: 7, rx: 1.5, class: 'wk'}, svg));
  return svg;
}
// Plusieurs zones surveillées : un sélecteur en tête, une zone affichée à la fois.
function zonePicker(data) {
  const row = el('div', null, 'n-choices' + (data.zones.length === 2 ? ' two' : ''));
  data.zones.forEach(item => row.append(button(item.label, () => swap({k: 'injury', zone: item.zone}),
    item.zone === data.zone ? 'n-picked' : '')));
  return row;
}
export function renderInjury(data) {
  if (data.zones.length > 1) content.append(zonePicker(data));
  const current = data.weeks[data.weeks.length - 1] || {};
  add(content, add(el('div', null, 'n-tbig'), el('b', current.peak == null ? '—' : fmt(current.peak), current.peak > data.cap ? 'n-over' : null),
    add(el('span'), 'pic cette semaine', el('br'), 'plafond ', el('b', fmt(data.cap)))));
  content.append(painChart(data));
  const legend = el('p', null, 'n-pleg');
  [['run', 'séance'], ['sl', 'sortie longue'], ['wk', 'réveil']].forEach(([cls, text]) => add(legend, el('i', null, cls), text));
  content.append(legend);
  if (data.wakes.length) content.append(el('p', 'Le réveil d’un jour est placé juste avant la séance de ce jour.', 'n-pnote'));
  const scale = zone('Ton échelle');
  data.scale.forEach(item => scale.append(add(el('p', null, 'n-scale'), el('b', String(item.value)), el('span', item.text))));
  const table = zone('Par semaine');
  table.append(add(el('div', null, 'n-trow head'), el('span', 'Semaine'), el('span', 'Pic'), el('span', 'Séances'), el('span', 'Réveil')));
  [...data.weeks].reverse().forEach(item => table.append(add(el('div', null, 'n-trow'),
    add(el('span'), el('b', String(weekNumber(item.week))), item.week === data.week ? el('small', 'en cours') : null),
    el('span', item.peak == null ? '—' : fmt(item.peak), 'pk' + (item.peak > data.cap ? ' n-over' : '')),
    el('span', String(item.n)), el('span', item.wake == null ? '—' : fmt(item.wake)))));
  add(content, scale, table);
  if (data.missing.length) {
    const missing = zone('À renseigner');
    data.missing.forEach(date => missing.append(add(el('div', null, 'n-miss'),
      el('span', 'Réveil du ' + day(date).toLocaleDateString('fr-FR', {day: 'numeric', month: 'long'})), el('small', 'sans note chiffrée'))));
    content.append(missing);
  }
}
