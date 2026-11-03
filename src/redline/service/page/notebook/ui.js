// What all notebook screens share: the content area, the transient message,
// the section titles.
import {$, add, el} from '../shared/dom.js';
import {fmt, rpeRange} from './format.js';

export const content = $('content');
// The head of the screen under the title: on the three tabs it sits on the track
// (shell.css, `.n-piste`), on a pushed screen on paper.
export const hero = $('hero');
// A transient message floats above the tabs: it never pushes the page.
// `notice` stays in the flow, for the opening and the locked screen.
let toastTimer = null;
export function message(text) {
  const toast = $('toast');
  clearTimeout(toastTimer);
  toast.textContent = text || ''; toast.hidden = !text;
  if (text) toastTimer = setTimeout(() => { toast.hidden = true; }, Math.max(2800, 60 * text.length));
}
export function notice(text) { $('notice').textContent = text; $('notice').hidden = !text; }
export function section(title, aside) {
  const node = el('section', null, 'n-sec');
  const head = add(el('div', null, 'n-row'), el('h2', title));
  if (aside) head.append(aside);
  node.append(head); return node;
}
export function zone(title) {
  return add(el('section', null, 'n-zone'), el('h2', title, 'n-zh'));
}
// Installation is not optional: it is a step, offered until done,
// where the notebook is opened each day, then never again.
export const installed = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

// An SVG element, its attributes, its optional text, attached to its parent.
export const SVG = 'http://www.w3.org/2000/svg';
export function shape(tag, attrs, parent, text) {
  const node = document.createElementNS(SVG, tag);
  Object.entries(attrs).forEach(([name, value]) => node.setAttribute(name, String(value)));
  if (text != null) node.textContent = text;
  parent.append(node); return node;
}
// The notebook's only chevron: a drawn stroke, never the character « › ».
export function chevron(cls) {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 8 13'); svg.setAttribute('class', 'n-chevron' + (cls ? ' ' + cls : '')); svg.setAttribute('aria-hidden', 'true');
  shape('path', {d: 'M1.5 1.5 6.5 6.5 1.5 11.5'}, svg);
  return svg;
}
// Tinted badges: a light disc and its icon name the family of a session, a
// landmark or a capability, like the weather ones. They judge nothing.
const PAS_ICONS = {
  run: '<path d="M3.5 17.5v-6.3c0-.6.5-1 1.1-.9l2.9.7 2.4-3.4 3.6 2.1c.8 1.9 2.6 3.1 4.9 3.3 1.1.1 2.1 1 2.1 2.2v2.3z"/><path d="M9.3 11.2l1.6 1M11.2 9.7l1.5 1"/>',
  long: '<circle cx="6" cy="18.5" r="1.6"/><circle cx="18" cy="5.5" r="1.6"/><path d="M7.6 18.5h8.9a3 3 0 0 0 0-6h-9a3 3 0 0 1 0-6h8.9"/>',
  str: '<path d="M7 7v10M17 7v10M4.5 9.5v5M19.5 9.5v5M7 12h10"/>',
  rest: '<path d="M8.5 9h5.5l-5.5 6.5h5.5"/><path d="M15.5 4.5h3.5l-3.5 4h3.5"/>',
  other: '<path d="M3.5 12h4l2-5 4 10 2-5h5"/>',
  week: '<path d="M4 20v-5M8 20v-8M12 20v-3M16 20v-10M20 20V6"/>',
  pain: '<path d="M10 3.5v8c0 1.3-.4 2.5-1.1 3.6l-1.6 2.6c-.6 1 .1 2.3 1.3 2.3h6.9"/><path d="M14.5 3.5v9.5l1.5 7"/>',
  race: '<path d="M6 21V4"/><path d="M6 4.5h11.5l-2.6 4 2.6 4H6"/>',
  descent: '<path d="M3.5 6.5l7 7 3-3 7 7"/><path d="M15.5 17.5h5v-5"/>',
  food: '<path d="M7 3.5v17M4.5 3.5v5a2.5 2.5 0 0 0 5 0v-5"/><path d="M17 20.5v-17c-2.2 1.4-3.5 4-3.5 7v3.5H17"/>',
  night: '<path d="M19 14.5A7.5 7.5 0 0 1 9.5 5a7.5 7.5 0 1 0 9.5 9.5z"/>',
  mind: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".6"/>',
};
// The tint follows the family; the icon may refine it (the long outing remains a run).
const PAS_HUES = {long: 'run', week: 'run', descent: 'run', food: 'race', night: 'race', mind: 'run'};
export function pastille(icon, small) {
  const node = el('span', null, 'n-pas ' + (PAS_HUES[icon] || icon) + (small ? ' sm' : ''));
  node.setAttribute('aria-hidden', 'true');
  node.innerHTML = '<svg viewBox="0 0 24 24">' + (PAS_ICONS[icon] || PAS_ICONS.other) + '</svg>';
  return node;
}
// `kind` is the family of a program session (run, strength…) or the type of a
// ledger session (easy, long…); both reduce to a family.
const familyOf = kind => (window.REDLINE_KINDS || {})[kind] ? window.REDLINE_KINDS[kind].family : kind;
export const kindIcon = (kind, workout) => kind === 'long' || workout === 'long' ? 'long'
  : familyOf(kind) === 'strength' ? 'str' : familyOf(kind) === 'rest' ? 'rest'
  : familyOf(kind) === 'run' || familyOf(kind) === 'test' ? 'run' : 'other';

