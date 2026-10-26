#!/usr/bin/env python3
"""Récupère les transcripts YouTube des vidéos d'un CSV et les stocke bruts dans inbox/.

Entrée  : CSV produit par playlist-to-csv.py (colonnes position,video_id,url,title)
Sortie  : inbox/<video_id>__<slug-du-titre>.txt  (brut, horodaté, jamais réédité)
          sources/transcripts_failed.csv          (vidéos sans transcript)

Idempotent : une vidéo déjà présente dans inbox/ est sautée. Relancer le script
après un nouvel export de playlist ne retélécharge que les nouveautés.

Dépendance : pip install youtube-transcript-api

Usage :
    python3 scripts/fetch-transcripts.py sources/playlist.csv
    python3 scripts/fetch-transcripts.py sources/playlist.csv --inbox inbox --sleep 2
"""

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import date
from pathlib import Path

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
    )
except ImportError:
    print("Dépendance manquante : pip install youtube-transcript-api", file=sys.stderr)
    sys.exit(1)

# Langues par ordre de préférence (RunWise est francophone)
LANGUAGES = ["fr", "fr-FR", "fr-CA", "en"]


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].rstrip("-") or "sans-titre"


def hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fetch_title(video_id: str) -> str:
    """Titre via l'endpoint oEmbed public (pas de clé API). Vide si échec."""
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.load(r).get("title", "") or ""
    except Exception:
        return ""


def fetch_transcript(video_id: str):
    """Retourne (segments, code_langue, est_généré). Préfère le français, sinon n'importe quoi."""
    api = YouTubeTranscriptApi()
    transcript_list = api.list(video_id)
    try:
        transcript = transcript_list.find_transcript(LANGUAGES)
    except NoTranscriptFound:
        transcript = next(iter(transcript_list))  # première langue disponible
    fetched = transcript.fetch()
    segments = [(snip.start, snip.text) for snip in fetched]
    return segments, transcript.language_code, transcript.is_generated


def write_raw(path: Path, video_id: str, url: str, title: str, lang: str, generated: bool, segments):
    lines = [
        "# --- transcript brut — ne jamais éditer ---",
        f"# video_id: {video_id}",
        f"# url: {url}",
        f"# title: {title}",
        f"# language: {lang} ({'auto-généré' if generated else 'manuel'})",
        f"# fetched: {date.today().isoformat()}",
        "# ---",
        "",
    ]
    for start, text in segments:
        text = " ".join(text.split())  # les sous-titres auto contiennent des \n internes
        if text:
            lines.append(f"[{hms(start)}] {text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_file", type=Path, help="CSV de playlist (playlist-to-csv.py)")
    ap.add_argument("--inbox", type=Path, default=Path("inbox"), help="dossier de sortie (défaut: inbox)")
    ap.add_argument("--sleep", type=float, default=1.5, help="pause entre requêtes en secondes (défaut: 1.5)")
    ap.add_argument("--limit", type=int, default=None, help="ne traiter que N vidéos (pour tester)")
    args = ap.parse_args()

    with args.csv_file.open(newline="", encoding="utf-8-sig") as f:
        videos = list(csv.DictReader(f))
    if args.limit:
        videos = videos[: args.limit]

    args.inbox.mkdir(parents=True, exist_ok=True)
    # Les IDs YouTube font toujours 11 caractères — plus robuste qu'un split sur "__"
    existing_ids = {p.name[:11] for p in args.inbox.glob("*.txt")}

    done = skipped = 0
    failures = []
    consecutive_blocks = 0

    for i, row in enumerate(videos, 1):
        vid = row["video_id"].strip()
        title = (row.get("title") or "").strip()
        url = row.get("url") or f"https://www.youtube.com/watch?v={vid}"

        if vid in existing_ids:
            skipped += 1
            continue

        if not title:
            title = fetch_title(vid)

        label = title or vid
        print(f"[{i}/{len(videos)}] {label[:70]} … ", end="", flush=True)
        try:
            segments, lang, generated = fetch_transcript(vid)
        except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable) as e:
            print(f"✗ {type(e).__name__}")
            failures.append((vid, url, title, type(e).__name__))
            continue
        except Exception as e:  # erreurs réseau, rate-limit, blocage IP, etc.
            err_name = type(e).__name__
            first_line = (str(e).strip().splitlines() or [""])[0]
            print(f"✗ {err_name}: {first_line}")
            failures.append((vid, url, title, f"{err_name}: {first_line}"))
            if err_name in ("IpBlocked", "RequestBlocked"):
                consecutive_blocks += 1
                if consecutive_blocks >= 3:
                    print("\n⛔ YouTube bloque l'IP — arrêt anticipé. Relancer plus tard "
                          "(idempotent : seules les vidéos manquantes seront retentées).")
                    break
                time.sleep(args.sleep * 8)  # backoff avant de retenter
            continue
        consecutive_blocks = 0

        out = args.inbox / f"{vid}__{slugify(title)}.txt"
        write_raw(out, vid, url, title, lang, generated, segments)
        print(f"✓ {lang}{' (auto)' if generated else ''}, {len(segments)} segments")
        done += 1
        time.sleep(args.sleep)

    if failures:
        fail_path = args.csv_file.parent / "transcripts_failed.csv"
        with fail_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["video_id", "url", "title", "error"])
            w.writerows(failures)
        print(f"\n⚠ {len(failures)} échec(s) → {fail_path}")

    print(f"\nBilan : {done} récupéré(s), {skipped} déjà en inbox, {len(failures)} échec(s).")
    return 0 if not failures or done > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
