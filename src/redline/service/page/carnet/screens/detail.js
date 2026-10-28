import {add, button, el} from '../../shared/dom.js';
import {chevron, content, kindIcon, message, pastille, shape, SVG} from '../ui.js';
import {api} from '../api.js';
import {dm, fmt, kindName, paceOf, rpeRange, weekNumber} from '../format.js';
import {gauges} from './week.js';
import {cellCount, specOf} from './today.js';
import {ensure, loadToday} from '../data.js';

// ------------------------------------------------------------ Completed session detail
// Sand palette: cards instead of rules, a head that states the session as a big figure and
// a bar, HR zone colours wherever an HR is shown, red for repetitions (intensity).
// Everything is computed by the service (/carnet-api/session/<id>); here we only format.
const SD_CHART_W = 340, SD_LABEL_W = 22, SD_PLOT_W = SD_CHART_W - SD_LABEL_W, SD_HR_H = 112, SD_ALT_H = 52;
const SD_HR_LOW = 100, SD_HR_HIGH = 200;
const SD_ICONS = {
  dplus: '<path d="M3 19l6-10 4 6 3-4 5 8z"/>',
  heart: '<path d="M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.4 4.3 4.3 0 0 1 19.5 10c0 5.4-7.5 10-7.5 10z"/>',
  steps: '<path d="M8 4.5c1.7 0 2.5 1.8 2.5 4s-.8 4-2.5 4-2.5-1.8-2.5-4 .8-4 2.5-4zM6 15h4v2.2a2 2 0 0 1-4 0zM16 7.5c1.7 0 2.5 1.8 2.5 4s-.8 4-2.5 4-2.5-1.8-2.5-4 .8-4 2.5-4zM14 18h4v.7a2 2 0 0 1-4 0z"/>',
  cloud: '<path d="M8 19.5h9.5a3.6 3.6 0 0 0 0-7.2 5 5 0 0 0-9.6 1.5A2.9 2.9 0 0 0 8 19.5z"/>',
  shoe: '<path d="M3.5 17.5v-6.3c0-.6.5-1 1.1-.9l2.9.7 2.4-3.4 3.6 2.1c.8 1.9 2.6 3.1 4.9 3.3 1.1.1 2.1 1 2.1 2.2v2.3z"/>',
  gauge: '<path d="M4 16a8 8 0 0 1 16 0"/><path d="M12 16l4-5"/>',
  trend: '<path d="M4 17h4l3-8 3 5 2-3h4"/>',
  ask: '<circle cx="12" cy="12" r="8.5"/><path d="M9.8 9.6a2.3 2.3 0 1 1 3.2 2.1c-.6.3-1 .8-1 1.5v.4M12 16.6v.1"/>',
  out: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
};
const sdIcon = name => { const node = el('span', null, 'n-dico'); node.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + SD_ICONS[name] + '</svg>'; return node; };
const sdDuration = seconds => {
  const total = Math.round(seconds), h = Math.floor(total / 3600), m = Math.floor(total % 3600 / 60), s = total % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : String(m)) + ':' + String(s).padStart(2, '0');
};
const sdMinutes = seconds => Math.round(seconds / 60) + '′';
const sdZone = (hr, bounds) => 1 + bounds.filter(edge => hr >= edge).length;
const sdZoneChip = n => { const chip = el('span', 'Z' + n, 'n-zc'); chip.style.setProperty('--zc', 'var(--z' + n + ')'); return chip; };
// The bounds come from the instance (Karvonen on its HR values), never from a default.
const sdBounds = data => data.hr_bounds || [];
// The effort target in words: "6-7" for a band, "4" for a single value.
const sdAim = target => target.rpe_min != null && target.rpe_min !== target.rpe_max ? target.rpe_min + '-' + target.rpe_max : String(target.rpe_max);
function sdHead(title, aside) {
  return add(el('div', null, 'n-dh'), el('h2', title), aside ? el('span', aside) : null);
}
function sdFamily(data) {
  if (data.quality) return 'Course · séance de qualité';
  if (data.kind === 'long' || data.workout === 'long') return 'Course · sortie longue';
  if (data.kind === 'easy') return 'Course · endurance fondamentale';
  return kindName[data.kind] || data.kind;
}

