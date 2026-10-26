import {$, el} from '../shared/dom.js';
import {tintBar} from '../shared/theme.js';
import {calm} from './state.js';
import {message} from './ui.js';

// ------------------------------------------------------------ visionneuse
// Une carte s'agrandit dans l'app, en plein écran sur fond noir, sans passer par
// Safari : pincer ou toucher deux fois pour zoomer, glisser vers le bas pour fermer.
// Partager ouvre la feuille du téléphone (Photos, Messages) ; sans elle, l'image se
// télécharge. L'image est préchargée : iOS n'ouvre la feuille que tout de suite
// après le toucher, pas après une attente réseau.
export const viewer = $('viewer');
const stage = viewer.querySelector('.n-viewer-stage'), viewed = $('viewer-img');
const blobs = new Map();
let viewing = null, zoom = {s: 1, x: 0, y: 0}, gesture = null, lastTap = 0;
const touches = new Map();
export function cardBlob(src) {
  if (!blobs.has(src)) {
    const slot = {blob: null};
    slot.promise = fetch(src, {credentials: 'same-origin'})
      .then(response => { if (!response.ok) throw new Error('image'); return response.blob(); })
      .then(blob => { slot.blob = blob; return blob; })
      .catch(error => { blobs.delete(src); throw error; });
    blobs.set(src, slot);
  }
  return blobs.get(src);
}
export const shareable = (() => {
  try { return Boolean(navigator.canShare && navigator.canShare({files: [new File([''], 'carte.png', {type: 'image/png'})]})); }
  catch { return false; }
})();
export async function shareCard(card) {
  const slot = cardBlob(card.src);
  try {
    const blob = slot.blob || await slot.promise;
    const file = new File([blob], card.name, {type: blob.type || 'image/png'});
    if (shareable && navigator.canShare({files: [file]})) { await navigator.share({files: [file]}); return; }
    const link = el('a'); link.href = URL.createObjectURL(blob); link.download = card.name;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 4000);
  } catch (error) {
    if (error && error.name === 'AbortError') return;
    message('Partage impossible pour le moment. Réessaie dans un instant.');
  }
}
function placeImage(settle) {
  stage.classList.toggle('settle', Boolean(settle));
  viewed.style.transform = 'translate(' + zoom.x + 'px, ' + zoom.y + 'px) scale(' + zoom.s + ')';
}
export function openViewer(card) {
  viewing = card; cardBlob(card.src).promise.catch(() => {});
  zoom = {s: 1, x: 0, y: 0}; placeImage(false); viewer.style.background = '';
  viewed.src = card.src; viewed.alt = card.alt || '';
  viewer.hidden = false; document.documentElement.classList.add('n-locked', 'n-viewing');
  void viewer.offsetWidth; viewer.classList.add('open'); tintBar('viewer');
  $('viewer-close').focus({preventScroll: true});
}
export function closeViewer() {
  if (viewer.hidden) return;
  touches.clear(); gesture = null;
  viewer.classList.remove('open'); document.documentElement.classList.remove('n-locked', 'n-viewing'); tintBar(null);
  setTimeout(() => { viewer.hidden = true; viewed.removeAttribute('src'); }, calm.matches ? 0 : 180);
}
$('viewer-close').addEventListener('click', () => closeViewer(false));
// La page ne défile pas sous la visionneuse (pas d'overflow: hidden, qui fige la bande d'état d'iOS).
viewer.addEventListener('touchmove', event => { if (event.cancelable) event.preventDefault(); }, {passive: false});
$('viewer-share').addEventListener('click', () => viewing && shareCard(viewing));
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeViewer(false); });
const spread = points => Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
const middle = points => ({x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2});
function begin() {
  const points = [...touches.values()];
  if (points.length === 2) gesture = {kind: 'pinch', d: spread(points), s: zoom.s, mid: middle(points), x: zoom.x, y: zoom.y};
  else if (points.length === 1) gesture = {kind: zoom.s > 1 ? 'pan' : 'drag', start: points[0], x: zoom.x, y: zoom.y, at: Date.now()};
}
stage.addEventListener('pointerdown', event => {
  stage.setPointerCapture(event.pointerId);
  touches.set(event.pointerId, {x: event.clientX, y: event.clientY}); begin();
});
stage.addEventListener('pointermove', event => {
  if (!touches.has(event.pointerId) || !gesture) return;
  touches.set(event.pointerId, {x: event.clientX, y: event.clientY});
  const points = [...touches.values()];
  if (gesture.kind === 'pinch' && points.length === 2) {
    const mid = middle(points);
    zoom = {s: Math.min(4, Math.max(1, gesture.s * spread(points) / gesture.d)),
      x: gesture.x + mid.x - gesture.mid.x, y: gesture.y + mid.y - gesture.mid.y};
  } else if (gesture.kind === 'pan') {
    zoom.x = gesture.x + points[0].x - gesture.start.x; zoom.y = gesture.y + points[0].y - gesture.start.y;
  } else if (gesture.kind === 'drag') {
    zoom.y = Math.max(0, points[0].y - gesture.start.y);
    viewer.style.background = 'rgb(0 0 0 / ' + Math.max(0.35, 1 - zoom.y / 420) + ')';
  }
  placeImage(false);
});
function release(event) {
  if (!touches.delete(event.pointerId) || !gesture) return;
  if (touches.size) { begin(); return; }
  const done = gesture; gesture = null;
  const moved = Math.hypot(event.clientX - (done.start || event).x, event.clientY - (done.start || event).y);
  if (done.kind === 'drag' && zoom.y > 120) { closeViewer(false); return; }
  viewer.style.background = '';
  if (done.kind !== 'pinch' && moved < 10 && Date.now() - done.at < 300) {
    // Double toucher : zoomer sur la carte, ou revenir à la taille de l'écran.
    if (Date.now() - lastTap < 320) { zoom = zoom.s > 1 ? {s: 1, x: 0, y: 0} : {s: 2.5, x: 0, y: 0}; lastTap = 0; placeImage(true); return; }
    lastTap = Date.now();
  }
  if (zoom.s <= 1.02) zoom = {s: 1, x: 0, y: 0};
  placeImage(true);
}
stage.addEventListener('pointerup', release);
stage.addEventListener('pointercancel', release);
