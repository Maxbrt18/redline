// Un réglage gardé sur cet appareil. Sans stockage (navigation privée, appareil plein),
// la lecture rend null et l'écriture ne fait rien : la page marche sans.
export function remembered(key, value) {
  try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); }
  catch { return null; }
  return null;
}
