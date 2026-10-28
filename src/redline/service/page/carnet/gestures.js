import {$} from '../shared/dom.js';
import {app, calm} from './state.js';
import {viewer} from './viewer.js';
import {animate, freeze, freezeBar, leave, pageNode, paintMove, render, startMove, topRoute} from './nav.js';
import {ensure, refresh} from './data.js';
import {sheetShown} from './sheet.js';

// ------------------------------------------------------------ tirer pour actualiser
// La PWA iOS n'en a pas : en haut de page, tirer au-delà du seuil recalcule tout.
const ptr = $('ptr');
const PULL = 64;
let pull = null;
function pulled(distance, spinning = false) {
  pageNode.style.transform = distance ? 'translateY(' + distance + 'px)' : '';
  ptr.style.opacity = String(Math.min(1, distance / PULL));
  ptr.style.transform = 'translateY(' + (distance - 40) + 'px) rotate(' + distance * 4 + 'deg)';
  ptr.classList.toggle('spin', spinning);
  pageNode.classList.toggle('settle', !pull);
}
window.addEventListener('touchstart', event => {
  if (scrollY > 0 || app.refreshing || app.seance || sheetShown() || $('connected').hidden || event.touches.length !== 1) return;
  pull = {y: event.touches[0].clientY, d: 0};
}, {passive: true});
window.addEventListener('touchmove', event => {
  if (!pull) return;
  const dy = event.touches[0].clientY - pull.y;
  if (dy <= 0 || scrollY > 0) { if (pull.d) { pull.d = 0; pulled(0); } return; }
  if (event.cancelable) event.preventDefault();
  pull.d = Math.min(120, dy * 0.5); pulled(pull.d);
}, {passive: false});
window.addEventListener('touchend', async () => {
  if (!pull) return;
  const go = pull.d >= PULL; pull = null;
  if (!go) { pulled(0); return; }
  pulled(52, true);
  await refresh(true);
  pulled(0);
});

// ------------------------------------------------------------ glisser pour revenir
// Depuis n'importe où sur l'écran, comme depuis iOS 26 : un geste franchement
// horizontal vers la droite. Lâché après la moitié ou avec de l'élan, l'écran part ;
// sinon il revient, à la même hauteur. Un champ ou un contenu qui défile de côté
// garde son geste.
let swipe = null;
function sideways(node) {
  for (node = node instanceof Element ? node : null; node && node !== document.body; node = node.parentElement) {
    if (node.matches('input, textarea, select, [contenteditable]')) return true;
    if (node.scrollWidth > node.clientWidth + 1 && /auto|scroll/.test(getComputedStyle(node).overflowX)) return true;
  }
  return false;
}
// Le geste s'écoute sur l'élément touché : l'écran d'avant se redessine sous le doigt
// et le détache, mais iOS continue de lui envoyer la suite du geste, et à lui seul.
window.addEventListener('touchstart', event => {
  if (event.touches.length !== 1 || !app.stacks[app.active].length || app.move || swipe
      || app.seance || sheetShown() || !viewer.hidden || $('connected').hidden || sideways(event.target)) return;
  const touch = event.touches[0], node = event.target;
  swipe = {node, x: touch.clientX, y: touch.clientY, lastX: touch.clientX, lastT: event.timeStamp, prevX: touch.clientX, prevT: event.timeStamp};
  node.addEventListener('touchmove', swipeMove, {passive: false});
  node.addEventListener('touchend', swipeEnd);
  node.addEventListener('touchcancel', swipeEnd);
}, {passive: true});
function swipeMove(event) {
  if (!swipe) return;
  const touch = event.touches[0], dx = touch.clientX - swipe.x, dy = touch.clientY - swipe.y;
  if (!swipe.route) {
    if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
    // Un défilement déjà parti, un geste vertical ou vers la gauche : ce n'est pas un retour.
    if (!event.cancelable || dx < Math.abs(dy) * 1.5 || (pull && pull.d)) { swipeEnd(event); return; }
    pull = null; pulled(0); pageNode.classList.remove('settle');
    const above = freeze(), was = freezeBar();
    Object.assign(swipe, {route: topRoute(), y: scrollY});
    leave(false);
    startMove(above, pageNode, true, was, 0);
  }
  event.preventDefault();
  Object.assign(swipe, {prevX: swipe.lastX, prevT: swipe.lastT, lastX: touch.clientX, lastT: event.timeStamp});
  paintMove(Math.min(1, Math.max(0, dx) / innerWidth));
}
function swipeEnd(event) {
  const done = swipe; swipe = null;
  if (!done) return;
  done.node.removeEventListener('touchmove', swipeMove);
  done.node.removeEventListener('touchend', swipeEnd);
  done.node.removeEventListener('touchcancel', swipeEnd);
  if (!done.route) return;
  const progress = Math.min(1, Math.max(0, done.lastX - done.x) / innerWidth);
  const speed = event.timeStamp - done.lastT < 100 ? (done.lastX - done.prevX) / Math.max(1, done.lastT - done.prevT) : 0;
  const gone = event.type === 'touchend' && (progress > 0.5 || speed > 0.35) && speed > -0.2;
  const rest = gone ? 1 - progress : progress;
  animate(gone ? 1 : 0, calm.matches ? 0 : Math.round(150 + 200 * rest), gone ? null : () => {
    // Ramené : l'écran repris tel quel, à la même hauteur, avant que la copie parte.
    app.generation++; app.stacks[app.active].push(done.route); render(done.y); ensure();
  });
}
