# Polices embarquées dans les cards Redline

Ces fichiers sont **commités volontairement** : `cards/render.py` les inline en base64
dans chaque document HTML. C'est ce qui garantit que le Mac et le VPS produisent
exactement le même rendu — sans eux, chaque machine résoudrait une police différente
et un texte validé ici pourrait déborder là-bas.

Sous-ensemble **latin** uniquement (le contenu est en français) : U+0000–00FF plus la
ponctuation générale (— … ' ×, U+2000–206F). Un glyphe hors de cette plage retombe
silencieusement sur la police système : pas de tofu, mais des métriques différentes.

| Fichier | Famille | Rôle | Source |
|---|---|---|---|
| `Inter-latin.woff2` | Inter (variable, 100–900) | corps, chiffres tabulaires | [Google Fonts](https://fonts.google.com/specimen/Inter) |
| `BarlowCondensed-SemiBold.woff2` | Barlow Condensed 600 | valeurs, index | [Google Fonts](https://fonts.google.com/specimen/Barlow+Condensed) |
| `BarlowCondensed-Bold.woff2` | Barlow Condensed 700 | titres, compte à rebours | idem |

## Licence

Les deux familles sont sous **SIL Open Font License 1.1** (texte complet dans
`OFL.txt`) — redistribution libre, y compris embarquée.

- Inter — Copyright 2020 The Inter Project Authors (https://github.com/rsms/inter)
- Barlow — Copyright 2017 The Barlow Project Authors (https://github.com/jpt/barlow)

## Remplacer une police

Déposer le `.woff2` ici et mettre à jour `FONT_FACES` dans `cards/render.py`. Rien
d'autre : les noms de famille exposés au CSS (`Redline Sans`, `Redline Display`) sont
des alias, `theme.css` n'a pas à bouger.
