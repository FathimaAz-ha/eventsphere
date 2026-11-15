import os
from datetime import datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sqlalchemy import text
from sqlmodel import SQLModel, Session, create_engine, select

from .models import (
    User, Venue, Event, TicketCategory,
)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./eventsphere.db")

# Force SQLAlchemy to use psycopg2 for Postgres on Railway if Postgres URL is provided
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+psycopg2" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, echo=False, connect_args=_connect_args)


def get_session():
    with Session(engine) as s:
        yield s


def init_db():
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        for col_name, col_type in [
            ("original_price", "FLOAT DEFAULT 0.0"),
            ("savings", "FLOAT DEFAULT 0.0"),
            ("negotiation_rounds", "INTEGER DEFAULT 1"),
            ("concession_note", "VARCHAR DEFAULT ''"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE hirerequest ADD COLUMN {col_name} {col_type}"))
                conn.commit()
            except Exception:
                pass


def seed():
    """Idempotent demo data for the viva. Safe to call on every startup."""
    from .security import hash_password   # avoid circular import

    with Session(engine) as db:
        if db.exec(select(User).where(User.email == "admin@eventsphere.local")).first():
            return

        admin = User(email="admin@eventsphere.local", name="Admin",
                     password_hash=hash_password("admin123"), role="admin")
        staff = User(email="staff@eventsphere.local", name="Gate Staff",
                     password_hash=hash_password("staff123"), role="staff")
        demo = User(email="demo@eventsphere.local", name="Demo Attendee",
                    password_hash=hash_password("demo1234"), role="attendee")
        db.add_all([admin, staff, demo]); db.commit()

        v1 = Venue(name="Grand Hall", location="Building A", capacity=200,
                   description="Main concert hall",
                   hourly_rate=80.0, open_for_hire=True)
        v2 = Venue(name="Seminar Room B", location="Building C", capacity=60,
                   description="Workshop room",
                   hourly_rate=40.0, open_for_hire=True)
        db.add_all([v1, v2]); db.commit()
        db.refresh(v1); db.refresh(v2)

        now = datetime.utcnow()
        e1 = Event(title="Demo Concert", category="concert", venue_id=v1.id,
                   starts_at=now + timedelta(days=10),
                   ends_at=now + timedelta(days=10, hours=3),
                   description="Seeded demo event", status="published")
        e2 = Event(title="Workshop: Agent Systems", category="workshop",
                   venue_id=v2.id,
                   starts_at=now + timedelta(days=14),
                   ends_at=now + timedelta(days=14, hours=2),
                   description="Seeded demo workshop", status="published")
        db.add_all([e1, e2]); db.commit()
        db.refresh(e1); db.refresh(e2)

        db.add_all([
            TicketCategory(event_id=e1.id, name="Standard", price=15.0, quantity=150),
            TicketCategory(event_id=e1.id, name="VIP",      price=30.0, quantity=50),
            TicketCategory(event_id=e2.id, name="Standard", price=10.0, quantity=60),
        ])
        db.commit()