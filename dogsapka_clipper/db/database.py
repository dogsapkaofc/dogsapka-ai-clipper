"""Database engine, sessions, migrations and settings persistence."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from dogsapka_clipper.core.config import AppSettings
from dogsapka_clipper.db.models import AppSetting, Base, SchemaVersion

log = logging.getLogger(__name__)


def _migration_1(conn) -> None:
    Base.metadata.create_all(conn)


def _migration_2(conn) -> None:
    """YouTube live streamers + multiple publish accounts."""
    cols = {c["name"] for c in inspect(conn).get_columns("streamers")}
    for name, ddl in (("platform", "VARCHAR(16) NOT NULL DEFAULT 'twitch'"),
                      ("youtube_channel_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
                      ("youtube_uploads_playlist", "VARCHAR(64) NOT NULL DEFAULT ''"),
                      ("youtube_handle", "VARCHAR(128) NOT NULL DEFAULT ''"),
                      ("live_video_id", "VARCHAR(32) NOT NULL DEFAULT ''")):
        if name not in cols:   # a fresh database already got them from migration 1
            conn.execute(text(f"ALTER TABLE streamers ADD COLUMN {name} {ddl}"))
    Base.metadata.create_all(conn)   # creates publish_accounts


# Append future migrations here; each receives a Connection inside a transaction.
MIGRATIONS: list[Callable] = [_migration_1, _migration_2]
SCHEMA_VERSION = len(MIGRATIONS)


class Database:
    def __init__(self, path: Path | str) -> None:
        url = "sqlite:///:memory:" if str(path) == ":memory:" else f"sqlite:///{Path(path).as_posix()}"
        kw = {}
        if str(path) == ":memory:":
            from sqlalchemy.pool import StaticPool
            kw["poolclass"] = StaticPool
        self.engine: Engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30},
                                            future=True, **kw)
        event.listen(self.engine, "connect", self._on_connect)
        self._sessionmaker = sessionmaker(self.engine, expire_on_commit=False, future=True)
        self.migrate()

    @staticmethod
    def _on_connect(dbapi_conn, _record) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    def current_version(self) -> int:
        if not inspect(self.engine).has_table("schema_version"):
            return 0
        with self.engine.connect() as c:
            v = c.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
        return int(v or 0)

    def migrate(self) -> None:
        cur = self.current_version()
        for idx in range(cur, SCHEMA_VERSION):
            with self.engine.begin() as conn:
                MIGRATIONS[idx](conn)
                if idx == 0:
                    SchemaVersion.__table__.create(conn, checkfirst=True)
                conn.execute(SchemaVersion.__table__.insert().values(version=idx + 1))
            log.info("Database migrated to schema version %d", idx + 1)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._sessionmaker()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # -- settings -----------------------------------------------------
    def load_settings(self) -> AppSettings:
        with self.session() as s:
            row = s.get(AppSetting, "app")
            return AppSettings.from_dict(row.value if row else None)

    def save_settings(self, settings: AppSettings) -> None:
        settings.validate()
        with self.session() as s:
            row = s.get(AppSetting, "app")
            if row is None:
                s.add(AppSetting(key="app", value=settings.to_dict()))
            else:
                row.value = settings.to_dict()

    def count(self, model, *where) -> int:
        with self.session() as s:
            return int(s.scalar(select(func.count()).select_from(model).where(*where)) or 0)

    def close(self) -> None:
        self.engine.dispose()