// ---- the head: the big figure, then the session as a bar
function sdStrip(data) {
  if (data.phases && data.phases.length) {
    const total = data.phases.reduce((sum, phase) => sum + phase.time_s, 0) || 1;
    const strip = el('div', null, 'n-dstrip');
    strip.setAttribute('role', 'img');
    data.phases.forEach(phase => {
      const part = el('i', null, phase.kind === 'rep' ? 'q' : phase.kind === 'recovery' ? 'r' : 'e');
      part.style.width = (100 * phase.time_s / total) + '%';
      strip.append(part);
    });
    const reps = data.reps || [], first = data.phases.findIndex(phase => phase.kind === 'rep');
    const before = data.phases.slice(0, first).reduce((sum, phase) => sum + phase.time_s, 0);
    const span = data.phases.filter(phase => phase.kind === 'rep' || phase.kind === 'recovery').reduce((sum, phase) => sum + phase.time_s, 0);
    const repLabel = reps.length + ' × ' + sdMinutes(reps.reduce((sum, rep) => sum + rep.time_s, 0) / reps.length);
    strip.setAttribute('aria-label', 'Déroulé : ' + data.phases.map(phase => ({warmup: 'échauffement', rep: 'répétition', recovery: 'récupération', cooldown: 'retour au calme'})[phase.kind] + ' ' + sdMinutes(phase.time_s)).join(', '));
    const labels = el('div', null, 'n-dslab');
    const warm = data.phases.find(phase => phase.kind === 'warmup'), cool = data.phases.find(phase => phase.kind === 'cooldown');
    if (warm) labels.append(el('span', 'échauffement ' + sdMinutes(warm.time_s), 'l'));
    const mid = el('span', repLabel, 'q'); mid.style.left = (100 * (before + span / 2) / total) + '%'; labels.append(mid);
    if (cool) labels.append(el('span', 'calme ' + sdMinutes(cool.time_s), 'r'));
    return [strip, labels];
  }
  if (data.km && data.km.length && data.km.some(split => split.hr != null)) {
    const bounds = sdBounds(data), strip = el('div', null, 'n-dkstrip');
    strip.style.setProperty('--n', String(data.km.length));
    const zones = data.km.map(split => split.hr == null ? null : sdZone(split.hr, bounds));
    zones.forEach(n => { const block = el('i'); block.style.setProperty('--zc', n ? 'var(--z' + n + ')' : 'var(--ghost)'); strip.append(block); });
    const counts = {}; zones.filter(Boolean).forEach(n => { counts[n] = (counts[n] || 0) + 1; });
    const [main, count] = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
    strip.setAttribute('role', 'img');
    strip.setAttribute('aria-label', data.km.length + ' kilomètres, ' + count + ' en Z' + main);
    const labels = add(el('div', null, 'n-dslab'), el('span', '1 bloc = 1 km, à la couleur de sa zone', 'l'),
      add(el('span', null, 'r'), sdZoneChip(Number(main)), ' ' + count + ' sur ' + data.km.length));
    return [strip, labels];
  }
  return [];
}
function sdHero(data) {
  const fact = data.fact, hero = el('section', null, 'n-dhero');
  hero.append(add(el('p', null, 'n-dkind'), pastille(kindIcon(data.kind, data.workout)), sdFamily(data)));
  if (fact.km != null) hero.append(add(el('p', null, 'n-dbig'), el('b', fmt(fact.km, 2)), el('span', 'km')));
  const pair = el('div', null, 'n-dpair');
  if (fact.moving_s != null) pair.append(add(el('div'), el('b', sdDuration(fact.moving_s)), el('span', 'temps')));
  if (fact.km && fact.moving_s) pair.append(add(el('div'), add(el('b', paceOf(fact.moving_s / fact.km)), el('small', '/km')), el('span', 'allure moyenne')));
  if (pair.childNodes.length) hero.append(pair);
  return add(hero, ...sdStrip(data));
}
function sdTile(label, icon, value, suffix, sub, cls) {
  const box = el('div', null, 'n-dtile' + (cls ? ' ' + cls : ''));
  const head = el('span', null, 'n-dtl');
  if (icon) head.append(sdIcon(icon));
  head.append(label);
  return add(box, head, add(el('b', value), suffix ? el('small', suffix) : null), sub);
}
function sdFacts(data) {
  const fact = data.fact, grid = el('div', null, 'n-dtiles');
  grid.append(sdTile('Dénivelé', 'dplus', fact.dplus != null ? String(Math.round(fact.dplus)) : '—', fact.dplus != null ? 'm D+' : null));
  const hrSub = el('small');
  if (fact.hr_avg != null) {
    hrSub.append(sdZoneChip(sdZone(fact.hr_avg, sdBounds(data))));
    if (fact.hr_max != null) hrSub.append(' · max ' + fmt(fact.hr_max, 0));
  } else hrSub.textContent = 'sans brassard';
  grid.append(sdTile('FC moy', 'heart', fact.hr_avg != null ? fmt(fact.hr_avg, 0) : '—', null, hrSub));
  grid.append(sdTile('Cadence', 'steps', fact.cadence != null ? fmt(fact.cadence, 0) : '—', null, el('small', 'pas/min')));
  return grid;
}

