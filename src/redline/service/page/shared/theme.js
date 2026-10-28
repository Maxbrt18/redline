// Le thème suit le téléphone, sauf choix contraire sur cet appareil : le seul réglage
// gardé dans le navigateur, et il ne dit rien de l'athlète. Le carnet le choisit, le
// renfo l'applique : les deux pages lisent la même clé.
import {remembered} from './storage.js';

const KEY = 'carnet-theme';
const themeColors = {light: '#f7f5f2', dark: '#110e0d'};
// Sur iOS 26+, la bande sous l'heure suit la barre du haut (voir carnet/shell.css) ;
// `theme-color` reste tenu à jour pour l'écran de lancement et les iOS plus anciens.
const veiled = {light: '#c6c4c2', dark: '#0e0b0a'};  // --bg sous le voile noir à 20 %
let barTint = null;
const systemDark = matchMedia('(prefers-color-scheme: dark)');
function paintBar() {
  const scheme = document.documentElement.dataset.theme || (systemDark.matches ? 'dark' : 'light');
  const color = barTint === 'viewer' ? '#000000' : barTint === 'sheet' ? veiled[scheme] : themeColors[scheme];
  document.querySelector('meta[name="theme-color"]').content = color;
}
systemDark.addEventListener('change', paintBar);
export function tintBar(tint) { barTint = tint; paintBar(); }
// Applique le choix gardé (ou celui qu'on passe) et rend le thème épinglé, ou null.
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
