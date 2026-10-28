// The notebook state, shared by its modules. An object rather than variables: a module
// cannot reassign what it imports, it modifies `app.today`, never `today`.
export const app = {
  active: 'today', generation: 0, installPrompt: null,
  today: null, loadedAt: null, openDay: null, refreshing: false,
  // Each tab keeps its stack of pushed screens, like an iOS app:
  // the root at the bottom, the visible screen on top.
  stacks: {today: [], week: [], course: []},
  // Page memory; the latest roots are also kept on the device
  // (see `keep`), to open without a network.
  views: {}, signatures: {}, failures: {}, scrolls: {},
  seance: null,   // token of the guided session open over the notebook, else null
  standalone: false,  // opened from a session link (/s): the session alone, without the notebook
  stash: null,    // figures kept on the device, read back at opening
  connecting: null,  // the sign-in link exchange in progress, which other calls wait for
  move: null,     // the current transition: {top, bottom, back, was, after, timer}
};
export const skies = new Map();
export const flights = new Map();
export const loading = new Map();
export const calm = matchMedia('(prefers-reduced-motion: reduce)');
