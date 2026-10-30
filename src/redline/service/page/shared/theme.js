// The theme follows the phone unless overridden on this device: the only setting kept
// in the browser, and it says nothing about the athlete. The notebook picks it, the
// strength session applies it: both pages read the same key.
import {remembered} from './storage.js';

const KEY = 'carnet-theme';
const themeColors = {light: '#f7f5f2', dark: '#110e0d'};
// On iOS 26+, the strip under the clock follows the top bar (see notebook/shell.css);
// `theme-color` stays up to date for the launch screen and older iOS versions.
const veiled = {light: '#c6c4c2', dark: '#0e0b0a'};  // --bg under the 20% black veil
let barTint = null;
const systemDark = matchMedia('(prefers-color-scheme: dark)');
function paintBar() {
  const scheme = document.documentElement.dataset.theme || (systemDark.matches ? 'dark' : 'light');
  const color = barTint === 'viewer' ? '#000000' : barTint === 'sheet' ? veiled[scheme] : themeColors[scheme];
  document.querySelector('meta[name="theme-color"]').content = color;
}
systemDark.addEventListener('change', paintBar);
export function tintBar(tint) { barTint = tint; paintBar(); }
// Applies the stored choice (or the one passed in) and returns the pinned theme, or null.
export function applyTheme(choice = remembered(KEY)) {
  const pinned = choice === 'light' || choice === 'dark' ? choice : null;
  if (pinned) document.documentElement.dataset.theme = pinned;
  else delete document.documentElement.dataset.theme;
  paintBar();
  return pinned;
}
export function chooseTheme(choice) {
  remembered(KEY, choice);
  return applyTheme(choice);
}