// ---- the feel: three tiles, and the one-gesture question while any is missing
function sdSegments(value) {
  const bar = el('span', null, 'n-dseg');
  for (let index = 1; index <= 10; index++) {
    const segment = el('i', null, value >= index ? 'on' : value >= index - 0.5 ? 'on half' : null);
    bar.append(segment);
  }
  return bar;
}
async function sdAnswer(ask, value, card, picked) {
  card.querySelectorAll('button').forEach(node => { node.disabled = true; });
  if (picked) picked.classList.add('n-picked');
  try {
    const result = await api('answer', {ask, value});
    message(result.alert ? 'Noté. Le coach te répond dans Messenger.' : 'Noté.');
    loadToday().catch(() => {});
    ensure();
  } catch (error) {
    message(error.message);
    card.querySelectorAll('button').forEach(node => { node.disabled = false; node.classList.remove('n-picked'); });
  }
}
function sdAsk(data) {
  const ask = data.ask, metric = ask.fields[0], spec = specOf(metric, ask.labels), card = el('div', null, 'n-check');
  const label = el('span', spec.label), target = (data.plan || {}).targets;
  if (metric === 'rpe' && target && target.rpe_max != null) label.append(el('small', 'visé ' + rpeRange(target)));
  const total = cellCount(ask);
  card.append(add(el('p', null, 'n-check-head'), label, el('span', (total - ask.fields.length + 1) + ' sur ' + total, 'n-of')));
  const row = el('div', null, 'n-chips');
  row.style.setProperty('--cols', String(spec.values.length <= 7 ? spec.values.length : Math.ceil(spec.values.length / 2)));
  spec.values.forEach(value => row.append(button(fmt(value), node => sdAnswer(ask.id + ':' + metric, value, card, node), '')));
  card.append(row);
  const next = ask.fields.slice(1).map(field => specOf(field, ask.labels).name);
  if (next.length) card.append(el('p', 'Puis ' + next.join(' et ') + (next.length > 1 ? ', un geste chacun.' : '.'), 'n-note'));
  if (metric.startsWith('pain:')) card.append(el('p', 'Plus de 3 ? Dis-le au coach dans Messenger.', 'n-note'));
  return card;
}
function sdFeelings(data) {
  const ask = data.ask, missing = ask ? ask.fields : [];
  const box = add(el('section', null, 'n-dsec'), sdHead('Ressenti', ask ? 'un geste par valeur' : 'déclaré après la séance'));
  const grid = el('div', null, 'n-dtiles');
  // One pain tile per monitored zone, known or still asked.
  const known = Object.fromEntries((data.fact.pains || []).map(item => ['pain:' + item.zone, item]));
  const watched = Object.keys(ask ? ask.labels : {}).length ? Object.entries(ask.labels)
    : (data.fact.pains || []).map(item => ['pain:' + item.zone, item.label]);
  const feelings = watched.map(([field, label]) => [field, 'Douleur ' + label, 'pain', (known[field] || {}).value]);
  feelings.push(['rpe', 'Effort (RPE)', '', data.fact.rpe], ['enjoyment', 'Plaisir', '', data.fact.enjoyment]);
  feelings.forEach(([metric, label, cls, value]) => {
    if (value == null) {
      const now = missing[0] === metric;
      grid.append(sdTile(label, null, now ? '?' : '—', null, el('small', now ? 'en cours' : missing.includes(metric) ? 'ensuite' : 'non noté'), 'todo'));
    } else grid.append(add(sdTile(label, null, fmt(value), '/10', null, cls), sdSegments(value)));
  });
  box.append(grid);
  if (ask) box.append(sdAsk(data));
  const several = data.wake.zones.length > 1;
  data.wake.zones.forEach(wake => {
    const parts = [], who = several ? ' · ' + wake.label : '';
    if (wake.before != null) parts.push(el('span', 'Réveil du jour' + who + ' ' + fmt(wake.before) + '/10'));
    if (wake.after != null) parts.push(el('span', 'réveil du lendemain ' + fmt(wake.after) + '/10'));
    else if (data.wake.after_due) parts.push(add(el('span'), 'lendemain' + (wake.before == null ? who : '') + ' : ', el('span', 'à noter au réveil', 'n-dpend')));
    if (parts.length) {
      const line = el('p', null, 'n-dfoot');
      parts.forEach((part, index) => line.append(index ? ' · ' : '', part));
      box.append(line);
    }
  });
  return box;
}

