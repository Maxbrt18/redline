import {add, button, el} from '../../shared/dom.js';
import {SVG, chevron, content, section, shape} from '../ui.js';
import {dm, fmt, hm, paceOf} from '../format.js';
import {push} from '../nav.js';
import {app} from '../state.js';
import {skyIcon, skyOf} from './weather.js';

// ------------------------------------------------------------ race plan
// The service times the passages at one reference pace. Running time is proportional to
// the pace and the stops are not, so any pace retimes them here, with no request: the
// − / + of the plan screen explores and records nothing. Without a pace chosen with the
// coach, no passage is shown: the notebook never guesses a level.
const PACE_STEP = 5;
const pace = plan => app.racePace || plan.pace_s_per_km;
function retime(plan, seconds) {
  const scale = seconds / plan.reference_pace_s, stop = plan.stop_minutes / 60;
  const [median, p90] = plan.model_error;
  return plan.passages.map((item, index) => {
    // The service's hours include one stop per aid station already passed.
    const running = (item.hours - index * stop) * scale, hours = running + index * stop;
    const leg = item.running_hours * scale;
    return {km: item.km, name: item.name || 'Arrivée', finish: !item.name, hours,
      low: hours * (1 - median), high: hours * (1 + median), low90: hours * (1 - p90), high90: hours * (1 + p90),
      carbs: plan.carbs_g_h ? leg * plan.carbs_g_h : null};
  });
}
const raceDuration = hours => { const minutes = Math.round(hours * 60); return Math.floor(minutes / 60) + ' h ' + String(minutes % 60).padStart(2, '0'); };
// The local time of a passage, to the nearest minute like the durations: the start is a
// wall-clock time at the race's place.
function passageAt(plan, hours) {
  if (!plan.start) return null;
  return new Date(Math.round((new Date(plan.start).getTime() + hours * 3600000) / 60000) * 60000);
}
// A time of day the French way, « 0 h 13 »: « 00:13 » reads as minutes and seconds. A
// duration says « de course » next to it, so the two are never confused.
const wallClock = date => date.getHours() + '\u00a0h\u00a0' + String(date.getMinutes()).padStart(2, '0');
const hourKey = date => {
  const rounded = new Date(date.getTime() + 30 * 60000);
  return rounded.getFullYear() + '-' + String(rounded.getMonth() + 1).padStart(2, '0') + '-' + String(rounded.getDate()).padStart(2, '0')
    + 'T' + String(rounded.getHours()).padStart(2, '0') + ':00';
};
// The profile against an altitude scale, the aid stations named; full size, the km and
// the climb of each stretch are written under it. Close stations take turns on two rows.
function profileChart(plan, full) {
  const width = Math.min(content.clientWidth || 353, 580), height = full ? 210 : 116;
  const length = plan.length_km, heights = plan.profile.map(point => point[1]);
  const floor = Math.floor(Math.min(...heights) / 100) * 100, ceiling = Math.ceil(Math.max(...heights) / 100) * 100 + 50;
  const top = full ? 48 : 36, base = height - (full ? 42 : 20);
  const x = km => km / length * width, y = metres => base - (base - top) * (metres - floor) / (ceiling - floor);
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height); svg.setAttribute('class', 'n-rp-chart');
  svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', 'Profil du parcours et ravitaillements');
  // The scale's lines go under the profile, their altitudes over it: never hidden.
  const step = ceiling - floor > 600 ? 300 : 100, scale = [];
  for (let metres = Math.ceil(floor / step) * step; full && metres < ceiling; metres += step) if (metres !== floor) scale.push(metres);
  scale.forEach(metres => shape('line', {x1: 0, x2: width, y1: y(metres), y2: y(metres), class: 'grid'}, svg));
  const line = plan.profile.map(([km, metres], index) => (index ? 'L' : 'M') + x(km).toFixed(1) + ' ' + y(metres).toFixed(1)).join(' ');
  shape('path', {d: line + ' L' + width + ' ' + base + ' L0 ' + base + ' Z', class: 'area'}, svg);
  shape('path', {d: line, class: 'line'}, svg);
  scale.forEach(metres => shape('text', {x: width, y: y(metres) - 4, 'text-anchor': 'end', class: 'yl'}, svg, metres + ' m'));
  let last = -1e9, row = 0;
  plan.passages.filter(item => item.name).forEach(item => {
    row = x(item.km) - last < 70 ? 1 - row : 0; last = x(item.km);
    const label = top - 16 - row * 14;
    shape('line', {x1: x(item.km), x2: x(item.km), y1: label + 4, y2: base, class: 'aid'}, svg);
    shape('text', {x: x(item.km), y: label, 'text-anchor': 'middle', class: 'al'}, svg, item.name);
  });
  shape('line', {x1: 0, x2: width, y1: base, y2: base, class: 'grid'}, svg);
  if (full) {
    const marks = [0, ...plan.stretches.map(item => item.to_km)];
    marks.forEach((km, index) => shape('text', {x: x(km), y: base + 15, class: 'ak',
      'text-anchor': index === 0 ? 'start' : index === marks.length - 1 ? 'end' : 'middle'}, svg, 'km ' + fmt(km, 0)));
    plan.stretches.forEach((item, index) => shape('text', {x: (x(marks[index]) + x(item.to_km)) / 2, y: base + 32,
      'text-anchor': 'middle', class: 'dp'}, svg, '+' + item.dplus_m + ' −' + item.dminus_m));
    const [metres, km] = plan.highest;
    shape('circle', {cx: x(km), cy: y(metres), r: 3, class: 'pt'}, svg);
    shape('text', {x: x(km) + 6, y: y(metres) + 4, class: 'hi'}, svg, metres + ' m');
  }
  return svg;
}
// The Race tab: the profile, the finish at the pace chosen with the coach (never the one
// being explored), and the way to the plan.
export function raceSummary(plan) {
  const seconds = plan.pace_s_per_km;
  const node = section('Plan de course', seconds ? el('span', 'à ' + paceOf(seconds) + '/km à plat éq.', 'n-meta') : null);
  node.append(profileChart(plan, false));
  if (seconds) {
    const finish = retime(plan, seconds).pop(), when = passageAt(plan, finish.hours);
    node.append(add(el('div', null, 'n-rp-arr'),
      add(el('div'), el('div', 'Temps de course', 'lab'), el('div', raceDuration(finish.hours), 'big'),
        el('div', raceDuration(finish.low) + ' – ' + raceDuration(finish.high)
          + (when ? ' · arrivée vers ' + wallClock(when) : ''), 'n-rp-small'))));
  } else node.append(el('p', 'Allure à choisir avec le coach : les passages s’afficheront ensuite.', 'n-rp-small'));
  node.append(add(button('', () => push({k: 'race-plan'}), 'n-list'),
    el('span', seconds ? 'Passages, ravitos et glucides' : 'Profil et ravitos'), chevron()));
  return node;
}
function passageWeather(hour) {
  const look = skyOf(hour);
  return add(el('div', null, 'n-rp-wx'), skyIcon(look.icon),
    el('span', hour.temp + '°, ressenti ' + hour.feels + '° · ' + look.word + ' · vent ' + hour.wind + ' km/h'));
}
export function renderRacePlan(data) {
  const plan = data.plan, seconds = pace(plan);
  if (seconds) {
    const slower = button('', () => { app.racePace = seconds + PACE_STEP; redraw(); }, 'n-glass n-round');
    const faster = button('', () => { app.racePace = Math.max(150, seconds - PACE_STEP); redraw(); }, 'n-glass n-round');
    slower.setAttribute('aria-label', 'Plus lent'); faster.setAttribute('aria-label', 'Plus vite');
    slower.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 12h12"/></svg>';
    faster.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 12h12M12 6v12"/></svg>';
    content.append(add(el('div', null, 'n-rp-pace'), slower,
      add(el('div', null, 'v'), el('b', paceOf(seconds)), el('span', '/km à plat équivalent')), faster));
    const chosen = plan.pace_s_per_km;
    content.append(el('p', chosen && seconds !== chosen
      ? 'Allure retenue avec le coach : ' + paceOf(chosen) + '/km. Ici, tu explores : rien n’est enregistré.'
      : 'Allure retenue avec le coach. Le − / + explore sans rien enregistrer.', 'n-rp-small'));
  } else content.append(el('p', 'Allure à choisir avec le coach : les passages s’afficheront ensuite.', 'n-rp-small'));

  const profile = section('Profil', el('span', plan.source === 'ign' ? 'altitude IGN' : 'altitude du GPX', 'n-meta'));
  profile.append(profileChart(plan, true));
  content.append(profile);

  if (seconds) {
    const passages = retime(plan, seconds), finish = passages[passages.length - 1];
    // In large, the time on the watch since the start; the time of day comes after
    // « vers », so a passage never reads as two clocks.
    const node = section('Passages', el('span', plan.start ? 'temps de course · départ ' + hm(plan.start.slice(11, 16)) : 'temps de course', 'n-meta'));
    passages.forEach((item, index) => {
      const when = passageAt(plan, item.hours);
      const facts = [when ? 'vers ' + wallClock(when) : null,
        item.carbs != null ? '≈ ' + fmt(Math.round(item.carbs / 5) * 5, 0) + ' g de glucides ' + (item.finish ? 'au dernier tronçon' : 'au tronçon') : null];
      const row = add(el('div', null, 'n-rp-row' + (item.finish ? ' goal' : '')),
        add(el('div', null, 'nm'), item.name + ' ', el('small', 'km ' + fmt(item.km, 0))),
        el('div', raceDuration(item.hours), 'ck'),
        el('div', facts.filter(Boolean).join(' · '), 'sub'),
        el('div', raceDuration(item.low) + ' – ' + raceDuration(item.high), 'rg'));
      const hours = data.weather && when ? data.weather[index] : null;
      const hour = hours ? hours.find(entry => entry.time === hourKey(when)) : null;
      if (hour) row.append(passageWeather(hour));
      node.append(row);
    });
    node.append(el('p', 'Arrivée 9 fois sur 10 entre ' + raceDuration(finish.low90) + ' et ' + raceDuration(finish.high90)
      + ' de course. Nuit, fatigue et terrain technique ne sont pas modélisés.', 'n-rp-small'));
    content.append(node);
    if (plan.start && !data.weather) {
      const opens = new Date(new Date(plan.start).getTime() - 15 * 86400000);
      content.append(add(section('Météo aux passages'), el('p',
        'Prévision disponible vers le ' + dm(opens.toISOString().slice(0, 10)) + ' : ressenti, ciel et vent à chaque ravito, à l’heure du passage.', 'n-rp-small')));
    }
  }

  const band = plan.dplus_band_m ? ' (' + fmt(plan.dplus_band_m[0], 0) + ' – ' + fmt(plan.dplus_band_m[1], 0) + ')' : '';
  const facts = [fmt(plan.length_km) + ' km · D+ ' + fmt(plan.dplus_m, 0) + ' m' + band + ' · D− ' + fmt(plan.dminus_m, 0) + ' m.'];
  if (plan.carbs_g_h) facts.push('Glucides : ' + plan.carbs_g_h + ' g/h de course.');
  if (plan.stop_minutes) facts.push('Arrêts : ' + fmt(plan.stop_minutes) + ' min par ravito.');
  content.append(add(section('Parcours'), el('p', facts.join(' '), 'n-rp-src')));
}
// The plan screen redraws itself when the pace moves; the figures stay on the device.
function redraw() {
  const plan = app.views['race-plan'];
  if (!plan) return;
  const y = scrollY;
  content.replaceChildren();
  renderRacePlan(plan);
  scrollTo(0, y);
}
