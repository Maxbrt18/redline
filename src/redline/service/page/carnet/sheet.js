// ------------------------------------------------------------ settings
import {$, add, button, el} from '../shared/dom.js';
import {remembered} from '../shared/storage.js';
import {chooseTheme, tintBar} from '../shared/theme.js';
import {app, calm} from './state.js';
import {chevron, installed, message} from './ui.js';
import {lock} from './session.js';
import {api} from './api.js';
import {stamp} from './data.js';

// In the settings too, as long as the notebook is not on the home screen.
function installRow() {
  const slot = $('install');
  slot.replaceChildren();
  if (installed()) return;
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const how = el('p', ios ? 'Dans Safari : Partager, puis « Sur l’écran d’accueil ».'
    : 'Menu du navigateur, puis « Installer l’application ».', 'n-small');
  how.hidden = true;
  const row = button('', async () => {
    if (app.installPrompt) {
      const prompt = app.installPrompt; app.installPrompt = null;
      await prompt.prompt();
      if ((await prompt.userChoice).outcome === 'accepted') remembered('carnet-install-later', '1');
      installRow();
    } else how.hidden = !how.hidden;
  }, 'n-cell');
  add(row, el('span', 'Sur l’écran d’accueil'), add(el('small', app.installPrompt ? 'Installer' : 'Comment faire'), app.installPrompt ? null : chevron()));
  add(slot, add(el('div', null, 'n-group'), row), how);
}
// The theme: Auto follows the phone, Light or Dark pin it on this device.
export function showTheme(pinned) {
  document.querySelectorAll('[data-theme-choice]').forEach(node => node.setAttribute('aria-pressed', String(node.dataset.themeChoice === (pinned || 'auto'))));
}
document.querySelectorAll('[data-theme-choice]').forEach(node => node.addEventListener('click', () => showTheme(chooseTheme(node.dataset.themeChoice))));
// Signing out is rare and irreversible on this device: an in-place confirmation, which
// also says how to come back and how to cut everything off.
const device = $('device'), logoutRow = $('logout');
logoutRow.addEventListener('click', () => {
  const box = el('div', null, 'n-cell n-confirm');
  add(box, el('p', 'Déconnecter cet appareil ?'),
    el('p', 'Pour revenir : /carnet dans Messenger. Pour déconnecter tous tes appareils : /carnet-revoquer.', 'n-meta'),
    add(el('div'), button('Déconnecter', async node => {
      node.disabled = true;
      try { await api('logout', {}); history.replaceState(null, '', '/carnet'); lock('Cet appareil est déconnecté.'); }
      catch (error) { message(error.message); node.disabled = false; }
    }, 'n-action'), button('Annuler', () => device.replaceChildren(logoutRow), 'n-quiet')));
  device.replaceChildren(box);
});
// ------------------------------------------------------------ sheets
// A sheet rising from the bottom, as on iOS: it closes by dragging it down, by tapping
// outside, with the cross or Escape. The page does not scroll beneath.
// Three sheets share this gesture: the settings, the session sound and the exit of a
// started session. Only one is open at a time.
const sheet = $('sheet');
let openWrap = null, opener = null, sheetTimer = null, settleSheet = null;
export const sheetShown = () => Boolean(document.querySelector('.n-sheetwrap:not([hidden])'));
export function present(wrap, focus) {
  clearTimeout(sheetTimer);
  if (settleSheet) settleSheet();
  if (openWrap) dismiss(true);
  openWrap = wrap; opener = document.activeElement;
  const panel = wrap.querySelector('.n-sheet');
  wrap.hidden = false; document.documentElement.classList.add('n-locked', 'n-veiled');
  void panel.offsetWidth; wrap.classList.add('open'); tintBar('sheet');
  if (focus) panel.querySelector(focus)?.focus({preventScroll: true});
}
export function dismiss(now = false) {
  const wrap = openWrap;
  if (!wrap) return;
  openWrap = null;
  const panel = wrap.querySelector('.n-sheet');
  wrap.classList.remove('open'); panel.style.transform = ''; tintBar(null);
  document.documentElement.classList.remove('n-locked', 'n-veiled');
  settleSheet = () => { settleSheet = null; wrap.hidden = true; if (wrap === sheet) device.replaceChildren(logoutRow); };
  if (now || calm.matches) settleSheet(); else sheetTimer = setTimeout(settleSheet, 260);
  if (opener && opener.isConnected) opener.focus({preventScroll: true});
}
export const closeSheet = dismiss;
$('settings').addEventListener('click', () => {
  installRow(); stamp(); device.replaceChildren(logoutRow);
  present(sheet, '[data-theme-choice][aria-pressed="true"]');
});
document.addEventListener('keydown', event => { if (event.key === 'Escape') dismiss(); });
document.querySelectorAll('.n-sheetwrap').forEach(wrap => {
  const panel = wrap.querySelector('.n-sheet');
  wrap.querySelectorAll('[data-close]').forEach(node => node.addEventListener('click', () => dismiss()));
  wrap.addEventListener('touchmove', event => { if (!panel.contains(event.target) && event.cancelable) event.preventDefault(); }, {passive: false});
  let drag = null;
  panel.addEventListener('touchstart', event => { drag = {y: event.touches[0].clientY, dy: 0}; panel.classList.add('dragging'); }, {passive: true});
  panel.addEventListener('touchmove', event => {
    if (!drag) return;
    drag.dy = Math.max(0, event.touches[0].clientY - drag.y);
    panel.style.transform = drag.dy ? 'translateY(' + drag.dy + 'px)' : '';
    if (drag.dy && event.cancelable) event.preventDefault();
  }, {passive: false});
  panel.addEventListener('touchend', () => {
    if (!drag) return;
    const far = drag.dy > 90; drag = null; panel.classList.remove('dragging');
    if (far) dismiss(); else panel.style.transform = '';
  });
});
