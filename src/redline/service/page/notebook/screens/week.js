import {add, button, el} from '../../shared/dom.js';
import {app} from '../state.js';
import {chevron, content, kindIcon, pastille, section} from '../ui.js';
import {day, dm, fmt, isoDate, kindName, plannedValue, shortTitle, weekNumber, weekSpan} from '../format.js';
import {push} from '../nav.js';

// ------------------------------------------------------------ Week
// One figure, one bar, seven lines. Each day fits on one line: the session and a single
// figure, the fact in ink, the plan in grey, an overshoot in one red phrase. The detail
// unfolds on touch, one day at a time.
// A gauge: it fills up to the fact, a tick marks the limit, and what exceeds it turns red.
// No knob: a knob on a rail reads as a slider that could be dragged.
function gauge(label, low, high, cap, value, shown, limit, out, floor) {
  const at = number => Math.max(0, Math.min(100, 100 * (number - low) / (high - low)));
  const rail = el('span', null, 'n-rail'), fill = el('i');
  fill.style.width = at(Math.min(value, cap)) + '%';
  rail.append(fill);
  if (value > cap) {
    const beyond = el('i', null, 'out');
    beyond.style.left = at(cap) + '%'; beyond.style.width = (at(value) - at(cap)) + '%';
    rail.append(beyond);
  }
  const mark = el('u'); mark.style.left = at(cap) + '%'; rail.append(mark);
  if (floor != null) { const low = el('u', null, 'floor'); low.style.left = at(floor) + '%'; rail.append(low); }
  const right = add(el('span', null, 'n-gv'), el('b', shown, out ? 'n-over' : null), el('span', limit));
  return add(el('div', null, 'n-g'), el('span', label), rail, right);
}
// The gauges of a completed session.
export function gauges(item) {
  const fact = item.fact, target = item.targets || {}, over = new Set(fact.over || []), rows = [];
  const distanceCap = target.distance_cap_km || target.distance_target_km;
  if (distanceCap && fact.km != null) {
    rows.push(gauge('Distance', 0, Math.max(distanceCap * 1.25, fact.km * 1.1), distanceCap, fact.km, fmt(fact.km), '≤' + fmt(distanceCap), over.has('km')));
  }
  if (target.duration_cap_min && fact.duration_min != null && (!distanceCap || over.has('duration_min'))) {
    rows.push(gauge('Durée', 0, Math.max(target.duration_cap_min * 1.3, fact.duration_min * 1.1), target.duration_cap_min, fact.duration_min, fact.duration_min + '′', '≤' + target.duration_cap_min, over.has('duration_min')));
  }
  if (target.hr_cap_bpm && fact.hr_avg != null) {
    rows.push(gauge('FC moy', 120, Math.max(180, fact.hr_avg + 5), target.hr_cap_bpm, fact.hr_avg, fmt(fact.hr_avg, 0), '≤' + target.hr_cap_bpm, over.has('hr_avg')));
  }
  if (target.rpe_max != null && fact.rpe != null) {
    const low = target.rpe_min != null && target.rpe_min !== target.rpe_max ? target.rpe_min : null;
    rows.push(gauge('RPE', 0, 10, target.rpe_max, fact.rpe, fmt(fact.rpe), low != null ? low + '-' + target.rpe_max : String(target.rpe_max), over.has('rpe'), low));
  }
  return rows.length ? add(el('div', null, 'n-gauges'), ...rows) : null;
}
// What overshot, as labels after the title: the gap to the limit, not a sentence. The
// detail screen shows it on its gauges.
function crossing(item) {
  const fact = item.fact, target = item.targets || {}, over = new Set(fact.over || []), tags = [];
  if (over.has('hr_avg')) tags.push('FC +' + fmt(fact.hr_avg - target.hr_cap_bpm, 0));
  if (over.has('rpe')) tags.push('RPE +' + fmt(fact.rpe - target.rpe_max));
  if (over.has('km')) tags.push('+' + fmt(fact.km - target.distance_cap_km) + ' km');
  if (over.has('duration_min')) tags.push('+' + (fact.duration_min - target.duration_cap_min) + ' min');
  return tags.map(text => el('span', text, 'n-cross'));
}
// A day: its programmed sessions and what was run outside the program.
function dayRow(date, items, extras, todayIso) {
  const live = items.filter(item => item.kind !== 'rest');
  const runs = live.filter(item => item.kind !== 'strength');
  const strength = live.filter(item => item.kind === 'strength');
  const main = runs[0] || strength[0];
  const past = date < todayIso;
  let title, value = '', cls = '', alert = '', tags = [], icons = [];
  // A completed outing opens on its detail; what accompanies it that day (strength,
  // unprogrammed) stays readable under it.
  let observed = null;
  const detail = [], beside = [];
  if (!main && !extras.length) { title = 'Repos'; cls = 'rest'; icons = ['rest']; }
  else if (!main) {
    const extra = extras[0];
    title = (kindName[extra.kind] || extra.kind) + ' · hors programme';
    icons = [kindIcon(extra.kind)];
    value = extra.km != null ? fmt(extra.km) + ' km' : extra.duration_min != null ? extra.duration_min + ' min' : '';
    observed = {id: extra.observation_id, title: kindName[extra.kind] || extra.kind};
  } else {
    const names = [shortTitle(main.title)];
    if (main.kind !== 'strength' && strength.length) names.push('renfo');
    title = names.join(' + ');
    icons = [kindIcon(main.kind, main.workout), main.kind !== 'strength' && strength.length ? 'str' : null].filter(Boolean);
    if (main.status === 'done') {
      value = main.fact && main.fact.km != null ? fmt(main.fact.km) + ' km' : main.fact && main.fact.duration_min != null ? main.fact.duration_min + ' min' : 'fait';
      if (main.fact && main.kind !== 'strength') {
        tags = crossing(main);
        observed = {id: main.fact.observation_id, title: main.title};
      }
    } else if (main.status === 'cancelled') { value = 'sauté'; cls = 'skip'; if (main.note) detail.push(el('p', main.note)); }
    else if (past) { value = 'pas de trace'; cls = 'plan'; }
    else { value = plannedValue(main); cls = 'plan'; if (main.status === 'held') alert = 'en attente d’une réponse dans Messenger'; }
    if (main.change) (observed ? beside : detail).push(el('p', main.change));
    strength.filter(item => item.status === 'done').forEach(item => {
      const line = el('p', 'Renfo · ' + (item.fact && item.fact.duration_min != null ? item.fact.duration_min + ' min' : 'fait'));
      if (item.entry_id) { line.append(' · '); line.append(button('compte rendu', () => push({k: 'entry', id: item.entry_id}), 'n-inline')); }
      (observed ? beside : detail).push(line);
    });
    if (cls === 'plan' && main.note && !past) detail.push(el('p', main.note));
    extras.forEach(extra => (observed ? beside : detail).push(el('p', 'Hors programme · ' + (kindName[extra.kind] || extra.kind) + (extra.km != null ? ' · ' + fmt(extra.km) + ' km' : ''))));
  }
  const pressable = observed || detail.length;
  const head = el(pressable ? 'button' : 'div', null, 'n-krow' + (cls ? ' ' + cls : '') + (date === todayIso ? ' today' : ''));
  const d = day(date);
  add(head, add(el('span', null, 'n-kd'), el('b', String(d.getDate())), d.toLocaleDateString('fr-FR', {weekday: 'short'}).replace('.', '')),
    add(el('span', null, 'n-kt'), add(el('span', null, 'n-pasg'), ...icons.map(icon => pastille(icon))),
      add(el('span'), title, ...tags, alert ? el('small', alert) : null)),
    add(el('span', null, 'n-kv'), value, pressable ? chevron(observed ? null : 'n-kchev') : null));
  const wrap = add(el('div', null, 'n-kday'), head);
  if (observed) {
    head.type = 'button';
    head.addEventListener('click', () => push({k: 'session', id: observed.id, title: observed.title}));
    if (beside.length) wrap.append(add(el('div', null, 'n-kmore'), ...beside));
  } else if (detail.length) {
    head.type = 'button';
    const panel = add(el('div', null, 'n-kmore'), ...detail);
    // The unfolded day stays unfolded when the figures are recomputed behind it.
    const opened = app.openDay === date;
    panel.hidden = !opened; head.setAttribute('aria-expanded', String(opened)); wrap.classList.toggle('open', opened);
    head.addEventListener('click', () => {
      const opening = panel.hidden;
      app.openDay = opening ? date : null;
      content.querySelectorAll('.n-kday').forEach(other => {
        const more = other.querySelector('.n-kmore'), row = other.querySelector('.n-krow');
        if (more) { more.hidden = true; row.setAttribute('aria-expanded', 'false'); other.classList.remove('open'); }
      });
      if (opening) { panel.hidden = false; head.setAttribute('aria-expanded', 'true'); wrap.classList.add('open'); }
    });
    wrap.append(panel);
  }
  return wrap;
}
// A past week on one line: its name, its dates, the fact against the target.
function weekRow(item) {
  const figure = el('span', null, 'n-wfig');
  if (item.km_target) {
    const meter = el('span', null, 'n-wmeter'), fill = el('i');
    fill.style.width = Math.min(100, 100 * item.km_done / item.km_target) + '%';
    figure.append(add(meter, fill));
  }
  figure.append(el('small', fmt(item.km_done) + (item.km_target ? ' / ' + fmt(item.km_target) : '') + ' km'));
  return add(button('', () => push({k: 'week', label: item.week}), 'n-wrow'),
    add(el('span'), 'Semaine ' + weekNumber(item.week), el('small', weekSpan(item.week))), figure, chevron());
}
// The week as bars, under its figure: solid = done, dashed = planned, hairline = rest.
function weekChart(days) {
  const scale = Math.max(1, ...days.map(item => Math.max(item.done_km || 0, item.planned_km || 0)));
  const bars = el('div', null, 'n-strip'), letters = el('div', null, 'n-letters');
  days.forEach(item => {
    const bar = el('div', null, 'n-bar');
    let label = null;
    if (item.done_km) { bar.style.height = (14 + 86 * item.done_km / scale) + 'px'; label = fmt(item.done_km); }
    else if (item.planned_km || item.planned_label) {
      bar.classList.add('plan'); if (item.long) bar.classList.add('long');
      bar.style.height = (item.planned_km ? 14 + 86 * item.planned_km / scale : 50) + 'px';
      label = item.planned_km ? fmt(item.planned_km) : item.planned_label;
    } else bar.classList.add('flat');
    if (label) bar.append(el('small', label));
    bars.append(add(el('div', null, 'n-sday'), bar));
    const letter = el('span', item.letter, item.today ? 'today' : null);
    letter.append(el('em', item.abbr || (item.rest ? 'repos' : '—')));
    letters.append(letter);
  });
  const node = add(el('div', null, 'n-chart'), bars, letters);
  node.setAttribute('aria-hidden', 'true');
  return node;
}
export function renderWeek(data, root) {
  const monday = day(data.monday);
  const todayIso = app.today ? app.today.date : '';
  const totals = data.totals, open = data.week >= data.current, upcoming = data.week > data.current;
  // An upcoming week has nothing done: its figure is the target, not a zero.
  if (upcoming) content.append(add(el('div', null, 'n-ksum'), el('b', totals.km_target ? '~' + fmt(totals.km_target) : '—'), el('span', 'km prévus')));
  else content.append(add(el('div', null, 'n-ksum'), el('b', fmt(totals.km_done)), el('span', totals.km_target ? 'sur ~' + fmt(totals.km_target) + ' km' : 'km')));
  if (totals.km_target && !upcoming) {
    const meter = el('div', null, 'n-kmeter'), fill = el('i');
    fill.style.width = Math.min(100, 100 * totals.km_done / totals.km_target) + '%';
    content.append(add(meter, fill));
  }
  const sub = el('p', null, 'n-ksub');
  if (upcoming) sub.append(el('b', totals.runs_planned + ' sortie' + (totals.runs_planned > 1 ? 's' : '')), totals.strength_planned ? ' · ' + totals.strength_planned + ' renfo' + (totals.strength_planned > 1 ? 's' : '') : '');
  else sub.append(el('b', totals.runs_done + ' sortie' + (totals.runs_done > 1 ? 's' : '') + (totals.runs_planned ? ' sur ' + totals.runs_planned : '')));
  const rest = [];
  if (open && !upcoming && totals.km_target && totals.km_target > totals.km_done) rest.push('reste ~' + fmt(totals.km_target - totals.km_done, 0) + ' km');
  else if (totals.dplus) rest.push(totals.dplus + ' m D+');
  const peaks = totals.pain_peaks || [];
  peaks.forEach(item => {
    if (item.peak != null) rest.push('douleur au pic ' + (peaks.length > 1 ? item.label.toLowerCase() + ' ' : '') + fmt(item.peak));
  });
  if (rest.length) sub.append(' · ' + rest.join(' · '));
  content.append(sub);
  if (data.strip) content.append(weekChart(data.strip));
  if (data.plan.message) content.append(el('p', data.plan.message, 'n-banner'));
  if (!data.sessions.length && data.plan.status === 'invalid') content.append(el('p', 'Aucun programme enregistré pour cette semaine. Le registre reste ci-dessous.', 'n-meta'));
  const days = el('div', null, 'n-kdays');
  for (let offset = 0; offset < 7; offset++) {
    const date = new Date(monday); date.setDate(monday.getDate() + offset);
    const iso = isoDate(date);
    days.append(dayRow(iso, data.sessions.filter(item => item.date === iso), data.extra.filter(item => item.date === iso), todayIso));
  }
  content.append(days);
  // The next week as soon as its program exists: on Sunday, that is the one being
  // organised.
  if (root && data.next) {
    const node = section('Semaine prochaine'), next = data.next;
    const figure = add(el('span', null, 'n-wfig'), next.km_target ? el('small', '~' + fmt(next.km_target) + ' km') : null);
    node.append(add(button('', () => push({k: 'week', label: next.week}), 'n-wrow'),
      add(el('span'), 'Semaine ' + weekNumber(next.week), el('small', weekSpan(next.week) + ' · ' + next.runs_planned + ' sorties')), figure, chevron()));
    content.append(node);
  }
  // The review takes its place among the cards: a grey line until it is written, the
  // same line made touchable once it arrives.
  if (data.review || open || data.cards.length) {
    const node = section('Cartes de la semaine');
    if (data.review) {
      const title = 'Bilan W' + data.week.slice(-2);
      node.append(add(button('', () => push({k: 'entry', id: data.review.id, title}), 'n-list'), el('span', title), add(el('small', dm(data.review.occurred_on)), chevron())));
    } else if (open) node.append(add(el('div', null, 'n-list n-pending'), el('span', 'Bilan de la semaine'), el('small', 'dimanche')));
    data.cards.forEach(entry => node.append(add(button('', () => push({k: 'entry', id: entry.id, title: entry.title}), 'n-list'), el('span', entry.title), add(el('small', dm(entry.occurred_on)), chevron()))));
    content.append(node);
  }
  // The tab's root is always the current week; the others are pushed on top.
  const past = (root && app.views.weeks && app.views.weeks.weeks) || [];
  if (past.length) {
    const node = section('Semaines passées');
    past.slice(0, 4).forEach(item => node.append(weekRow(item)));
    if (past.length > 4) node.append(add(button('', () => push({k: 'weeks'}), 'n-list'), el('span', 'Toutes les semaines'), add(el('small', String(past.length)), chevron())));
    content.append(node);
  }
}
export function renderWeeks(data) {
  const list = el('div', null, 'n-wlist');
  const weeks = data.weeks || [];
  weeks.forEach(item => list.append(weekRow(item)));
  content.append(weeks.length ? list : el('p', 'Aucune semaine close au registre.', 'n-meta'));
}
