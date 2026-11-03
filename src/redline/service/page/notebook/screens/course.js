import {add, button, el} from '../../shared/dom.js';
import {chevron, content, hero, pastille, section} from '../ui.js';
import {dm, fmt, shortDay} from '../format.js';
import {push} from '../nav.js';
import {raceProfile, raceSummary} from './raceplan.js';

// ------------------------------------------------------------ Course
const archiveLabel = entry => entry.kind === 'monthly' && entry.period
  ? 'Monthly · ' + new Date(entry.period + '-01T12:00:00').toLocaleDateString('fr-FR', {month: 'long', year: 'numeric'})
  : entry.kind === 'weekly' && entry.period ? 'Weekly · W' + entry.period.slice(-2) : entry.title;
export function renderCourse(data) {
  const race = data.race;
  if (race) {
    // On the track: the days left, then the course itself.
    hero.append(add(el('p', null, 'n-hfig'), el('b', 'J-' + race.days), race.target ? el('span', 'cible ' + race.target) : null));
    if (data.plan) hero.append(raceProfile(data.plan));
    if (data.plan) content.append(raceSummary(data.plan));
  } else content.append(el('p', 'Aucune course datée.', 'n-meta'));
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
