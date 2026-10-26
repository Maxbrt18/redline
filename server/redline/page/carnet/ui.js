// Ce que tous les écrans du carnet partagent : la zone de contenu, le message
// passager, les titres de section.
import {$, add, el} from '../shared/dom.js';

export const content = $('content');
// Un message passager flotte au-dessus des onglets : il ne pousse jamais la page.
// `notice` reste dans le flux, pour l'ouverture et l'écran verrouillé.
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
// L'installation n'est pas une option : c'est une étape, proposée tant qu'elle n'est
// pas faite, là où l'on ouvre le carnet chaque jour, puis plus jamais.
export const installed = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

// Un élément SVG, ses attributs, son texte éventuel, accroché à son parent.
export const SVG = 'http://www.w3.org/2000/svg';
export function shape(tag, attrs, parent, text) {
  const node = document.createElementNS(SVG, tag);
  Object.entries(attrs).forEach(([name, value]) => node.setAttribute(name, String(value)));
  if (text != null) node.textContent = text;
  parent.append(node); return node;
}
// Le seul chevron du carnet (2026-10-10) : un trait dessiné, jamais le caractère « › ».
export function chevron(cls) {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 8 13'); svg.setAttribute('class', 'n-chevron' + (cls ? ' ' + cls : '')); svg.setAttribute('aria-hidden', 'true');
  shape('path', {d: 'M1.5 1.5 6.5 6.5 1.5 11.5'}, svg);
  return svg;
}
// Les pastilles teintées (maquette « Carnet en couleur » validée par Sam le
// 2026-10-22) : un disque léger et son icône nomment la famille d'une séance, d'un
// repère ou d'une capacité, comme celles de la météo. Elles ne jugent rien.
const PAS_ICONS = {
  run: '<path d="M3.5 17.5v-6.3c0-.6.5-1 1.1-.9l2.9.7 2.4-3.4 3.6 2.1c.8 1.9 2.6 3.1 4.9 3.3 1.1.1 2.1 1 2.1 2.2v2.3z"/><path d="M9.3 11.2l1.6 1M11.2 9.7l1.5 1"/>',
  long: '<circle cx="6" cy="18.5" r="1.6"/><circle cx="18" cy="5.5" r="1.6"/><path d="M7.6 18.5h8.9a3 3 0 0 0 0-6h-9a3 3 0 0 1 0-6h8.9"/>',
  str: '<path d="M7 7v10M17 7v10M4.5 9.5v5M19.5 9.5v5M7 12h10"/>',
  rest: '<path d="M8.5 9h5.5l-5.5 6.5h5.5"/><path d="M15.5 4.5h3.5l-3.5 4h3.5"/>',
  other: '<path d="M3.5 12h4l2-5 4 10 2-5h5"/>',
  week: '<path d="M4 20v-5M8 20v-8M12 20v-3M16 20v-10M20 20V6"/>',
  tib: '<path d="M10 3.5v8c0 1.3-.4 2.5-1.1 3.6l-1.6 2.6c-.6 1 .1 2.3 1.3 2.3h6.9"/><path d="M14.5 3.5v9.5l1.5 7"/>',
  race: '<path d="M6 21V4"/><path d="M6 4.5h11.5l-2.6 4 2.6 4H6"/>',
  descent: '<path d="M3.5 6.5l7 7 3-3 7 7"/><path d="M15.5 17.5h5v-5"/>',
  food: '<path d="M7 3.5v17M4.5 3.5v5a2.5 2.5 0 0 0 5 0v-5"/><path d="M17 20.5v-17c-2.2 1.4-3.5 4-3.5 7v3.5H17"/>',
  night: '<path d="M19 14.5A7.5 7.5 0 0 1 9.5 5a7.5 7.5 0 1 0 9.5 9.5z"/>',
  mind: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".6"/>',
};
// La teinte suit la famille ; l'icône peut préciser (la sortie longue reste une course).
const PAS_HUES = {long: 'run', week: 'run', descent: 'run', food: 'race', night: 'race', mind: 'run'};
export function pastille(icon, small) {
  const node = el('span', null, 'n-pas ' + (PAS_HUES[icon] || icon) + (small ? ' sm' : ''));
  node.setAttribute('aria-hidden', 'true');
  node.innerHTML = '<svg viewBox="0 0 24 24">' + (PAS_ICONS[icon] || PAS_ICONS.other) + '</svg>';
  return node;
}
// Le programme dit run, strength, test, rest, bike ou hike, et SL dans `workout` ; le
// registre dit EF, SL, seuil, rando, vélo... Le vélo et la rando restent à la pierre.
const RUN_KINDS = new Set(['run', 'test', 'EF', 'seuil', 'côtes', 'lignes', 'cadence', 'course']);
export const kindIcon = (kind, workout) => kind === 'SL' || workout === 'SL' ? 'long' : kind === 'strength' ? 'str'
  : kind === 'rest' ? 'rest' : RUN_KINDS.has(kind) ? 'run' : 'other';
