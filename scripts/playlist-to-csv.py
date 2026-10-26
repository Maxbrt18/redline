#!/usr/bin/env python3
"""Normalise un export du site export-youtube-playlist.vercel.app en CSV canonique.

Le site (YouTube Playlist Link Extractor) exporte en TXT, CSV, Markdown, etc.
Ce script accepte n'importe lequel de ces fichiers (ou un copier-coller brut),
en extrait les vidéos et écrit un CSV : position,video_id,url,title

Usage :
    python3 scripts/playlist-to-csv.py sources/export-runwise.txt
    python3 scripts/playlist-to-csv.py sources/export-runwise.csv -o sources/playlist.csv

Sans -o, écrit à côté de l'entrée : <entrée>.normalized.csv
Stdlib uniquement, aucune dépendance.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?[^\s\"']*v=|shorts/|embed/|live/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
)
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def clean_title(title: str) -> str:
    title = title.strip().strip('"').strip()
    # Le site préfixe parfois par un numéro d'ordre ("1. Titre")
    title = re.sub(r"^\d+[.)]\s*", "", title)
    # Séparateurs résiduels (exports TXT : "url | titre")
    return title.strip("|•–—- \t")


def extract_from_markdown(text: str):
    """Paires (titre, url) depuis un export Markdown [titre](url)."""
    for title, url in MD_LINK_RE.findall(text):
        m = VIDEO_ID_RE.search(url)
        if m:
            yield m.group(1), url, clean_title(title)


def extract_from_csv(path: Path):
    """Export CSV du site : détecte les colonnes URL et titre, peu importe leur nom/ordre."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        for row in csv.reader(f, dialect):
            found = next(((c, m) for c in row if (m := VIDEO_ID_RE.search(c))), None)
            if found is None:
                continue  # ligne d'en-tête ou vide
            url_cell, m = found
            # Titre = la cellule non-URL la plus longue qui ne ressemble pas à une durée/un nombre
            candidates = [
                c.strip() for c in row
                if c.strip() and c is not url_cell
                and not VIDEO_ID_RE.search(c)
                and not re.fullmatch(r"[\d:.,\s]+", c.strip())
            ]
            title = clean_title(max(candidates, key=len)) if candidates else ""
            yield m.group(1), url_cell.strip(), title


def extract_from_text(text: str):
    """TXT / copier-coller : une URL par ligne, titre éventuel sur la même ligne."""
    for line in text.splitlines():
        m = VIDEO_ID_RE.search(line)
        if not m:
            continue
        url_m = re.search(r"https?://\S+", line)
        url = url_m.group(0).rstrip(".,;") if url_m else f"https://www.youtube.com/watch?v={m.group(1)}"
        remainder = line.replace(url_m.group(0), "") if url_m else line
        title = clean_title(re.sub(r"[|\t•–-]+$|^[|\t•–-]+", "", remainder.strip()))
        yield m.group(1), url, title


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="fichier exporté depuis le site (txt/csv/md)")
    ap.add_argument("-o", "--output", type=Path, default=None, help="CSV de sortie")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"Fichier introuvable : {args.input}", file=sys.stderr)
        return 1

    text = args.input.read_text(encoding="utf-8-sig", errors="replace")
    suffix = args.input.suffix.lower()

    if suffix == ".csv":
        rows = list(extract_from_csv(args.input))
    elif suffix in (".md", ".markdown"):
        rows = list(extract_from_markdown(text))
    else:
        rows = list(extract_from_text(text))
        if not rows:
            rows = list(extract_from_markdown(text))

    # Déduplication en conservant l'ordre de la playlist
    seen, unique = set(), []
    for vid, url, title in rows:
        if vid not in seen:
            seen.add(vid)
            unique.append((vid, url, title))

    if not unique:
        print("Aucune vidéo YouTube trouvée dans ce fichier.", file=sys.stderr)
        return 1

    out = args.output or args.input.with_suffix(".normalized.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["position", "video_id", "url", "title"])
        for i, (vid, _url, title) in enumerate(unique, 1):
            w.writerow([i, vid, f"https://www.youtube.com/watch?v={vid}", title])

    n_titled = sum(1 for _, _, t in unique if t)
    print(f"✓ {len(unique)} vidéos → {out} ({n_titled} avec titre)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
