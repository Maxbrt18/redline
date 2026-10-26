// L'état du carnet, partagé par ses modules. Un objet et non des variables : un module
// ne peut pas réassigner ce qu'il importe, il modifie `app.today`, jamais `today`.
export const app = {
  active: 'today', generation: 0, installPrompt: null,
  today: null, loadedAt: null, openDay: null, refreshing: false,
  // Chaque onglet garde sa pile d'écrans poussés, comme une app iOS (carte validée le
  // 2026-10-08) : la racine en bas, l'écran visible en haut.
  stacks: {today: [], week: [], course: []},
  // La mémoire de la page ; les dernières racines sont aussi gardées sur l'appareil
  // (voir `keep`), pour s'ouvrir sans réseau.
  views: {}, signatures: {}, failures: {}, scrolls: {},
  seance: null,   // le jeton de la séance guidée ouverte par-dessus le carnet, sinon null
  standalone: false,  // ouvert depuis un lien de séance (/s) : la séance seule, sans le carnet
  stash: null,    // les chiffres gardés sur l'appareil, relus à l'ouverture
  connecting: null,  // l'échange du lien de connexion en cours, que les autres appels attendent
  move: null,     // la transition en cours : {top, bottom, back, was, after, timer}
};
export const skies = new Map();
export const flights = new Map();
export const loading = new Map();
export const calm = matchMedia('(prefers-reduced-motion: reduce)');