// ---- planned and done
function sdPlan(data) {
  if (!data.plan) return null;
  const [rules] = gauges({fact: data.fact, targets: data.plan.targets});
  const steps = el('div', null, 'n-dsteps');
  const phases = data.phases || [];
  const step = (kind, name, sub, value, valueSub) => add(el('div', null, 'n-dstep' + (kind ? ' ' + kind : '')), el('i'),
    add(el('span', name), sub ? el('small', sub) : null), add(el('span', value), valueSub ? el('small', valueSub) : null));
  const warm = phases.find(phase => phase.kind === 'warmup'), cool = phases.find(phase => phase.kind === 'cooldown');
  const reps = data.reps || [];
  if (warm) steps.append(step('', 'Échauffement', null, sdDuration(warm.time_s), warm.hr != null ? 'FC ' + warm.hr : null));
  if (reps.length) {
    const times = reps.map(rep => rep.time_s), beats = reps.map(rep => rep.hr).filter(hr => hr != null);
    steps.append(step('q', reps.length + ' × ' + sdMinutes(times.reduce((a, b) => a + b, 0) / reps.length),
      data.plan.targets.rpe_max != null ? 'effort visé ' + sdAim(data.plan.targets) : null,
      sdDuration(Math.min(...times)) + (Math.max(...times) !== Math.min(...times) ? ' – ' + sdDuration(Math.max(...times)) : ''),
      beats.length ? 'FC ' + Math.min(...beats) + (Math.max(...beats) !== Math.min(...beats) ? ' – ' + Math.max(...beats) : '') : null));
  }
  if (cool) steps.append(step('', 'Retour au calme', null, sdDuration(cool.time_s), cool.hr != null ? 'FC ' + cool.hr : null));
  if (!rules && !steps.childNodes.length) return null;
  const card = add(el('div', null, 'n-dcard'), rules, steps.childNodes.length ? steps : null);
  if (data.plan.note) {
    const more = add(el('details', null, 'n-dnotefold'), el('summary', 'La consigne'), el('p', data.plan.note, 'n-dnote'));
    card.append(more);
  }
  return add(el('section', null, 'n-dsec'), sdHead('Prévu et fait'), card);
}

