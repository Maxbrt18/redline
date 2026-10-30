# Fonts embedded in the Redline cards

These files are **committed on purpose**: `cards/render.py` inlines them in base64 in
every HTML document. That is what guarantees that every machine produces exactly the
same rendering: without them, each machine would resolve a different font, and a text
checked on one could overflow on another.

**Latin** subset only: U+0000–00FF plus general punctuation (— … ' ×, U+2000–206F). A
glyph outside this range silently falls back on the system font: no tofu, but
different metrics.

| File | Family | Role | Source |
|---|---|---|---|
| `Inter-latin.woff2` | Inter (variable, 100–900) | body, tabular figures | [Google Fonts](https://fonts.google.com/specimen/Inter) |
| `BarlowCondensed-SemiBold.woff2` | Barlow Condensed 600 | values, index | [Google Fonts](https://fonts.google.com/specimen/Barlow+Condensed) |
| `BarlowCondensed-Bold.woff2` | Barlow Condensed 700 | titles, countdown | same |

## License

Both families are under the **SIL Open Font License 1.1** (full text in `OFL.txt`):
free redistribution, embedded included.

- Inter — Copyright 2020 The Inter Project Authors (https://github.com/rsms/inter)
- Barlow — Copyright 2017 The Barlow Project Authors (https://github.com/jpt/barlow)

## Replacing a font

Drop the `.woff2` here and update `FONT_FACES` in `cards/render.py`. Nothing else: the
family names exposed to CSS (`Redline Sans`, `Redline Display`) are aliases, so
`theme.css` does not change.
