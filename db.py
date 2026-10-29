import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from .models import Base

BASE_DIR = Path(__file__).resolve().parent


def local_now():
    return datetime.now(ZoneInfo(os.environ.get("RENTAL_TIMEZONE", "Asia/Kolkata"))).replace(tzinfo=None, microsecond=0)


def create_database(url):
    options = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 20}
    else:
        options["isolation_level"] = "READ COMMITTED"
    engine = create_engine(url, **options)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=20000")
            cursor.close()
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def default_database_url():
    (BASE_DIR / "instance").mkdir(exist_ok=True)
    return "sqlite:///" + str(BASE_DIR / "instance" / "roam.db")
