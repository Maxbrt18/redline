import {$, add, button, el} from '../shared/dom.js';
import {retry} from '../strength/send.js';
import {app, calm} from './state.js';
import {content, message} from './ui.js';
import {entryTitle, fmt, humanDate, kindName, kindWord, longDay, raceFacts, startedAt, weekNumber, weekSpan, weekState} from './format.js';
import {renderToday} from './screens/today.js';
import {renderWeek, renderWeeks} from './screens/week.js';
import {renderCourse} from './screens/course.js';
import {renderRacePlan} from './screens/raceplan.js';
import {renderInjury} from './screens/injury.js';
import {renderOutcome} from './screens/detail.js';
import {renderArchive, renderEntry} from './screens/archives.js';
import {ensure, refresh} from './data.js';

// ------------------------------------------------------------ navigation
// Three roots always visible; everything else is pushed on top and closes one step
// back toward where the user came from.
// A screen already seen is shown from page memory, then recomputed behind; it is
// redrawn only if a figure changed, and keeps its position.
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
  // One watched area per screen: the key and the request name it.
  if (route.k === 'injury') return {key: 'injury:' + (route.zone || ''), path: 'injury' + (route.zone ? '?zone=' + encodeURIComponent(route.zone) : '')};
  if (route.k === 'race-plan') return {key: 'race-plan', path: 'race-plan'};
  if (route.k === 'session') return {key: 'session:' + route.id, path: 'session/' + encodeURIComponent(route.id), title: route.title};
  return {key: 'entry:' + route.id, path: 'entries/' + encodeURIComponent(route.id), title: route.title};
}
// The title always says where we are; the subtitle, from when.
function heading(view, data) {
  if (view.key === 'today') return ['Aujourd’hui', data ? longDay(data.date) + ' · semaine ' + weekNumber(data.week) : ''];
  if (view.key === 'course') return data && data.race ? [data.race.name, raceFacts(data.race)] : ['Course', ''];
  if (view.key.startsWith('week:')) {
    const label = view.key.slice(5);
    return label ? ['Semaine ' + weekNumber(label), weekSpan(label) + (weekState(label) ? ' · ' + weekState(label) : '')] : ['Semaine', ''];
  }
  if (view.key === 'race-plan') {
    const race = app.views.course && app.views.course.race;
    return ['Plan de course', [race ? race.name : null, data ? fmt(data.plan.length_km) + ' km · D+ ' + fmt(data.plan.dplus_m, 0) + ' m' : null].filter(Boolean).join(' · ')];
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
// `y`: the height at which to show the screen, set before reading the top bar state.
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
  else if (view.key === 'race-plan') renderRacePlan(data);
  else renderEntry(data);
  content.querySelectorAll('details > summary').forEach(node => { if (opened.has(node.textContent)) node.parentElement.open = true; });
  if (y != null) scrollTo(0, y);
  barState(true);
}
// Like UINavigationController, inventing nothing: the pushed screen arrives from the
// right over the previous one, which recedes by a third while darkening; going back
// (button, retapped tab, swipe) does the reverse; the tab bar never moves and the top
// bar cross-fades from one screen to the next. The moving screen is a frozen copy: the
// real one is already in its final state, underneath.
// Pushed screens do not enter the browser history: its back gesture slides a snapshot
// of the whole page, tab bar included, which no iOS app does.
const topBar = $('bar');
export const pageNode = $('page');
const barInner = topBar.querySelector('.n-topbar-in');
const SHIFT = 0.3, DURATION = 350;
export const EASE = 'cubic-bezier(.32, .72, 0, 1)';
export function remember() { app.scrolls[screen().key] = scrollY; }
// A copy of the screen as seen, placed under the two bars.
export function freeze(under = false) {
  const rect = pageNode.getBoundingClientRect(), copy = pageNode.cloneNode(true);
  copy.removeAttribute('id'); copy.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
  copy.style.cssText = 'position:absolute;margin:0;transform:none;left:' + rect.left + 'px;top:' + rect.top + 'px;width:' + rect.width + 'px';
  const layer = add(el('div', null, under ? 'n-move under' : 'n-move'), copy);
  layer.setAttribute('aria-hidden', 'true');
  pageNode.after(layer);  // next to the original: same ancestors, hence same styles
  return layer;
}
// The top bar as seen, frozen over the real one for the duration of the transition:
// one fades out while the other appears, as on iOS.
export function freezeBar() {
  const copy = barInner.cloneNode(true);
  copy.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
  copy.querySelector('.c').style.opacity = getComputedStyle($('bar-title')).opacity;
  copy.classList.add('n-barcopy'); copy.setAttribute('aria-hidden', 'true');
  topBar.append(copy);
  return copy;
}
// t = 0: the top screen covers everything; t = 1: it has left to the right.
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
// Ends the current transition at once (another gesture does not wait for it).
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
// Replaces the top screen without stacking: switching from one watched area to another
// is not one more step in the navigation.
export function swap(route) {
  app.generation++;
  const stack = app.stacks[app.active];
  stack[stack.length - 1] = route;
  render(0); ensure();
}
// Steps back one level, or to the root; the screen recovers its position.
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
    // Retapping the active tab: first to its root, then to the very top.
    if (app.stacks[app.active].length) back(true);
    else scrollTo({top: 0, behavior: calm.matches ? 'auto' : 'smooth'});
    return;
  }
  land(); remember(); app.generation++;
  app.active = tab;
  render(app.scrolls[screen().key] || 0);
  ensure();
}
// The top bar: its rule appears as soon as the page scrolls; the title collapses into it
// when the large title passes under. Fades on scroll, switches at once on screen change.
// During the guided session, its large title is the one that collapses.
export function barState(instant = false) {
  const title = app.workout ? $('title') : $('lt-title');
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
