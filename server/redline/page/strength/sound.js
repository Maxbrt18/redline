import {$, button, el} from '../shared/dom.js';

// -------------------------------------------------------------------- son
//
/* Le wake lock existait, le son manquait : pendant un isométrique de 45 s ou une
 * planche, on ne regarde pas l'écran. Un oscillateur nu -- aucun asset, aucun
 * octet de plus dans la page, et rien à autoriser dans la CSP.
 *
 * L'AudioContext se crée AU PREMIER GESTE et jamais au chargement : iOS refuse
 * de le démarrer hors d'un geste utilisateur, et un contexte créé trop tôt
 * reste muet pour toute la séance. */
const SOUND_KEY = 'redline:strength:son';
const INTENSITY_KEY = 'redline:strength:intensite';
/* La voix est à part, et coupée par défaut : sur iPhone, speechSynthesis prend
 * une session audio exclusive qui met en pause la musique des autres apps, et
 * aucune API web ne la fait se mélanger. Les bips Web Audio, eux, se mixent. */
const VOICE_KEY = 'redline:strength:voix';
let audio = null;
let muted = false;
let voice = false;
let intensity = 0.8;
try { muted = localStorage.getItem(SOUND_KEY) === 'non'; } catch (e) { /* private mode */ }
try { voice = localStorage.getItem(VOICE_KEY) === 'oui'; } catch (e) { /* private mode */ }
try { intensity = Math.max(0.2, Math.min(1, Number(localStorage.getItem(INTENSITY_KEY)) || 0.8)); } catch (e) { /* private mode */ }

/* Après une mise en veille, iOS laisse le contexte « interrupted » (un état
 * propre à WebKit, que `resume()` hors geste ne relève pas), voire « running »
 * mais muet. On le ferme donc quand la page se cache, et le premier geste au
 * retour en crée un neuf -- sans quoi il fallait fermer la page et la rouvrir. */
export function dropAudio() {
  try { audio?.close(); } catch (e) { /* déjà fermé */ }
  audio = null;
}

export function wakeAudio() {
  if (muted) return;
  try {
    if (audio && (audio.state === 'interrupted' || audio.state === 'closed')) dropAudio();
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!audio && Context) audio = new Context();
    if (audio && audio.state === 'suspended') audio.resume();
  } catch (e) { audio = null; }
}

export function beep(hertz, seconds, delay = 0) {
  if (muted || !audio || audio.state !== 'running') return;
  try {
    const tone = audio.createOscillator();
    const gain = audio.createGain();
    tone.frequency.value = hertz;
    tone.type = 'triangle';
    // Une rampe, pas un créneau : un gain coupé net claque dans le haut-parleur.
    const start = audio.currentTime + delay;
    const level = 0.65 * intensity;
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.linearRampToValueAtTime(level, start + 0.012);
    gain.gain.setValueAtTime(level, start + Math.max(0.02, seconds - 0.06));
    gain.gain.linearRampToValueAtTime(0.0001, start + seconds);
    tone.connect(gain).connect(audio.destination);
    tone.start(start);
    tone.stop(start + seconds);
  } catch (e) { /* pas de son : la séance continue */ }
}

export const COUNTDOWN_BEEP = 880;
const DONE_BEEP = 523;

export function playSignal(kind) {
  const signals = {
    start: [[988, 0.35, 0]],
    side: [[784, 0.28, 0], [988, 0.28, 0.4]],
    workEnd: [[DONE_BEEP, 0.4, 0], [DONE_BEEP, 0.4, 0.55]],
    restEnd: [[1046, 0.25, 0], [1046, 0.25, 0.4], [1046, 0.4, 0.8]],
  };
  for (const [frequency, seconds, delay] of signals[kind]) beep(frequency, seconds, delay);
}

export function announce(text) {
  if (muted || !voice || !window.speechSynthesis) return;
  const speech = new SpeechSynthesisUtterance(text);
  speech.lang = 'fr-FR';
  speech.volume = intensity;
  speech.rate = 1;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(speech);
}

/* Le son se règle dans une feuille, ouverte par l'icône de la barre, comme les
 * réglages du carnet : un seul style pour toute l'app (Sam, 2026-10-09). */
function setMuted(value) {
  muted = value;
  if (muted) window.speechSynthesis?.cancel();
  try { localStorage.setItem(SOUND_KEY, muted ? 'non' : 'oui'); } catch (e) { /* private mode */ }
  paintSound();
}

// L'icône de la barre : haut-parleur, barré quand le son est coupé.
export function paintSound() {
  const node = $('seance-sound');
  node.classList.toggle('muted', muted);
  node.setAttribute('aria-label', muted ? 'Son coupé' : 'Son');
}

export function renderSoundSettings() {
  const box = $('sound-panel');
  box.replaceChildren();
  const signals = el('div', null, 'n-cell');
  const choice = el('div', null, 'n-seg');
  for (const [label, off] of [['Oui', false], ['Non', true]]) {
    const option = button(label, () => {
      setMuted(off);
      if (!muted) { wakeAudio(); if (audio) Promise.resolve(audio.resume()).then(() => playSignal('start')); }
      renderSoundSettings();
    });
    option.setAttribute('aria-pressed', String(muted === off));
    choice.appendChild(option);
  }
  signals.append(el('span', 'Signaux sonores'), choice);
  const level = el('label', null, 'n-cell');
  const slider = el('input');
  slider.type = 'range'; slider.min = '20'; slider.max = '100'; slider.step = '10';
  slider.value = String(Math.round(intensity * 100));
  slider.setAttribute('aria-label', 'Intensité des signaux');
  slider.addEventListener('input', () => {
    intensity = Number(slider.value) / 100;
    try { localStorage.setItem(INTENSITY_KEY, String(intensity)); } catch (e) { /* private mode */ }
  });
  level.append(el('span', 'Intensité'), slider);
  const spoken = el('label', null, 'n-cell');
  const words = el('span', 'Annoncer le côté à voix haute');
  words.appendChild(el('small', 'Coupe la musique sur iPhone'));
  const check = el('input', null, 'n-switch');
  check.type = 'checkbox'; check.checked = voice;
  check.setAttribute('role', 'switch');
  check.setAttribute('aria-label', 'Annoncer le côté à voix haute');
  check.addEventListener('change', () => {
    voice = check.checked;
    if (!voice) window.speechSynthesis?.cancel();
    try { localStorage.setItem(VOICE_KEY, voice ? 'oui' : 'non'); } catch (e) { /* private mode */ }
  });
  spoken.append(words, check);
  const test = button('Tester le son', () => {
    setMuted(false);
    wakeAudio();
    if (audio) Promise.resolve(audio.resume()).then(() => playSignal('workEnd'));
    renderSoundSettings();
  }, 'n-cell');
  const group = (...cells) => { const node = el('div', null, 'n-group'); node.append(...cells); return node; };
  box.append(group(signals, level), group(spoken), group(test),
    el('p', 'Fin du travail : deux bips graves. Fin du repos : trois bips aigus. Changement de côté : deux notes montantes. Monte aussi le volume multimédia du téléphone si besoin.', 'n-small'));
}
