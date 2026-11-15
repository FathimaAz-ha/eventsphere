import os
from datetime import datetime, timedelta

from sqlmodel import SQLModel, Session, create_engine, select

from .models import (
    User, Venue, Event, TicketCategory,
)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:gISofQDfSLNTBmThJTKBUlgCrnJhqkQo@eventsphere-db.railway.internal:5432/railway")

# Force SQLAlchemy to use psycopg2 for Postgres on Railway
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

# THE MISSING LINE WAS HERE:
engine = create_engine(DATABASE_URL, echo=False, connect_args=_connect_args)


def get_session():
    with Session(engine) as s:
        yield s

def init_db():
    SQLModel.metadata.create_all(engine)