// ---- charts: HR over the background of its zones, altitude below
const sdX = (seconds, duration) => SD_PLOT_W * seconds / (duration || 1);
const sdY = hr => SD_HR_H * (SD_HR_HIGH - Math.max(SD_HR_LOW, Math.min(SD_HR_HIGH, hr))) / (SD_HR_HIGH - SD_HR_LOW);
// `joined`: an area is not cut at stops, only a line shows them.
function sdPath(times, values, duration, y, joined = false) {
  let path = '', open = false;
  values.forEach((value, index) => {
    if (value == null) { if (!joined) open = false; return; }
    path += (open ? ' L' : ' M') + sdX(times[index], duration).toFixed(1) + ',' + y(value).toFixed(1);
    open = true;
  });
  return path.trim();
}
function sdAxis(duration) {
  const axis = el('div', null, 'n-daxis'), step = duration > 75 * 60 ? 20 * 60 : duration > 30 * 60 ? 15 * 60 : 5 * 60;
  for (let t = 0; t < duration - step / 3; t += step) {
    const tick = el('span', t ? Math.round(t / 60) + '′' : '0');
    tick.style.left = (100 * sdX(t, duration) / SD_CHART_W) + '%';
    axis.append(tick);
  }
  return axis;
}
function sdChart(data) {
  const series = data.series;
  if (!series || !series.hr || !series.hr.some(value => value != null)) return null;
  const duration = series.duration, bounds = sdBounds(data), box = el('div', null, 'n-dchart');
  box.append(add(el('p', null, 'n-dclab'), add(el('span'), el('b', 'Fréquence cardiaque'), ' sur ses zones'),
    series.alt ? null : el('span', 'parcours plat, profil masqué')));
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 -16 ' + SD_CHART_W + ' ' + (SD_HR_H + 16));
  svg.setAttribute('role', 'img');
  const known = series.hr.filter(value => value != null);
  svg.setAttribute('aria-label', 'Fréquence cardiaque de ' + Math.min(...known) + ' à ' + Math.max(...known) + ' bpm, sur le fond des cinq zones');
  const edges = [SD_HR_LOW, ...bounds, SD_HR_HIGH];
  for (let n = 1; n <= 5; n++) {
    const top = sdY(edges[n]), bottom = sdY(edges[n - 1]);
    shape('rect', {x: 0, y: top, width: SD_PLOT_W, height: bottom - top, class: 'zb z' + n}, svg);
    shape('text', {x: SD_CHART_W, y: (top + bottom) / 2 + 3.5, class: 'zl z' + n}, svg, 'Z' + n);
  }
  (series.bands || []).forEach(([start, end], index) => {
    shape('rect', {x: sdX(start, duration), y: -16, width: Math.max(2, sdX(end, duration) - sdX(start, duration)), height: SD_HR_H + 16, class: 'rb'}, svg);
    shape('text', {x: sdX((start + end) / 2, duration), y: -4, class: 'rep'}, svg, String(index + 1));
  });
  shape('path', {d: sdPath(series.t, series.hr, duration, sdY), class: 'hr'}, svg);
  // The peak is written when no repetition already states it in its table.
  if (!(series.bands || []).length) {
    const peak = series.hr.indexOf(Math.max(...known));
    shape('text', {x: sdX(series.t[peak], duration), y: sdY(Math.max(...known)) - 5, class: 'v'}, svg, String(Math.max(...known)));
  }
  (series.stops || []).forEach(([start, end]) => shape('text', {x: sdX((start + end) / 2, duration), y: SD_HR_H - 4, class: 'stop'}, svg, 'arrêt'));
  box.append(svg);
  if (series.alt) {
    const alts = series.alt.filter(value => value != null), low = Math.min(...alts), high = Math.max(...alts), range = Math.max(1, high - low);
    const y = value => 4 + (SD_ALT_H - 8) * (high - value) / range;
    box.append(add(el('p', null, 'n-dclab'), add(el('span'), el('b', 'Altitude'), data.fact.dplus != null ? ' · +' + Math.round(data.fact.dplus) + ' m' : '')));
    const alt = document.createElementNS(SVG, 'svg');
    alt.setAttribute('viewBox', '0 0 ' + SD_CHART_W + ' ' + SD_ALT_H);
    alt.setAttribute('role', 'img');
    alt.setAttribute('aria-label', 'Altitude de ' + Math.round(low) + ' à ' + Math.round(high) + ' m');
    (series.bands || []).forEach(([start, end]) => shape('rect', {x: sdX(start, duration), y: 0, width: Math.max(2, sdX(end, duration) - sdX(start, duration)), height: SD_ALT_H, class: 'rb'}, alt));
    const line = sdPath(series.t, series.alt, duration, y, true);
    const firstX = sdX(series.t[series.alt.findIndex(value => value != null)], duration);
    const lastIndex = series.alt.length - 1 - [...series.alt].reverse().findIndex(value => value != null);
    shape('path', {d: line + ' L' + sdX(series.t[lastIndex], duration).toFixed(1) + ',' + SD_ALT_H + ' L' + firstX.toFixed(1) + ',' + SD_ALT_H + ' Z', class: 'alt'}, alt);
    const top = series.alt.indexOf(high);
    shape('text', {x: sdX(series.t[top], duration), y: -3, class: 'v ta-m'}, alt, Math.round(high) + ' m');
    shape('text', {x: SD_CHART_W, y: SD_ALT_H - 4, class: 'ta-e'}, alt, Math.round(low) + ' m');
    box.append(alt);
  }
  box.append(sdAxis(duration));
  return box;
}

