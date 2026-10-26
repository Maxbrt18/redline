// ------------------------------------------------------------ météo
import {add, el} from '../../shared/dom.js';
import {flights, skies} from '../state.js';
import {api} from '../api.js';
import {hm} from '../format.js';

// La météo arrive après l'écran : elle ne le fait jamais attendre, et son absence ne
// casse rien. Elle sert deux décisions, et seulement elles (maquette B validée le
// 2026-10-08) : comment s'habiller, et la nuit tombera-t-elle pendant la séance.
const rainWord = chance => chance >= 50 ? 'pluie probable' : chance >= 20 ? 'pluie possible' : 'sec';
const toHours = text => Number(text.slice(0, 2)) + Number(text.slice(3, 5)) / 60;
// Le ciel en code WMO (Open-Meteo) : une icône et un mot, de jour comme de nuit.
function skyOf(hour) {
  const code = hour.code, light = hour.day !== false;
  // Sans code (prévision ancienne), le mot dit seulement la pluie.
  const night = light ? null : 'night';
  if (code == null) return {icon: hour.rain >= 50 ? 'rain' : light ? 'sun' : 'moon', word: rainWord(hour.rain), wet: true, sky: hour.rain >= 50 ? 'rain' : night || 'clear'};
  if (code >= 95) return {icon: 'storm', word: 'orage', wet: true, sky: 'storm'};
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return {icon: 'snow', word: 'neige', wet: true, sky: 'snow'};
  if (code >= 80) return {icon: 'rain', word: 'averses', wet: true, sky: 'rain'};
  if (code >= 61) return {icon: 'rain', word: 'pluie', wet: true, sky: 'rain'};
  if (code >= 51) return {icon: 'rain', word: 'bruine', wet: true, sky: 'rain'};
  if (code === 45 || code === 48) return {icon: 'fog', word: 'brouillard', sky: night || 'cloud'};
  if (code === 3) return {icon: 'cloud', word: 'couvert', sky: night || 'cloud'};
  if (code === 2) return {icon: light ? 'partly' : 'cloud', word: 'éclaircies', sky: night || 'partly'};
  return {icon: light ? 'sun' : 'moon', word: code === 1 ? 'peu nuageux' : light ? 'ensoleillé' : 'ciel clair', sky: night || 'clear'};
}
// Des pictogrammes en couleur (demande de Sam, 2026-10-08 : « plus sympa, moins
// triste ») : le soleil jaune, la pluie bleue, la lune indigo. La couleur reste dans
// la météo ; ailleurs elle garde son sens (tokens.css).
const CLOUD = d => '<path class="s-cloud" d="' + d + '"/>';
const SKY_ICONS = {
  sun: '<circle class="s-sun" cx="12" cy="12" r="4.2"/><path class="s-ray" d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.5 1.5M17.2 17.2l1.5 1.5M5.3 18.7l1.5-1.5M17.2 6.8l1.5-1.5"/>',
  moon: '<path class="s-moon" d="M19 14.5A7.5 7.5 0 0 1 9.5 5a7.5 7.5 0 1 0 9.5 9.5z"/>',
  partly: '<circle class="s-sun" cx="9" cy="9" r="3.4"/><path class="s-ray" d="M9 2.6v1.6M2.6 9h1.6M4.5 4.5l1.1 1.1M13.5 4.5l-1.1 1.1"/>' + CLOUD('M8 19.5h9.5a3.6 3.6 0 0 0 0-7.2 5 5 0 0 0-9.6 1.5A2.9 2.9 0 0 0 8 19.5z'),
  cloud: CLOUD('M7 18h10.5a4 4 0 0 0 0-8 5.5 5.5 0 0 0-10.6 1.6A3.2 3.2 0 0 0 7 18z'),
  rain: CLOUD('M7 14h10.5a3.6 3.6 0 0 0 0-7.2 5 5 0 0 0-9.6 1.5A2.9 2.9 0 0 0 7 14z') + '<path class="s-drop" d="M9 17l-1 2.5M13 17l-1 2.5M17 17l-1 2.5"/>',
  snow: CLOUD('M7 14h10.5a3.6 3.6 0 0 0 0-7.2 5 5 0 0 0-9.6 1.5A2.9 2.9 0 0 0 7 14z') + '<path class="s-snow" d="M9 18h.01M13 19.5h.01M17 18h.01"/>',
  storm: CLOUD('M7 14h10.5a3.6 3.6 0 0 0 0-7.2 5 5 0 0 0-9.6 1.5A2.9 2.9 0 0 0 7 14z') + '<path class="s-bolt" d="M12.5 14.5l-2.2 3.8h3.2l-2.2 3.7"/>',
  fog: '<path class="s-fog" d="M4 9h16M6 13h12M4 17h16"/>',
  // Pas un ciel : la montre de la ligne « avant de partir », à l'encre.
  watch: '<rect x="6.5" y="6.5" width="11" height="11" rx="3"/><path d="M9 6.5l.6-3h4.8l.6 3M9 17.5l.6 3h4.8l.6-3M12 9.5v2.7l1.6 1"/>'
};
function skyIcon(name) {
  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  icon.setAttribute('viewBox', '0 0 24 24'); icon.setAttribute('class', 'n-sky'); icon.setAttribute('aria-hidden', 'true');
  icon.innerHTML = SKY_ICONS[name] || SKY_ICONS.cloud;
  return icon;
}
// Avant de partir : des lignes à pastille sur une même colonne, le ciel, la nuit, la
// montre. La pastille prend la teinte du ciel, le texte reste à l'encre.
function pill(icon, tint) {
  const node = el('span', null, 'n-pill');
  node.dataset.sky = tint;
  node.append(skyIcon(icon));
  return node;
}
const prepRow = (icon, ...text) => add(el('div', null, 'n-prow'), icon, add(el('span'), ...text));
// La nuit, dite par rapport au départ : avant, pendant ou après la séance.
function nightLine(item, data) {
  if (!data.sunset) return null;
  const sunset = toHours(data.sunset), start = item.start_time ? toHours(item.start_time) : null;
  if (start == null) return ['moon', 'Nuit à ' + hm(data.sunset) + '.'];
  if (data.sunrise && start < toHours(data.sunrise)) return ['sun', 'Départ avant le jour : il se lève à ' + hm(data.sunrise) + '.'];
  if (start >= sunset) return ['moon', 'Départ de nuit : elle tombe à ' + hm(data.sunset) + '.'];
  const minutes = (item.targets || {}).duration_cap_min;
  if (minutes) {
    const end = start + minutes / 60;
    if (end > sunset) return ['moon', 'Nuit à ' + hm(data.sunset) + ' : elle tombe pendant ta séance.'];
    const margin = Math.round((sunset - end) * 60);
    return ['moon', 'Nuit à ' + hm(data.sunset) + ' : tu rentres ' + (margin >= 60 ? 'plus d’une heure' : margin + ' min') + ' avant.'];
  }
  const left = Math.round((sunset - start) * 60);
  return ['moon', 'Nuit à ' + hm(data.sunset) + ', ' + Math.floor(left / 60) + ' h ' + String(left % 60).padStart(2, '0') + ' après ton départ.'];
}
// Trois tailles : la séance en cours (le ciel sur deux lignes, puis la nuit), une ligne
// quand le point du matin passe devant, quelques mots dans la ligne de demain.
function fillWeather(slot, data, mode, item) {
  if (!data || !data.hours || !data.hours.length) return;
  const first = data.hours[0], last = data.hours[data.hours.length - 1], sky = skyOf(first);
  if (mode === 'inline') {
    if (slot.parentNode && slot.parentNode.firstChild !== slot) slot.append(' · ');
    slot.append(skyIcon(sky.icon), first.temp + '°, ' + sky.word + (!item.start_time && first.hour < 12 ? ' le matin' : ''));
    return;
  }
  if (mode === 'line') {
    slot.append(prepRow(pill(sky.icon, sky.sky), first.temp + '°, ressenti ' + first.feels + '° · ' + sky.word,
      data.sunset ? el('small', 'Nuit à ' + hm(data.sunset)) : null));
    return;
  }
  const words = sky.wet ? sky.word : sky.word + (first.rain >= 20 ? ', ' + rainWord(first.rain) : ', sec');
  const later = [first.wind >= 25 ? 'Vent fort ' + first.wind + ' km/h' : 'Vent ' + first.wind + ' km/h'];
  if (last !== first) later.push(last.temp + '° à ' + last.hour + ' h');
  slot.append(prepRow(pill(sky.icon, sky.sky), add(el('span', null, 'n-wx'), el('b', first.temp + '°'),
    add(el('span'), 'Ressenti ' + first.feels + '° · ' + words, el('small', later.join(' · '))))));
  const night = nightLine(item, data);
  if (night) slot.append(prepRow(pill(night[0], night[0] === 'moon' ? 'night' : 'clear'), night[1]));
}
// Une prévision déjà reçue se redessine sans réseau : un retour dans l'app ne fait plus
// clignoter le bloc. La première fois, sa place est réservée pour ne rien pousser.
function sky(query) {
  const key = String(query), known = skies.get(key);
  if (known && Date.now() - known.at < 15 * 60000) return Promise.resolve(known.data);
  if (!flights.has(key)) flights.set(key, api('weather?' + query)
    .then(data => { skies.set(key, {data, at: Date.now()}); return data; })
    .finally(() => flights.delete(key)));
  return flights.get(key);
}
export function weatherSlot(item, mode) {
  const inline = mode === 'inline', settled = inline ? '' : 'n-wrows';
  const slot = el(inline ? 'span' : 'div', null, settled);
  const query = new URLSearchParams({date: item.date});
  if (item.start_time) query.set('time', item.start_time);
  if (item.location) query.set('place', item.location);
  const known = skies.get(String(query));
  let drawn = null;
  const draw = data => {
    const signature = JSON.stringify(data || null);
    if (signature === drawn) return;
    drawn = signature; slot.className = settled; slot.replaceChildren();
    if (data) fillWeather(slot, data, mode, item);
  };
  if (known) draw(known.data);
  else if (!inline) slot.className = settled + ' n-wslot' + (mode === 'line' ? ' line' : '');
  sky(query).then(draw).catch(() => { if (!known) draw(null); });
  return slot;
}
export function prep(item, options) {
  const box = add(el('div', null, 'n-prep'), weatherSlot(item, options.compact ? 'line' : 'full'));
  const cap = (item.targets || {}).hr_cap_bpm;
  if (options.today && !options.compact && cap) box.append(prepRow(pill('watch', 'watch'), 'Alerte FC de la montre sur ', el('b', String(cap))));
  return box;
}
