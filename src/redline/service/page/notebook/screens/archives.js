import {add, button, el} from '../../shared/dom.js';
import {app} from '../state.js';
import {chevron, content, message} from '../ui.js';
import {api} from '../api.js';
import {capital, dm, humanDate} from '../format.js';
import {cardBlob, openViewer, shareCard, shareable} from '../viewer.js';
import {push, render} from '../nav.js';

// ------------------------------------------------------------ archives and cards
export function renderArchive(data) {
  let month = '';
  const ordered = [...data.entries].sort((a, b) => b.occurred_on.localeCompare(a.occurred_on));
  ordered.forEach(entry => {
    const key = entry.occurred_on.slice(0, 7);
    if (key !== month) { content.append(el('p', capital(new Date(key + '-01T12:00:00').toLocaleDateString('fr-FR', {month: 'long', year: 'numeric'})), 'n-kicker')); month = key; }
    content.append(add(button('', () => push({k: 'entry', id: entry.id, title: entry.title}), 'n-list'), el('span', entry.title), add(el('small', dm(entry.occurred_on)), chevron())));
  });
  if (data.next_offset != null) {
    content.append(button('Voir plus d’archives', async node => {
      node.disabled = true;
      try {
        const more = await api('entries?offset=' + data.next_offset);
        data.entries.push(...more.entries); data.next_offset = more.next_offset;
        app.signatures.archive = JSON.stringify(data); render();
      } catch (error) { message(error.message); node.disabled = false; }
    }, 'n-more'));
  }
}
export function renderEntry(item) {
  const id = item.id;
  if (item.image_hash) {
    const card = {src: '/notebook-api/entries/' + encodeURIComponent(id) + '/image', name: 'redline-' + id + '.png', alt: item.title};
    const open = button('', () => openViewer(card), 'n-imagebtn');
    open.setAttribute('aria-label', 'Agrandir la carte');
    const image = el('img', null, 'n-image'); image.src = card.src; image.alt = item.title;
    image.addEventListener('error', () => message('Image indisponible. Actualise le carnet pour réessayer.'));
    content.append(add(open, image), button(shareable ? 'Partager la carte' : 'Télécharger la carte', () => shareCard(card), 'n-action n-share'));
    cardBlob(card.src).promise.catch(() => {});
  }
  if (item.body) content.append(el('h2', 'Le mot du coach'), el('p', item.body, 'n-prose'));
  if (item.snapshot) {
    const prescription = add(el('details'), el('summary', 'Consignes de cette séance'));
    const list = el('ol', null, 'n-exercises');
    for (const exercise of item.snapshot.exercises || []) list.append(add(el('li', exercise.name || exercise.slug), el('span', (exercise.dose || {}).text || '', 'n-dose')));
    prescription.append(list); content.append(prescription);
  }
  if (item.report) {
    const report = item.report;
    content.append(el('h2', 'Ce que tu as réalisé'));
    content.append(el('p', humanDate(report.performed_on) + (report.duration_minutes ? ' · ' + report.duration_minutes + ' min' : '')));
    if (report.note) content.append(el('p', report.note, 'n-prose'));
    const states = {completed: 'fait', partial: 'partiel', skipped: 'passé', gated: 'retenu', unknown: 'non précisé'};
    for (const exercise of report.exercises || []) {
      const name = item.snapshot?.exercises?.find(e => e.slug === exercise.slug)?.name || exercise.slug;
      const reps = (exercise.sets || []).map(s => (s.side === 'left' ? 'G ' : s.side === 'right' ? 'D ' : '') + s.repetitions).join(' · ');
      content.append(el('p', name + ' — ' + (states[exercise.status] || 'non précisé') + (reps ? ' · ' + reps + ' rép.' : '') + (exercise.note ? '\n' + exercise.note : ''), 'n-prose'));
    }
  } else if (item.kind === 'strength') content.append(el('p', 'Aucun compte rendu rattaché à cette carte.', 'n-meta'));
  if (item.kind === 'monthly' && (item.related || []).length) {
    content.append(el('h2', 'Les weekly de ce mois'));
    item.related.forEach(entry => content.append(add(button('', () => push({k: 'entry', id: entry.id, title: entry.title}), 'n-list'), el('span', entry.title), chevron())));
  }
}