// ---- repetitions (quality) or kilometres (the rest)
function sdReps(data) {
  const bounds = sdBounds(data), table = el('div', null, 'n-dreps');
  table.append(add(el('div', null, 'n-drh'), el('span'), el('span', 'Durée'), el('span', 'D+'), el('span', 'Allure'), el('span', 'FC moy'), el('span', 'Cad.')));
  data.reps.forEach(rep => {
    const hr = el('span', rep.hr != null ? String(rep.hr) : '—', rep.hr != null ? 'fc' : null);
    if (rep.hr != null) hr.style.setProperty('--zc', 'var(--z' + sdZone(rep.hr, bounds) + ')');
    table.append(add(el('div', null, 'n-drr'), add(el('span'), el('span', String(rep.n), 'n-drn')), el('span', sdDuration(rep.time_s)),
      add(el('span', String(rep.dplus)), el('small', 'm')), el('span', rep.pace_s ? paceOf(rep.pace_s) : '—'), hr,
      el('span', rep.cadence != null ? String(rep.cadence) : '—')));
  });
  return table;
}
function sdRepsNote(data) {
  const recoveries = (data.phases || []).filter(phase => phase.kind === 'recovery');
  const drops = data.reps.map(rep => rep.hr_drop).filter(drop => drop != null && drop > 0);
  const note = el('p', null, 'n-dnote');
  if (recoveries.length) note.append('Récupérations : ', el('b', recoveries.map(phase => sdDuration(phase.time_s)).join(' · ')), '. ');
  if (drops.length) note.append('Une minute après chaque répétition, la FC redescend de ', el('b', Math.min(...drops) + (Math.max(...drops) !== Math.min(...drops) ? ' à ' + Math.max(...drops) : '') + ' bpm'), '.');
  return note.childNodes.length ? note : null;
}
function sdReadRow(icon, strong, rest, cls) {
  return add(el('div', null, 'n-dread-row' + (cls ? ' ' + cls : '')), sdIcon(icon), add(el('span'), el('b', strong), rest ? ' · ' + rest : ''));
}
function sdReserve(data) {
  if (data.fact.reserve != null) {
    return sdReadRow('ask', data.fact.reserve > 0 ? 'Une répétition en réserve' : 'Pas de réserve', data.fact.reserve > 0 ? 'il t’en restait une à la fin' : 'tu étais au bout');
  }
  if (!data.reserve_ask) return sdReadRow('ask', 'Réserve non déclarée', null, 'pend');
  const card = add(el('div', null, 'n-dread-row ask'), add(el('span', null, 'n-dask'), sdIcon('ask'),
    add(el('span'), el('b', 'À la fin, il t’en restait une ?'), ' Une répétition entière, au même effort.')));
  const row = el('div', null, 'n-chips n-dchoice');
  row.style.setProperty('--cols', '2');
  [[1, 'Oui, une entière'], [0, 'Non, j’étais au bout']].forEach(([value, text]) => row.append(button(text, node => sdAnswer(data.reserve_ask, value, card, node), '')));
  return add(card, row);
}
function sdQualityRead(data) {
  const read = el('div', null, 'n-dread'), target = (data.plan || {}).targets || {}, rpe = data.fact.rpe;
  if (rpe != null && target.rpe_max != null) {
    const low = target.rpe_min != null ? target.rpe_min : 0;
    const inside = rpe >= low && rpe <= target.rpe_max;
    read.append(sdReadRow('gauge', inside ? 'Effort dans la bande' : rpe > target.rpe_max ? 'Effort au-dessus de la bande' : 'Effort sous la bande', 'RPE ' + fmt(rpe) + ' pour ' + sdAim(target) + ' visé'));
  }
  const reading = data.reading;
  if (reading) {
    const range = paceOf(reading.fastest_before) + ' – ' + paceOf(reading.slowest_before);
    read.append(reading.final_acceleration
      ? sdReadRow('trend', 'Accélération finale', 'la dernière à ' + paceOf(reading.last) + ', plus vite que les précédentes (' + range + ')')
      : sdReadRow('trend', 'Pas d’accélération finale', 'la dernière à ' + paceOf(reading.last) + ', dans l’écart des précédentes (' + range + ')'));
  }
  read.append(sdReserve(data));
  return read;
}
function sdKm(data) {
  const bounds = sdBounds(data), table = el('div', null, 'n-dkm');
  const paces = data.km.map(split => split.pace_s), fast = Math.min(...paces), slow = Math.max(...paces);
  table.append(add(el('div', null, 'n-dkh'), el('span', 'Km'), el('span', 'Allure'), el('span', 'plus long = plus vite'), el('span', 'FC'), el('span', 'Cad.')));
  data.km.forEach((split, index) => {
    const last = index === data.km.length - 1 && split.dist_m < 950;
    const bar = add(el('span', null, 'bar'), el('i'));
    bar.firstChild.style.width = (slow === fast ? 60 : 15 + 85 * (slow - split.pace_s) / (slow - fast)) + '%';
    const hr = el('span', split.hr != null ? String(split.hr) : '—', split.hr != null ? 'fc' : null);
    if (split.hr != null) hr.style.setProperty('--zc', 'var(--z' + sdZone(split.hr, bounds) + ')');
    table.append(add(el('div', null, 'n-dkr'), el('span', last ? fmt(split.dist_m / 1000, 2) : String(split.n)), el('span', paceOf(split.pace_s)), bar, hr,
      el('span', split.cadence != null ? String(split.cadence) : '—', 'cad')));
  });
  return table;
}
function sdKmRead(data) {
  const read = el('div', null, 'n-dread'), target = (data.plan || {}).targets || {}, rpe = data.fact.rpe;
  if (data.drift) {
    const delta = data.drift.second - data.drift.first;
    read.append(sdReadRow('trend', Math.abs(delta) < 3 ? 'Pas de dérive cardiaque' : 'FC ' + (delta > 0 ? 'en hausse' : 'en baisse') + ' de ' + Math.abs(delta) + ' bpm',
      data.drift.first + ' sur la 1ʳᵉ moitié, ' + data.drift.second + ' sur la 2ᵈᵉ'));
  }
  if (rpe != null && target.rpe_max != null) {
    const low = target.rpe_min != null ? target.rpe_min : target.rpe_max;
    read.append(sdReadRow('gauge', rpe > target.rpe_max ? 'Effort au-dessus de la cible' : rpe < low ? 'Effort sous la cible' : 'Effort dans la cible', 'RPE ' + fmt(rpe) + ' pour ' + sdAim(target) + ' visé'));
  }
  return read.childNodes.length ? read : null;
}
function sdWork(data) {
  const chart = sdChart(data);
  if (data.reps && data.reps.length) {
    const card = add(el('div', null, 'n-dcard'), chart, sdReps(data), sdRepsNote(data), sdQualityRead(data));
    return add(el('section', null, 'n-dsec'), sdHead('Les répétitions', data.reps.length + ' × ' + sdMinutes(data.reps.reduce((a, rep) => a + rep.time_s, 0) / data.reps.length)), card);
  }
  if (!chart && !(data.km && data.km.length)) return null;
  const stops = data.stops, note = stops ? add(el('p', null, 'n-dnote'), 'Arrêts : ', el('b', sdDuration(stops.total_s)),
    ' en ' + stops.count + ' fois' + (stops.count > 1 && stops.longest_km ? ', dont ' + sdDuration(stops.longest_s) + ' au ' + stops.longest_km + (stops.longest_km === 1 ? 'ᵉʳ' : 'ᵉ') + ' km' : '') + '.') : null;
  const card = add(el('div', null, 'n-dcard'), chart, data.km && data.km.length ? sdKm(data) : null, note, sdKmRead(data));
  return add(el('section', null, 'n-dsec'), sdHead('Au kilomètre', 'temps en mouvement'), card);
}

