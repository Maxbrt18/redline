import {$} from '../shared/dom.js';
import {MAX_NOTE, credentials, localDate, save, session, slotsOf, state} from './session.js';
import {renderReview} from './review.js';
import {renderSession} from './render.js';

// --------------------------------------------------------------------- send

function say(text) {
  const status = $('status');
  if (status) status.textContent = text;
}

/* The report is never lost.
 *
 * This is the only path by which the session returns to the coach, and it is taken
 * from a basement, a locker room, or during a server redeploy. The intent is
 * persisted with the rest of the state, and the resend fires on its own when the
 * network returns, when the tab is revisited, and when the link is reopened.
 * No duplicate risk: the route accepts a report for a session only once. */
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
    // Calendar date of the FIRST exercise, not the server's date at the moment an
    // offline phone finally manages to post the report.
    performed_on: state.performedOn,
    // Only the zones read are sent; a zone without a figure is not a zero.
    pains: Object.fromEntries(Object.entries(state.pains)
      .filter(([, moments]) => moments.before != null || moments.after != null)
      .map(([slug, moments]) => [slug, {before: moments.before ?? null, after: moments.after ?? null}])),
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
        // POSITIONAL, blanks included: the position in the slot list says which leg a
        // count belongs to, and the server reads it back from the session. Filtering
        // blanks here would shift everything as soon as a set was left empty, and
        // assign counts to the wrong side. (Contact blocks are slots like the others.)
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
    try { result = await response.json(); } catch (error) { /* old server */ }
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
