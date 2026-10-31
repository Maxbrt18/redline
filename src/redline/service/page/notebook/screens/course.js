import {add, button, el} from '../../shared/dom.js';
import {SVG, chevron, content, pastille, section, shape} from '../ui.js';
import {day, dm, fmt, shortDay} from '../format.js';
import {push} from '../nav.js';
import {raceSummary} from './raceplan.js';

// ------------------------------------------------------------ Course
const archiveLabel = entry => entry.kind === 'monthly' && entry.period
  ? 'Monthly · ' + new Date(entry.period + '-01T12:00:00').toLocaleDateString('fr-FR', {month: 'long', year: 'numeric'})
  : entry.kind === 'weekly' && entry.period ? 'Weekly · W' + entry.period.slice(-2) : entry.title;
// Each week's volume against its target, and its long run on its own line of figures.
// Nothing is stacked: the bar says where the week stands, the line below gives its long
// run. The current week's long run is the programmed one until it has been run.
function volumeChart(longest) {
  const weeks = longest.weeks, width = Math.min(content.clientWidth || 353, 580);
  const top = 30, base = 140, bar = 32, band = width / weeks.length;
  const peak = Math.max(1, ...weeks.map(item => Math.max(item.volume || 0, item.target || 0)));
  const y = km => base - (base - top) * km / peak;
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 ' + width + ' ' + (base + 80)); svg.setAttribute('class', 'n-vchart');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Volume de chaque semaine contre son objectif, et sa sortie longue');
  weeks.forEach((item, index) => {
    const cx = band * (index + .5), x = cx - bar / 2, now = item.open ? ' now' : '';
    if (item.target) shape('rect', {x, y: y(item.target), width: bar, height: base - y(item.target), rx: 4, class: 'track'}, svg);
    if (item.volume) shape('rect', {x, y: y(item.volume), width: bar, height: base - y(item.volume), rx: 4, class: 'done'}, svg);
    const head = shape('text', {x: cx, y: y(Math.max(item.volume || 0, item.target || 0)) - 7, 'text-anchor': 'middle', class: 'vl'}, svg, fmt(item.volume || 0));
    if (item.target) shape('tspan', {class: 't'}, head, '/' + fmt(item.target));
    shape('text', {x: cx, y: base + 17, 'text-anchor': 'middle', class: 'wl' + now}, svg, 'W' + item.week.slice(-2));
    const planned = item.open && longest.planned_km && longest.planned_km > item.km;
    const long = planned ? longest.planned_km : item.km;
    shape('text', {x: cx, y: base + 62, 'text-anchor': 'middle', class: 'slv' + (planned ? ' plan' : '')}, svg, long ? fmt(long) : '—');
    if (planned) shape('text', {x: cx, y: base + 76, 'text-anchor': 'middle', class: 'sls'}, svg,
      'prévue ' + day(longest.planned_on).toLocaleDateString('fr-FR', {weekday: 'short'}));
  });
  shape('line', {x1: 0, x2: width, y1: base, y2: base, class: 'axis'}, svg);
  shape('line', {x1: 0, x2: width, y1: base + 30, y2: base + 30, class: 'sep'}, svg);
  shape('text', {x: 0, y: base + 44, class: 'cap'}, svg, 'sortie longue, en km');
  return svg;
}
export function renderCourse(data) {
  const race = data.race;
  if (race) {
    const head = add(el('div', null, 'n-head'), el('h1', 'J-' + race.days));
    if (race.target) head.append(add(el('div', null, 'goal'), el('div', 'cible', 'n-meta'), el('b', race.target)));
    content.append(head);
    if (data.plan) content.append(raceSummary(data.plan));
  } else content.append(el('p', 'Aucune course datée.', 'n-meta'));
  const longest = data.longest;
  if (longest.weeks.length) {
    const node = section('Volume et sortie longue');
    node.append(volumeChart(longest));
    if (longest.weeks.some(item => item.target)) {
      const legend = el('p', null, 'n-vleg');
      [['done', 'fait'], ['track', 'objectif de la semaine']].forEach(([cls, text]) => add(legend, add(el('span'), el('i', null, cls), text)));
      node.append(legend);
    }
    content.append(node);
  }
  if (data.milestones.length) {
    const node = section('Prochains jalons'), line = el('div', null, 'n-timeline');
    let first = true;
    data.milestones.forEach(item => {
      const when = item.kind === 'deadline'
        ? (item.overdue ? 'en retard · ' + dm(item.date) : 'avant le ' + dm(item.date))
        : shortDay(item.date) + (item.start_time ? ' · ' + item.start_time.replace(':00', ' h') : '');
      const cls = 'n-tl' + (item.kind === 'race' ? ' goal' : first && !item.overdue ? ' now' : '');
      if (!item.overdue && item.kind !== 'race') first = false;
      line.append(add(el('div', null, cls), el('p', when, 'w' + (item.overdue ? ' n-over' : '')), el('p', item.label + (item.km ? ', ' + fmt(item.km) + ' km' : ''), 'x')));
    });
    node.append(line); content.append(node);
  }
  if (data.capabilities.length) {
    const node = section('Capacités course');
    data.capabilities.forEach(item => {
      const pips = el('div', null, 'n-pips');
      for (let index = 0; index < item.scale; index++) pips.append(el('i', null, index < item.score ? 'f' : null));
      node.append(add(el('div', null, 'n-cp'), add(el('span'), pastille(item.icon, true), item.label), pips, el('b', String(item.score))));
    });
    const latest = data.capabilities.map(item => item.evaluated_on).sort().pop();
    node.append(el('p', 'Évaluées le ' + dm(latest) + ', au bilan.', 'n-meta'));
    content.append(node);
  }
  const node = section('Archives', el('span', 'bilans, monthly', 'n-meta'));
  data.archives.forEach(entry => {
    const label = archiveLabel(entry);
    node.append(add(button('', () => push({k: 'entry', id: entry.id, title: label}), 'n-list'), el('span', label), chevron()));
  });
  node.append(add(button('', () => push({k: 'archive'}), 'n-list'), el('span', 'Toutes les archives'), chevron()));
  content.append(node);
}