// ---- time per zone, comparison, conditions, week
function sdZones(data) {
  if (!data.zones) return null;
  const seconds = data.zones.seconds, total = seconds.reduce((a, b) => a + b, 0) || 1, top = Math.max(...seconds) || 1;
  const bounds = sdBounds(data), card = el('div', null, 'n-dcard n-dzones');
  const span = n => n === 1 ? '< ' + bounds[0] : n === 5 ? '> ' + bounds[3] : bounds[n - 2] + '–' + bounds[n - 1];
  seconds.forEach((value, index) => {
    const n = index + 1, row = el('div', null, 'n-dz'), fill = el('i');
    row.style.setProperty('--zc', 'var(--z' + n + ')');
    fill.style.width = (100 * value / top) + '%';
    const share = Math.round(100 * value / total);
    row.append(add(el('span'), sdZoneChip(n), el('small', span(n))), add(el('span', null, 'n-dzbar'), fill),
      add(el('span'), el('b', sdMinutes(value)), el('small', value && !share ? '< 1 %' : share + ' %')));
    card.append(row);
  });
  return add(el('section', null, 'n-dsec'), sdHead('Temps par zone', 'FC max estimée ' + data.zones.max), card);
}
function sdPrevious(data) {
  const before = data.previous;
  if (!before) return null;
  const rows = [], fact = data.fact, num = (value, digits = 1) => value == null ? '—' : fmt(value, digits);
  const reps = data.reps || [], repDplus = reps.length ? Math.round(reps.reduce((a, rep) => a + rep.dplus, 0) / reps.length) : null;
  const repHr = reps.filter(rep => rep.hr != null);
  const repBeat = repHr.length ? Math.round(repHr.reduce((a, rep) => a + rep.hr, 0) / repHr.length) : null;
  if (data.quality && repDplus != null && before.rep_dplus != null) rows.push(['Montée par rép.', '+' + repDplus + ' m', '+' + before.rep_dplus + ' m']);
  if (data.quality && repBeat != null && before.rep_hr != null) rows.push(['FC moy des rép.', String(repBeat), String(before.rep_hr)]);
  if (!data.quality) rows.push(['Distance', num(fact.km), num(before.km)], ['FC moyenne', num(fact.hr_avg, 0), num(before.hr_avg, 0)]);
  const then = Object.fromEntries((before.pains || []).map(item => [item.zone, item.value]));
  rows.push(['Effort (RPE)', num(fact.rpe), num(before.rpe)]);
  (fact.pains || []).forEach(item => rows.push(['Douleur ' + item.label, num(item.value), num(then[item.zone])]));
  rows.push(['Plaisir', num(fact.enjoyment), num(before.enjoyment)]);
  const card = add(el('div', null, 'n-dcard'), add(el('div', null, 'n-dcrow head'), el('span'), el('span', dm(data.date)), el('span', dm(before.date))));
  rows.forEach(([label, now, then]) => card.append(add(el('div', null, 'n-dcrow'), el('span', label), el('span', now), el('span', then))));
  if (data.quality && repDplus != null && before.rep_dplus != null && Math.abs(repDplus - before.rep_dplus) > 0.2 * Math.max(repDplus, before.rep_dplus)) {
    card.append(el('p', 'Côte ' + (repDplus > before.rep_dplus ? 'plus raide' : 'moins raide') + ' que la dernière fois : les allures ne se comparent pas d’une côte à l’autre.', 'n-dnote'));
  }
  const title = data.quality ? 'Le seuil précédent' : data.kind === 'long' ? 'La sortie longue précédente' : 'La séance précédente du même type';
  return add(el('section', null, 'n-dsec'), sdHead(title, dm(before.date)), card);
}
function sdConditions(data) {
  const grid = el('div', null, 'n-dtiles two'), meteo = data.weather;
  if (meteo && meteo.temp != null) {
    const sub = [meteo.conditions, meteo.ressenti != null ? 'ressenti ' + fmt(meteo.ressenti, 0) + ' °C' : null, meteo.wbgt != null ? 'WBGT ' + fmt(meteo.wbgt, 0) : null].filter(Boolean).join(' · ');
    grid.append(sdTile('Météo au départ', 'cloud', fmt(meteo.temp, 0), '°C', el('small', sub)));
  } else if (meteo && meteo.wbgt != null) grid.append(sdTile('Météo au départ', 'cloud', fmt(meteo.wbgt, 0), '°C WBGT', el('small', 'température au thermomètre-globe')));
  else grid.append(sdTile('Météo au départ', 'cloud', '—', null, el('small', 'non relevée'), 'todo'));
  const gear = data.gear;
  if (gear) {
    const wear = gear.status === 'retired' ? 'retirée' : gear.retire_at_km ? 'contrôle à ' + gear.retire_at_km : null;
    grid.append(sdTile('Chaussures', 'shoe', String(gear.km), 'km', el('small', [gear.name, wear].filter(Boolean).join(' · '))));
  } else grid.append(sdTile('Chaussures', 'shoe', '—', null, el('small', 'non renseignées'), 'todo'));
  return add(el('section', null, 'n-dsec'), sdHead('Conditions'), grid);
}
function sdWeek(data) {
  const week = data.week, box = add(el('section', null, 'n-dsec'), sdHead('Dans la semaine', 'semaine ' + weekNumber(week.week) + (week.current ? '' : ', close')));
  const card = add(el('div', null, 'n-dcard n-dweek'), add(el('b', fmt(week.km_done)), el('small', week.km_target ? 'sur ~' + fmt(week.km_target, 0) + ' km' : 'km')));
  if (week.km_target) {
    const meter = add(el('div', null, 'n-dmeter'), el('i'));
    meter.firstChild.style.width = Math.min(100, 100 * week.km_done / week.km_target) + '%';
    card.append(meter);
  }
  card.append(el('p', week.runs_done + ' sortie' + (week.runs_done > 1 ? 's' : '') + (week.runs_planned ? ' sur ' + week.runs_planned : ''), 'n-dnote'));
  box.append(card);
  if (week.next) {
    const families = week.next.families.map(([kind, workout]) => pastille(kindIcon(kind, workout), true));
    box.append(add(el('div', null, 'n-dlink'), add(el('span', null, 'n-pasg'), ...families),
      el('span', 'Ensuite · ' + week.next.titles.join(' + ')), el('small', dm(week.next.date))));
  }
  return box;
}
function sdStrava(data) {
  if (!data.strava_id) return null;
  const link = add(el('a', null, 'n-dlink'), sdIcon('out'), el('span', 'Ouvrir dans Strava'), chevron());
  link.href = 'https://www.strava.com/activities/' + data.strava_id;
  link.target = '_blank'; link.rel = 'noopener';
  return link;
}
export function renderOutcome(data) {
  content.append(sdHero(data), sdFacts(data), sdFeelings(data));
  [sdPlan(data), sdWork(data), sdZones(data), sdPrevious(data), sdConditions(data), sdWeek(data), sdStrava(data)]
    .forEach(node => { if (node) content.append(node); });
}
