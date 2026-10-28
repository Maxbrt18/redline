// Dates, nombres et libellés, en français. Rien ici ne touche au DOM.
import {app} from './state.js';

export const capital = text => text.charAt(0).toUpperCase() + text.slice(1);
export const day = value => new Date(value + 'T12:00:00');
export const fmt = (value, digits = 1) => Number(value).toLocaleString('fr-FR', {maximumFractionDigits: digits});
export const longDay = value => capital(day(value).toLocaleDateString('fr-FR', {weekday: 'long', day: 'numeric', month: 'long'}));
const weekday = value => day(value).toLocaleDateString('fr-FR', {weekday: 'long'});
export const shortDay = value => day(value).toLocaleDateString('fr-FR', {weekday: 'short', day: '2-digit', month: '2-digit'}).replace('.', '');
export const dm = value => day(value).toLocaleDateString('fr-FR', {day: '2-digit', month: '2-digit'});
export const humanDate = value => day(value).toLocaleDateString('fr-FR', {day: 'numeric', month: 'long', year: 'numeric'});
export const weekNumber = label => Number(label.slice(-2));
export const isoDate = date => date.getFullYear() + '-' + String(date.getMonth() + 1).padStart(2, '0') + '-' + String(date.getDate()).padStart(2, '0');
// Les types de séance et leurs libellés viennent du service (`window.REDLINE_KINDS`).
export const kinds = window.REDLINE_KINDS || {};
export const kindName = Object.fromEntries(Object.entries(kinds).map(([slug, kind]) => [slug, kind.name]));
export const shortTitle = title => title.split(',')[0].trim();
// Une heure ne se coupe jamais en fin de ligne : « 19 h 05 » tient d'un bloc.
export const hm = text => Number(text.slice(0, 2)) + '\u00a0h' + (text.slice(3, 5) === '00' ? '' : '\u00a0' + text.slice(3, 5));
export const startedAt = fact => fact && fact.started_at ? hm(fact.started_at.slice(11, 16)) : '';
export const paceOf = seconds => Math.floor(seconds / 60) + ':' + String(Math.round(seconds % 60)).padStart(2, '0');
export const rpeRange = target => target.rpe_min != null && target.rpe_min !== target.rpe_max ? target.rpe_min + '-' + target.rpe_max : '≤' + target.rpe_max;
export const minutesText = minutes => minutes < 60 ? minutes + ' min' : Math.floor(minutes / 60) + ' h ' + String(minutes % 60).padStart(2, '0');
export const dayNumber = value => weekday(value) + ' ' + day(value).getDate();
export function plannedValue(item) {
  const target = item.targets || {};
  if (target.distance_target_km != null) return fmt(target.distance_target_km) + ' km';
  if (target.distance_cap_km != null) return '≤' + fmt(target.distance_cap_km) + ' km';
  if (target.duration_cap_min != null) return '≤' + target.duration_cap_min + ' min';
  return '';
}
// Une semaine ISO : son lundi, son étendue en mots, son état par rapport à aujourd'hui.
function mondayOf(label) {
  const year = Number(label.slice(0, 4)), jan4 = new Date(year, 0, 4, 12);
  const monday = new Date(jan4); monday.setDate(jan4.getDate() - ((jan4.getDay() + 6) % 7) + (weekNumber(label) - 1) * 7);
  return monday;
}
export function weekSpan(label) {
  const monday = mondayOf(label), sunday = new Date(monday); sunday.setDate(monday.getDate() + 6);
  return monday.getMonth() === sunday.getMonth()
    ? monday.getDate() + ' – ' + sunday.getDate() + ' ' + sunday.toLocaleDateString('fr-FR', {month: 'long'})
    : monday.toLocaleDateString('fr-FR', {day: 'numeric', month: 'short'}).replace('.', '') + ' – ' + sunday.toLocaleDateString('fr-FR', {day: 'numeric', month: 'long'});
}
export const weekState = label => !app.today ? '' : label === app.today.week ? 'en cours' : label < app.today.week ? 'terminée' : 'à venir';
export const raceFacts = race => {
  const approx = race.elevation_approximate ? '~' : '';
  return [day(race.date).toLocaleDateString('fr-FR', {day: 'numeric', month: 'long'}), race.distance_km ? fmt(race.distance_km) + ' km' : null,
    race.elevation_gain_m ? approx + race.elevation_gain_m + ' D+' + (race.elevation_loss_m ? ' / ' + approx + race.elevation_loss_m + ' D−' : '') : null].filter(Boolean).join(' · ');
};
// Le titre d'une carte : le mois pour un monthly, la semaine pour un bilan.
export function entryTitle(item) {
  if (item.kind === 'monthly' && item.period) return capital(new Date(item.period + '-01T12:00:00').toLocaleDateString('fr-FR', {month: 'long', year: 'numeric'}));
  if (item.kind === 'weekly' && item.period) return 'Bilan W' + item.period.slice(-2);
  return item.title;
}
export const kindWord = {monthly: 'Monthly', weekly: 'Weekly', strength: 'Renfo'};
