import {$} from '../shared/dom.js';
import {MAX_NOTE, credentials, localDate, save, session, slotsOf, state} from './session.js';
import {renderReview} from './review.js';
import {renderSession} from './render.js';

// --------------------------------------------------------------------- send

function say(text) {
  const status = $('status');
  if (status) status.textContent = text;
}

/* Le compte-rendu ne se perd plus.
 *
 * Avant : un POST raté affichait « Réessaie dans un instant » et n'essayait
 * jamais. Or c'est le seul chemin par lequel la séance revient au coach, et il
 * s'emprunte depuis une cave, un vestiaire, ou pendant un redéploiement du VPS.
 * Maintenant l'intention est persistée avec le reste de l'état, et le renvoi part
 * seul au retour du réseau, au retour sur l'onglet, et à la réouverture du lien.
 * Aucun risque de doublon : la route rend une séance rapportable une seule fois. */
let sending = false;
let retryTimer = null;
let retryDelay = 5000;

function scheduleRetry() {
  if (retryTimer || state.sent || !state.pending) return;
  retryTimer = setTimeout(async () => {
    retryTimer = null;
    await retry();
  }, retryDelay);
  retryDelay = Math.min(retryDelay * 2, 5 * 60 * 1000);
}

export function cancelRetry() {
  if (retryTimer) clearTimeout(retryTimer);
  retryTimer = null;
}

export async function retry() {
  if (!state || !state.pending || state.sent || sending) return;
  await send();
}

export async function send() {
  if (sending || state.sent) return;
  sending = true;
  state.pending = true;
  state.performedOn = state.performedOn || localDate(state.startedAt || Date.now());
  const persisted = save();
  say('Envoi…');
  const report = {
    // Date civile du PREMIER exercice, pas celle du serveur au moment où un
    // téléphone hors ligne réussira finalement à poster le compte-rendu.
    performed_on: state.performedOn,
    shin_before: state.shinBefore,
    shin: state.shin,
    minutes: state.minutes,
    note: (state.note || '').slice(0, MAX_NOTE),
    exercises: session.exercises.map((exercise, index) => {
      const progress = state.steps[index];
      if (!progress) return null;
      return {
        i: index,
        score: progress.score,
        skipped: !!(progress.skipped || progress.gated),
        gated: !!progress.gated,
        status: progress.gated ? 'gated' : progress.skipped ? 'skipped'
          : progress.stopped || progress.slots.length < slotsOf(index).length ? 'partial' : 'completed',
        note: progress.note || '',
        // POSITIONNEL, blancs compris : c'est la position dans la liste des
        // créneaux qui dit à quelle jambe appartient un compte, et le serveur la
        // relit sur la séance. Filtrer les blancs ici décalait tout dès qu'une
        // série était laissée vide, et attribuait les comptes au mauvais côté.
        // (Les blocs de contacts sont des créneaux comme les autres depuis le
        // 2026-07-31 : plus de cas particulier.)
        reps: progress.slots,
      };
    }).filter(Boolean),
  };
  state.outgoingReport = state.outgoingReport || report;
  save();
  renderReview();
  say('Envoi…');
  try {
    const response = await fetch('/strength-report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...credentials, report: state.outgoingReport }),
    });
    let result = {};
    try { result = await response.json(); } catch (error) { /* ancien serveur */ }
    if (response.status === 410 || result.status === 'expired') {
      state.pending = false;
      state.outgoingReport = null;
      save();
      say('Ce lien a expiré. Redemande la séance au coach.');
      return;
    }
    if (response.status === 429 || result.status === 'quota') {
      state.pending = false;
      state.outgoingReport = null;
      save();
      say('Envoi refusé : limite de sécurité atteinte. Préviens le coach.');
      return;
    }
    if (response.status === 403) {
      state.pending = false;
      state.outgoingReport = null;
      save();
      say('Ce lien est invalide. Redemande la séance au coach.');
      return;
    }
    if (response.status === 400) {
      state.pending = false;
      state.outgoingReport = null;
      save();
      say('Compte-rendu invalide. Garde cette page ouverte et préviens le coach.');
      return;
    }
    if (!response.ok) throw new Error(String(response.status));
    state.sent = true;
    state.duplicate = result.status === 'duplicate';
    state.pending = false;
    retryDelay = 5000;
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = null;
    save();
    renderSession();
    say(result.status === 'duplicate'
      ? 'Déjà reçu pour ce lien. Aucun nouvel enregistrement.'
      : 'Envoyé. Le coach a la séance.');
  } catch (error) {
    say(persisted
      ? 'Pas de réseau pour l\'instant. C\'est gardé, ça repartira tout seul.'
      : 'Envoi impossible. Garde cette page ouverte : elle va réessayer toute seule.');
    scheduleRetry();
  } finally {
    sending = false;
    if (!state.pending && !state.sent) {
      for (const field of $('review').querySelectorAll('input, textarea, .p-scale__step')) field.disabled = false;
    }
  }
}
