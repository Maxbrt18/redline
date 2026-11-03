"""How the athlete's days present themselves: weekly habits and days stated ahead."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..schema import day_habits, stated_days
from ._common import StoreBase, _audit, _plain

#: The columns a day state is written in, the same in both tables.
STATE_COLUMNS = ("kind", "minutes", "pace", "required", "sport", "demanding", "run_too")


class Days(StoreBase):
    def habits(self, on: dt.date | None = None, until: dt.date | None = None) -> list[dict]:
        """The habits in force at some point between `on` and `until` (both inclusive);
        every one still in force or to come when `on` alone is given; all of them without."""
        query = sa.select(day_habits).where(day_habits.c.athlete_id == self.athlete_id)
        if on is not None:
            query = query.where(sa.or_(day_habits.c.ends_on.is_(None),
                                       day_habits.c.ends_on >= on))
        if until is not None:
            query = query.where(day_habits.c.starts_on <= until)
        with self.database.engine.connect() as connection:
            rows = connection.execute(query.order_by(
                day_habits.c.weekday, day_habits.c.starts_on, day_habits.c.id)).mappings()
            return [dict(row) for row in rows]

    def add_habit(self, row: dict, *, actor: str = "notebook") -> uuid.UUID:
        habit_id = uuid.uuid4()
        values = {"id": habit_id, "athlete_id": self.athlete_id, "weekday": row["weekday"],
                  "starts_on": row["starts_on"], "ends_on": row.get("ends_on"),
                  **{name: row.get(name) for name in STATE_COLUMNS}}
        with self.database.engine.begin() as connection:
            connection.execute(day_habits.insert().values(**values))
            _audit(connection, actor, "habit.added", "habit", habit_id,
                   {key: _plain(value) for key, value in values.items()})
        return habit_id

    def end_habit(self, habit_id: uuid.UUID, on: dt.date, *, actor: str = "notebook") -> bool:
        """The habit no longer holds after `on`; one that had not started yet is removed."""
        with self.database.engine.begin() as connection:
            row = connection.execute(sa.select(day_habits).where(
                day_habits.c.id == habit_id,
                day_habits.c.athlete_id == self.athlete_id)).mappings().first()
            if row is None:
                return False
            if row["starts_on"] > on:
                connection.execute(day_habits.delete().where(day_habits.c.id == habit_id))
            else:
                connection.execute(day_habits.update().where(day_habits.c.id == habit_id)
                                   .values(ends_on=on))
            _audit(connection, actor, "habit.ended", "habit", habit_id, {"on": on.isoformat()})
        return True

    def stated_days(self, first: dt.date, last: dt.date) -> list[dict]:
        with self.database.engine.connect() as connection:
            rows = connection.execute(sa.select(stated_days).where(
                stated_days.c.athlete_id == self.athlete_id,
                stated_days.c.day.between(first, last)).order_by(stated_days.c.day)).mappings()
            return [dict(row) for row in rows]

    def state_days(self, first: dt.date, last: dt.date, rows: list[dict],
                   *, actor: str = "notebook") -> None:
        """The days stated between `first` and `last`, replacing those stated before."""
        with self.database.engine.begin() as connection:
            connection.execute(stated_days.delete().where(
                stated_days.c.athlete_id == self.athlete_id,
                stated_days.c.day.between(first, last)))
            for row in rows:
                values = {"athlete_id": self.athlete_id, "day": row["day"],
                          **{name: row.get(name) for name in STATE_COLUMNS}}
                connection.execute(pg_insert(stated_days).values(**values))
            _audit(connection, actor, "days.stated", "days",
                   uuid.uuid5(uuid.NAMESPACE_URL, f"days:{self.athlete_id}:{first}"),
                   {"first": first.isoformat(), "last": last.isoformat(),
                    "days": [{key: _plain(value) for key, value in row.items()} for row in rows]})
