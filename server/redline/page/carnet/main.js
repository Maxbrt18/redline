import {$} from '../shared/dom.js';
import {applyTheme} from '../shared/theme.js';
import {app} from './state.js';
import {connect, embedded, handoff} from './session.js';
import {isoDate} from './format.js';
import {render} from './nav.js';
import {refresh} from './data.js';
import {showTheme} from './sheet.js';
import {openLink} from './seance.js';

// Le carnet : trois rubriques calculées par le service à chaque ouverture
// (/carnet-api/today, /week/<W>, /race). La page met en forme, elle ne compte rien :
// les chiffres viennent du registre et du programme, pas d'ici.
showTheme(applyTheme());
window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault(); app.installPrompt = event;
  if (app.active === 'today' && !app.stacks.today.length) render();
});
// Au retour dans l'app : rien ne bouge si les chiffres ont moins d'une minute, et
// sinon seule une rubrique qui a changé se redessine, à la même position.
document.addEventListener('visibilitychange', () => {
  if (document.hidden || app.standalone || $('connected').hidden) return;
  const fresh = app.loadedAt && Date.now() - app.loadedAt < 60000 && app.today && isoDate(new Date()) === app.today.date;
  if (!fresh) refresh();
});
// Une app ne zoome pas : la balise viewport l'interdit, ceci retient le pincement
// d'iOS, que Safari fait passer avant elle. La visionneuse a son propre zoom.
document.addEventListener('gesturestart', event => event.preventDefault());
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/carnet-sw.js', {scope: '/carnet'}).catch(() => {});
const capability = new URLSearchParams(location.hash.slice(1)).get('t');
// Un lien de séance (/s#t=…) ouvre la séance seule ; sinon, le carnet.
if (location.pathname === '/s') openLink();
else if (capability && embedded) handoff(capability); else connect(capability);
