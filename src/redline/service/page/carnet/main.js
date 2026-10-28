import {$} from '../shared/dom.js';
import {applyTheme} from '../shared/theme.js';
import {app} from './state.js';
import {connect, embedded, handoff} from './session.js';
import {isoDate} from './format.js';
import {render} from './nav.js';
import {refresh} from './data.js';
import {showTheme} from './sheet.js';
import {openLink} from './seance.js';

// The notebook: three sections computed by the service at each opening
// (/carnet-api/today, /week/<W>, /race). The page formats, it computes nothing:
// the figures come from the ledger and the plan, not from here.
showTheme(applyTheme());
window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault(); app.installPrompt = event;
  if (app.active === 'today' && !app.stacks.today.length) render();
});
// On return to the app: nothing moves if the figures are under a minute old, otherwise
// only a section that changed is redrawn, at the same position.
document.addEventListener('visibilitychange', () => {
  if (document.hidden || app.standalone || $('connected').hidden) return;
  const fresh = app.loadedAt && Date.now() - app.loadedAt < 60000 && app.today && isoDate(new Date()) === app.today.date;
  if (!fresh) refresh();
});
// An app does not zoom: the viewport tag forbids it, this stops the iOS pinch, which
// Safari handles first. The viewer has its own zoom.
document.addEventListener('gesturestart', event => event.preventDefault());
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/carnet-sw.js', {scope: '/carnet'}).catch(() => {});
const capability = new URLSearchParams(location.hash.slice(1)).get('t');
// A session link (/s#t=…) opens the session alone; otherwise, the notebook.
if (location.pathname === '/s') openLink();
else if (capability && embedded) handoff(capability); else connect(capability);
