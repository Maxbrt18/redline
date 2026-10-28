#!/usr/bin/env python3
"""Recovers the remaining archives without inventing a delivery or recomputing a review.

Run on the host with the service environment: --year <YYYY> --apply.
Without --apply it only takes inventory; the import is idempotent and leaves files alone.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
from pathlib import Path

import sqlalchemy as sa

from redline.service import strength
from redline.service.config import Config, _load_dotenv
from redline.service.notebook import period_day, review_period
from redline.storage import Database
from redline.storage.notebook import NotebookStore
from redline.storage.schema import outbound_deliveries


def recover(store: NotebookStore, root: Path, psid: str, year: int, apply: bool) -> dict:
    counts = {"images": 0, "reviews": 0}
    with store.database.engine.connect() as connection:
        rows = list(connection.execute(sa.select(outbound_deliveries).where(
            outbound_deliveries.c.psid == psid,
            outbound_deliveries.c.status == "delivered",
        )).mappings())
    for row in rows:
        kind, period = review_period(row["source_key"])
        if kind == "other" or not row["payload"]:
            continue
        body = "\n\n".join(str(part["text"]) for part in row["payload"].get("parts", [])
                           if part.get("kind") in {"text", "quick_replies"})
        if not body or store.source_entries(psid, row["source_key"]):
            continue
        counts["reviews"] += 1
        if apply:
            store.save_many(psid, row["source_key"], [{
                "slot": "review", "kind": kind, "title": f"{'Weekly' if kind == 'weekly' else 'Monthly'} · {period}",
                "period": period, "occurred_on": period_day(kind, period, row["created_at"].date()),
                "body": body, "created_at": row["created_at"],
            }])
    for image in sorted((root / "server" / "outbox" / "sent").glob("*")):
        if image.suffix.lower() not in {".png", ".jpg", ".jpeg"} or not image.is_file():
            continue
        data = image.read_bytes()
        if len(data) > 12 * 1024 * 1024:
            raise ValueError(f"image trop volumineuse : {image.name}")
        digest = hashlib.sha256(data).hexdigest()
        source = f"recovered:{image.name}:{digest}"
        if store.source_entries(psid, source):
            continue
        link = strength.read_sidecar(image)
        snapshot = link[1] if link else None
        created = dt.datetime.fromtimestamp(image.stat().st_mtime, dt.UTC)
        kind, period, title = "other", "", image.stem
        if snapshot:
            kind, period, title = "strength", snapshot.get("date", ""), snapshot.get("title", "Renfo")
        else:
            # Frozen legacy boundary: only the names actually inventoried are recognised;
            # no title or review prose is extracted.
            month = re.fullmatch(r"month-(20\d{2}-\d{2})", image.stem)
            week = re.fullmatch(r"(?:bilan-|week-)(?:(20\d{2})-)?[wW](\d{2})", image.stem)
            if month:
                kind, period = "monthly", month[1]
                title = f"Monthly · {period}"
            elif week:
                kind, period = "weekly", f"{week[1] or year}-W{week[2]}"
                title = f"Weekly · {period}"
            elif re.fullmatch(r"strength-20\d{2}-\d{2}-\d{2}", image.stem):
                kind, period, title = "strength", image.stem[9:], "Renfo"
        counts["images"] += 1
        if apply:
            store.save_many(psid, source, [{
                "slot": image.name, "kind": kind, "period": period,
                "title": f"{title} · image récupérée",
                "occurred_on": period_day(kind, period, created.date()), "created_at": created,
                "image": data, "image_hash": digest,
                "image_type": "image/png" if image.suffix.lower() == ".png" else "image/jpeg",
                "snapshot": snapshot, "origin": "recovered",
            }])
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 2020 <= args.year <= 2099:
        parser.error("année d'import invalide")
    _load_dotenv(Path("/etc/redline/runtime/coach-messenger.env"))
    config = Config.from_env()
    database = Database(config.database_url, application_name="redline-notebook-import")
    try:
        print(recover(NotebookStore(database), Path(config.vault_path), config.allowed_psid,
                      args.year, args.apply))
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
