// Les briques du DOM, communes au carnet et au renfo.

export const $ = id => document.getElementById(id);

// Un élément, son texte, sa classe : dans cet ordre, sur les deux pages.
export const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};

// Ajoute les enfants dans l'ordre et saute les absents (null, false) ; rend le parent.
export const add = (parent, ...children) => { children.forEach(child => child && parent.append(child)); return parent; };

// Un bouton qui n'envoie jamais de formulaire ; l'action reçoit le bouton touché.
export function button(text, action, cls) {
  const node = el('button', text, cls); node.type = 'button';
  node.addEventListener('click', () => action(node)); return node;
}
