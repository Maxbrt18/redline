// DOM building blocks shared by the notebook and the strength session.

export const $ = id => document.getElementById(id);

// An element, its text, its class: in that order, on both pages.
export const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};

// Appends the children in order, skipping absent ones (null, false); returns the parent.
export const add = (parent, ...children) => { children.forEach(child => child && parent.append(child)); return parent; };

// A button that never submits a form; the action receives the touched button.
export function button(text, action, cls) {
  const node = el('button', text, cls); node.type = 'button';
  node.addEventListener('click', () => action(node)); return node;
}