// The cursor: a rail, what was done in ink, the allowed band in dots, the limit as a red
// stroke that names its value, what passes it in red dots. A target that is not a limit
// is an ink tick. `scale` labels the rail's ends (and its middle when given three).
export function cursor({label, value, unit: suffix = null, lo, hi, fill = null, cap = null, capLabel = null,
  target = null, targetLabel = null, band = null, scale = null, below = false}) {
  const at = number => Math.max(0, Math.min(100, 100 * (number - lo) / (hi - lo)));
  const over = fill != null && cap != null && fill > cap;
  const head = add(el('div', null, 'n-cur-head'), el('span', label), add(el('b', value, over ? 'n-over' : null), suffix ? el('small', suffix) : null));
  const rail = el('div', null, 'n-cur-rail');
  const part = (cls, left, width) => { const node = el('span', null, cls); node.style.left = left + '%'; if (width != null) node.style.width = width + '%'; rail.append(node); return node; };
  if (band) part('band', at(band[0]), at(band[1]) - at(band[0]));
  if (fill != null) part('fill', 0, at(over ? cap : fill));
  if (over) part('ex', at(cap), at(fill) - at(cap));
  if (target != null) add(part('tgt' + (below ? ' below' : ''), at(target)), targetLabel ? el('em', targetLabel) : null);
  if (cap != null) add(part('lim', at(cap)), el('em', capLabel == null ? String(cap) : capLabel));
  const ends = add(el('div', null, 'n-cur-scale'), ...(scale || [lo, hi]).map(text => el('span', String(text))));
  return add(el('div', null, 'n-cur' + (over ? ' crossed' : '')), head, rail, ends);
}
// Planned and done, one line per measure: every limit sits at the same place on its
// rail, so the limits read as one red stroke down the block. What passes it spills in
// red dots and its figure turns --over. A target that is not a limit is an ink tick.
export function limits(rows) {
  const block = el('div', null, 'n-pf');
  if (rows.some(row => row.cap != null)) block.append(add(el('p', null, 'n-pfh'), el('span'), el('em', 'limite'), el('span')));
  rows.forEach(row => {
    const mark = row.cap != null ? row.cap : row.target, lo = row.lo || 0;
    const hi = lo + (mark - lo) / 0.75;
    const at = number => Math.max(0, Math.min(100, 100 * (number - lo) / (hi - lo)));
    const over = row.cap != null && row.fill != null && row.fill > row.cap;
    const rail = el('span', null, 'n-pf-rail');
    const part = (cls, left, width) => { const node = el('span', null, cls); node.style.left = left + '%'; if (width != null) node.style.width = width + '%'; rail.append(node); };
    if (row.band != null) part('band', at(row.band), 75 - at(row.band));
    if (row.fill != null) part('fill', 0, over ? 75 : at(row.fill));
    if (over) part('ex', 75, at(row.fill) - 75);
    part(row.cap != null ? 'lim' : 'tick', 75);
    block.append(add(el('div', null, 'n-pfr' + (over ? ' over' : '') + (row.cap == null ? ' tg' : '')),
      el('span', row.label, 'n-pf-l'), rail, add(el('span', null, 'n-pf-v'), el('b', row.value), el('small', row.sub))));
  });
  return block;
}
// The limits of a session as rows: done when there is a fact, planned otherwise.
export function sessionLimits(target, fact) {
  const rows = [], has = value => value != null;
  const num = (value, digits = 1) => fmt(value, digits);
  if (has(target.distance_cap_km) || has(target.distance_target_km)) {
    const cap = target.distance_cap_km, aim = target.distance_target_km;
    rows.push({label: 'Distance', lo: (cap || aim) / 2, cap, target: cap == null ? aim : null, fill: fact ? fact.km : null,
      value: fact && has(fact.km) ? num(fact.km, 2) : num(aim != null ? aim : cap), sub: cap != null ? num(cap) + ' max' : 'visés'});
  }
  if (has(target.duration_cap_min)) rows.push({label: 'Durée', lo: target.duration_cap_min / 3, cap: target.duration_cap_min,
    fill: fact ? fact.duration_min : null, value: (fact && has(fact.duration_min) ? fact.duration_min : target.duration_cap_min) + '′',
    sub: target.duration_cap_min + '′ max'});
  if (has(target.hr_cap_bpm)) rows.push({label: 'FC moy.', lo: target.hr_cap_bpm - 30, cap: target.hr_cap_bpm,
    fill: fact ? fact.hr_avg : null, value: fact && has(fact.hr_avg) ? num(fact.hr_avg, 0) : String(target.hr_cap_bpm), sub: target.hr_cap_bpm + ' max'});
  if (has(target.rpe_max)) rows.push({label: 'Effort', cap: target.rpe_max, band: target.rpe_min, fill: fact ? fact.rpe : null,
    value: fact && has(fact.rpe) ? num(fact.rpe) : rpeRange(target), sub: 'visé ' + rpeRange(target)});
  return rows.length ? limits(rows) : null;
}
