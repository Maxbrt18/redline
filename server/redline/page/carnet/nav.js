import {$, add, button, el} from '../shared/dom.js';
import {retry} from '../strength/send.js';
import {app, calm} from './state.js';
import {content, message} from './ui.js';
import {entryTitle, humanDate, kindName, kindWord, longDay, raceFacts, startedAt, weekNumber, weekSpan, weekState} from './format.js';
import {renderToday} from './screens/today.js';
import {renderWeek, renderWeeks} from './screens/week.js';
import {renderCourse} from './screens/course.js';
import {renderInjury} from './screens/injury.js';
import {renderOutcome} from './screens/detail.js';
import {renderArchive, renderEntry} from './screens/archives.js';
import {ensure, refresh} from './data.js';

// ------------------------------------------------------------ navigation
// Trois racines toujours visibles ; tout le reste se pousse par-dessus et se referme
// d'un cran vers l'endroit d'où l'on vient (carte validée par l'athlète le 2026-10-08).
// Chaque écran déjà vu s'affiche depuis la mémoire de la page puis se recalcule
// derrière ; il n'est redessiné que si un chiffre a changé, et garde sa position.
export const topRoute = () => app.stacks[app.active][app.stacks[app.active].length - 1] || null;
export function screen() {
  const route = topRoute();
  if (!route) {
    if (app.active === 'week') return app.today ? {key: 'week:' + app.today.week, path: 'week/' + app.today.week, root: true} : {key: 'week:', path: null, root: true};
    return {key: app.active, path: app.active === 'course' ? 'race' : 'today', root: true};
  }
  if (route.k === 'week') return {key: 'week:' + route.label, path: 'week/' + route.label, label: route.label};
  if (route.k === 'weeks') return {key: 'weeks', path: 'weeks'};
  if (route.k === 'archive') return {key: 'archive', path: 'entries'};
  // Une zone surveillée par écran : la clé et la requête la nomment.
  if (route.k === 'injury') return {key: 'injury:' + (route.zone || ''), path: 'injury' + (route.zone ? '?zone=' + encodeURIComponent(route.zone) : '')};
  if (route.k === 'session') return {key: 'session:' + route.id, path: 'session/' + encodeURIComponent(route.id), title: route.title};
  return {key: 'entry:' + route.id, path: 'entries/' + encodeURIComponent(route.id), title: route.title};
}
// Le titre dit toujours où l'on est ; le sous-titre, de quand.
function heading(view, data) {
  if (view.key === 'today') return ['Aujourd’hui', data ? longDay(data.date) + ' · semaine ' + weekNumber(data.week) : ''];
  if (view.key === 'course') return data && data.race ? [data.race.name, raceFacts(data.race)] : ['Course', ''];
  if (view.key.startsWith('week:')) {
    const label = view.key.slice(5);
    return label ? ['Semaine ' + weekNumber(label), weekSpan(label) + (weekState(label) ? ' · ' + weekState(label) : '')] : ['Semaine', ''];
  }
  if (view.key === 'weeks') return ['Toutes les semaines', data && data.weeks ? data.weeks.length + ' semaines au registre' : ''];
  if (view.key === 'archive') return ['Archives', 'bilans, monthly et cartes'];
  if (view.key.startsWith('injury:')) return [data ? data.label : 'Douleur', data ? 'Douleur au pic, sur 10 · ' + data.weeks.length + ' semaines' : ''];
  if (view.key.startsWith('session:')) {
    if (!data) return [view.title || '', ''];
    const start = startedAt(data.fact);
    return [data.title || kindName[data.kind] || data.kind, longDay(data.date) + (start ? ' · départ ' + start : '')];
  }
  if (!data) return [view.title || '', ''];
  return [entryTitle(data), [kindWord[data.kind], humanDate(data.occurred_on)].filter(Boolean).join(' · ')];
}
function skeleton() {
  const box = el('div', null, 'n-skel');
  box.setAttribute('aria-hidden', 'true');
  ['title', 'tiles', 'line', 'line short', 'block', 'line', 'line short'].forEach(kind => box.append(el('i', null, 'n-sk ' + kind)));
  return box;
}
function failure(text, retry) {
  return add(el('div', null, 'n-empty'), el('p', text, 'n-meta'), button('Réessayer', retry, 'n-action'));
}
// `y` : la hauteur où montrer l'écran, posée avant de lire l'état de la barre du haut.
export function render(y) {
  const view = screen(), data = app.views[view.key];
  const opened = new Set([...content.querySelectorAll('details[open] > summary')].map(node => node.textContent));
  content.replaceChildren();
  document.querySelectorAll('[data-tab]').forEach(node => {
    if (node.dataset.tab === app.active) node.setAttribute('aria-current', 'page');
    else node.removeAttribute('aria-current');
  });
  const [title, subtitle] = heading(view, data);
  $('lt-title').textContent = title; $('lt-sub').textContent = subtitle; $('bar-title').textContent = title;
  $('back').hidden = Boolean(view.root); $('settings').hidden = !view.root;
  const route = topRoute();
  history.replaceState(history.state, '', route && route.k === 'entry' ? '/carnet#entry=' + encodeURIComponent(route.id) : '/carnet');
  if (!data) content.append(app.failures[view.key] ? failure(app.failures[view.key], () => refresh(true)) : skeleton());
  else if (view.key === 'today') renderToday(data);
  else if (view.key === 'course') renderCourse(data);
  else if (view.key.startsWith('week:')) renderWeek(data, view.root);
  else if (view.key === 'weeks') renderWeeks(data);
  else if (view.key === 'archive') renderArchive(data);
  else if (view.key.startsWith('injury:')) renderInjury(data);
  else if (view.key.startsWith('session:')) renderOutcome(data);
  else renderEntry(data);
  content.querySelectorAll('details > summary').forEach(node => { if (opened.has(node.textContent)) node.parentElement.open = true; });
  if (y != null) scrollTo(0, y);
  barState(true);
}
// Comme UINavigationController, sans rien inventer : l'écran poussé arrive par la
// droite par-dessus le précédent, qui recule d'un tiers en s'assombrissant ; le retour
// (bouton, onglet retouché, glissement) fait l'inverse ; la barre d'onglets ne bouge
// jamais et celle du haut passe d'un écran à l'autre en fondu. L'écran qui bouge est
// une copie figée : le vrai est déjà dans son état final, sous elle.
// Les écrans poussés n'entrent pas dans l'historique du navigateur : son geste de
// retour fait glisser une capture de toute la page, barre d'onglets comprise, ce
// qu'aucune app iOS ne fait (l'athlète, 2026-10-09).
const topBar = $('bar');
export const pageNode = $('page');
const barInner = topBar.querySelector('.n-topbar-in');
const SHIFT = 0.3, DURATION = 350;
export const EASE = 'cubic-bezier(.32, .72, 0, 1)';
export function remember() { app.scrolls[screen().key] = scrollY; }
// Une copie de l'écran tel qu'on le voit, posée sous les deux barres.
export function freeze(under = false) {
  const rect = pageNode.getBoundingClientRect(), copy = pageNode.cloneNode(true);
  copy.removeAttribute('id'); copy.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
  copy.style.cssText = 'position:absolute;margin:0;transform:none;left:' + rect.left + 'px;top:' + rect.top + 'px;width:' + rect.width + 'px';
  const layer = add(el('div', null, under ? 'n-move under' : 'n-move'), copy);
  layer.setAttribute('aria-hidden', 'true');
  pageNode.after(layer);  // à côté de l'original : mêmes ancêtres, donc mêmes styles
  return layer;
}
// La barre du haut telle qu'on la voit, figée par-dessus la vraie le temps de la
// transition : l'une s'efface pendant que l'autre paraît, comme sur iOS.
export function freezeBar() {
  const copy = barInner.cloneNode(true);
  copy.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
  copy.querySelector('.c').style.opacity = getComputedStyle($('bar-title')).opacity;
  copy.classList.add('n-barcopy'); copy.setAttribute('aria-hidden', 'true');
  topBar.append(copy);
  return copy;
}
// t = 0 : l'écran du dessus couvre tout ; t = 1 : il est sorti à droite.
export function paintMove(t) {
  const width = innerWidth;
  app.move.top.style.transform = 'translateX(' + t * width + 'px)';
  app.move.bottom.style.transform = 'translateX(' + -SHIFT * (1 - t) * width + 'px)';
  app.move.bottom.style.filter = 'brightness(' + (0.92 + 0.08 * t) + ')';
  const shown = app.move.back ? t : 1 - t;
  barInner.style.opacity = String(shown);
  app.move.was.style.opacity = String(1 - shown);
}
export function animate(to, ms, after) {
  const ease = 'transform ' + ms + 'ms ' + EASE + ', filter ' + ms + 'ms ' + EASE + ', opacity ' + ms + 'ms ' + EASE;
  for (const node of [app.move.top, app.move.bottom, barInner, app.move.was]) node.style.transition = ease;
  paintMove(to);
  app.move.after = after;
  app.move.timer = setTimeout(land, ms);
}
// Termine la transition en cours, tout de suite (un autre geste ne l'attend pas).
export function land() {
  if (!app.move) return;
  const done = app.move; app.move = null;
  clearTimeout(done.timer);
  if (done.after) done.after();
  for (const node of [done.top, done.bottom]) if (node.classList.contains('n-move')) node.remove();
  done.was.remove();
  pageNode.style.transition = pageNode.style.transform = pageNode.style.filter = '';
  barInner.style.transition = barInner.style.opacity = '';
}
export function startMove(top, bottom, back, was, from) {
  app.move = {top, bottom, back, was};
  for (const node of [top, bottom, barInner, was]) node.style.transition = 'none';
  paintMove(from);
  void top.offsetWidth;
}
export function push(route) {
  land(); remember(); app.generation++;
  const below = calm.matches ? null : freeze(true), was = below && freezeBar();
  app.stacks[app.active].push(route);
  delete app.scrolls[screen().key];
  render(0); ensure();
  if (!below) return;
  startMove(freeze(), below, false, was, 1);
  animate(0, DURATION);
}
// Remplace l'écran du dessus sans l'empiler : passer d'une zone surveillée à l'autre
// n'est pas un pas de plus dans la navigation.
export function swap(route) {
  app.generation++;
  const stack = app.stacks[app.active];
  stack[stack.length - 1] = route;
  render(0); ensure();
}
// Recule d'un cran, ou jusqu'à la racine ; l'écran retrouve sa position.
export function leave(toRoot) {
  app.generation++;
  if (toRoot) app.stacks[app.active] = []; else app.stacks[app.active].pop();
  render(app.scrolls[screen().key] || 0); ensure();
}
function back(toRoot = false) {
  if (!app.stacks[app.active].length) return;
  land();
  const above = calm.matches ? null : freeze(), was = above && freezeBar();
  leave(toRoot);
  if (!above) return;
  startMove(above, pageNode, true, was, 0);
  animate(1, DURATION);
}
export function select(tab) {
  if (tab === app.active) {
    // Retoucher l'onglet actif : d'abord à sa racine, puis tout en haut.
    if (app.stacks[app.active].length) back(true);
    else scrollTo({top: 0, behavior: calm.matches ? 'auto' : 'smooth'});
    return;
  }
  land(); remember(); app.generation++;
  app.active = tab;
  render(app.scrolls[screen().key] || 0);
  ensure();
}
// La barre du haut : son filet paraît dès qu'on défile ; le titre s'y replie quand le
// grand titre passe dessous. En fondu au défilement, d'un coup quand l'écran change.
// Pendant la séance guidée, c'est son grand titre qui se replie.
export function barState(instant = false) {
  const title = app.seance ? $('title') : $('lt-title');
  topBar.classList.toggle('instant', instant);
  topBar.classList.toggle('scrolled', scrollY > 2);
  topBar.classList.toggle('titled', title.getBoundingClientRect().bottom < topBar.getBoundingClientRect().bottom);
  if (instant) { void topBar.offsetWidth; topBar.classList.remove('instant'); }
}
let ticking = false;
addEventListener('scroll', () => {
  if (ticking) return;
  ticking = true;
  requestAnimationFrame(() => { ticking = false; barState(); });
}, {passive: true});
$('back').addEventListener('click', () => back());
document.querySelectorAll('[data-tab]').forEach(node => node.addEventListener('click', () => { message(''); select(node.dataset.tab); }));
