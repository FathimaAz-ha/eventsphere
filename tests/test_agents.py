import asyncio
from datetime import datetime, timedelta

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from app.agents.bus import make
from app.agents.registry import build_agents
from app.models import (
    Event, Venue, TicketCategory, Booking, Ticket,
    WaitlistEntry, Notification,
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path):
    """Fresh SQLite DB + fresh MessageBus + fresh agents, per test."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    bus, agents = build_agents(engine)
    yield engine, bus, agents
    engine.dispose()


def _seed(engine, quantity=10, price=10.0, capacity=100, days_ahead=10,
          open_for_hire=True, hourly_rate=50.0):
    with Session(engine) as db:
        v = Venue(name="Test Venue", location="X", capacity=capacity,
                  hourly_rate=hourly_rate, open_for_hire=open_for_hire)
        db.add(v); db.commit(); db.refresh(v)
        e = Event(title="Test Event", category="seminar", venue_id=v.id,
                  starts_at=datetime.utcnow() + timedelta(days=days_ahead),
                  ends_at=datetime.utcnow() + timedelta(days=days_ahead, hours=2),
                  status="published")
        db.add(e); db.commit(); db.refresh(e)
        c = TicketCategory(event_id=e.id, name="Standard",
                           price=price, quantity=quantity)
        db.add(c); db.commit(); db.refresh(c)
        return v.id, e.id, c.id


def _next_weekday(target):
    now = datetime.utcnow()
    days = (target - now.weekday()) % 7 or 7
    return (now + timedelta(days=days)).replace(
        hour=14, minute=0, second=0, microsecond=0)


# ============================================================
# VenueAgent — 4 tests
# ============================================================

def test_venue_agent_refuses_clash(env):
    engine, bus, _ = env
    v_id, e_id, _ = _seed(engine, days_ahead=10)
    with Session(engine) as db:
        e = db.get(Event, e_id)
        start, end = e.starts_at, e.ends_at
    reply = run(bus.send(make(
        "Test", "VenueAgent", "REQUEST",
        {"action": "check_slot", "venue_id": v_id,
         "start": start.isoformat(), "end": end.isoformat(),
         "total_tickets": 5}, "conv-1")))
    assert reply.performative == "REFUSE"
    assert "clash" in reply.content["reason"].lower()


def test_venue_agent_refuses_over_capacity(env):
    engine, bus, _ = env
    v_id, _, _ = _seed(engine, capacity=50, days_ahead=20)
    start = datetime.utcnow() + timedelta(days=30)
    end = start + timedelta(hours=2)
    reply = run(bus.send(make(
        "Test", "VenueAgent", "REQUEST",
        {"action": "check_slot", "venue_id": v_id,
         "start": start.isoformat(), "end": end.isoformat(),
         "total_tickets": 100}, "conv-1")))
    assert reply.performative == "REFUSE"
    assert "capacity" in reply.content["reason"].lower()


def test_venue_agent_prices_weekend_hire(env):
    engine, bus, _ = env
    v_id, _, _ = _seed(engine, capacity=100, open_for_hire=True, hourly_rate=50.0)
    sat = _next_weekday(5)
    sat_end = sat + timedelta(hours=3)
    reply = run(bus.send(make(
        "Test", "VenueAgent", "REQUEST",
        {"action": "hire_request", "venue_id": v_id,
         "start": sat.isoformat(), "end": sat_end.isoformat(),
         "purpose": "party", "guests": 20}, "conv-1")))
    assert reply.performative == "AGREE"
    # 3h × £50 × 1.2 weekend multiplier = £180
    assert abs(reply.content["price"] - 180.0) < 0.01


def test_venue_agent_proposes_alternatives_on_clash(env):
    engine, bus, _ = env
    v_id, e_id, _ = _seed(engine, capacity=100, open_for_hire=True, days_ahead=10)
    with Session(engine) as db:
        existing = db.get(Event, e_id)
        req_start = existing.starts_at + timedelta(hours=1)
    req_end = req_start + timedelta(hours=2)
    reply = run(bus.send(make(
        "Test", "VenueAgent", "REQUEST",
        {"action": "hire_request", "venue_id": v_id,
         "start": req_start.isoformat(), "end": req_end.isoformat(),
         "purpose": "meeting", "guests": 20}, "conv-1")))
    assert reply.performative == "PROPOSE"
    assert len(reply.content["alternatives"]) >= 1


# ============================================================
# BookingAgent — 4 tests
# ============================================================

def test_booking_agent_books_and_issues_tickets(env):
    engine, bus, _ = env
    _, e_id, c_id = _seed(engine, quantity=10, price=10.0)
    reply = run(bus.send(make(
        "Test", "BookingAgent", "REQUEST",
        {"action": "book", "user_id": 1,
         "category_id": c_id, "quantity": 2}, "conv-1")))
    assert reply.performative == "AGREE"
    assert reply.content["payment_ref"].startswith("PAY-")
    assert reply.content["amount"] == 20.0
    assert len(reply.content["tickets"]) == 2
    with Session(engine) as db:
        assert db.get(TicketCategory, c_id).sold == 2
        assert len(db.exec(select(Ticket).where(Ticket.event_id == e_id)).all()) == 2


def test_booking_agent_refuses_over_six(env):
    engine, bus, _ = env
    _, _, c_id = _seed(engine, quantity=20)
    reply = run(bus.send(make(
        "Test", "BookingAgent", "REQUEST",
        {"action": "book", "user_id": 1,
         "category_id": c_id, "quantity": 7}, "conv-1")))
    assert reply.performative == "REFUSE"
    assert "6" in reply.content["reason"]


def test_booking_agent_prevents_overbooking(env):
    """REQUIRED: two simultaneous requests for the last seat → exactly one AGREE."""
    engine, bus, _ = env
    _, _, c_id = _seed(engine, quantity=1)

    async def attempt(uid):
        return await bus.send(make(
            "Test", "BookingAgent", "REQUEST",
            {"action": "book", "user_id": uid,
             "category_id": c_id, "quantity": 1}, f"conv-{uid}"))

    async def both():
        return await asyncio.gather(attempt(1), attempt(2))

    a, b = run(both())
    assert sorted([a.performative, b.performative]) == ["AGREE", "REFUSE"]
    refused = a if a.performative == "REFUSE" else b
    assert "0 tickets left" in refused.content["reason"].lower()


def test_booking_agent_refund_policy_buckets(env):
    """The documented 100 / 50 / 0 policy."""
    from app.agents.booking_agent import refund_ratio
    now = datetime.utcnow()
    assert refund_ratio(now + timedelta(days=8), now) == 1.0
    assert refund_ratio(now + timedelta(days=3), now) == 0.5
    assert refund_ratio(now + timedelta(hours=6), now) == 0.0


# ============================================================
# TicketAgent — 2 tests
# ============================================================

def test_ticket_agent_issues_unique_codes(env):
    engine, bus, _ = env
    _, _, c_id = _seed(engine, quantity=10)
    reply = run(bus.send(make(
        "Test", "BookingAgent", "REQUEST",
        {"action": "book", "user_id": 1,
         "category_id": c_id, "quantity": 3}, "conv-1")))
    codes = reply.content["tickets"]
    assert len(codes) == 3
    assert len(set(codes)) == 3
    assert all(c.startswith("ES-") for c in codes)


def test_ticket_agent_double_check_in_refused(env):
    """REQUIRED: same code scanned twice → second scan REFUSE."""
    engine, bus, _ = env
    _, e_id, c_id = _seed(engine, quantity=10)
    # move the event into the check-in window
    with Session(engine) as db:
        e = db.get(Event, e_id)
        e.starts_at = datetime.utcnow() + timedelta(hours=1)
        e.ends_at = datetime.utcnow() + timedelta(hours=3)
        db.add(e); db.commit()

    book = run(bus.send(make(
        "Test", "BookingAgent", "REQUEST",
        {"action": "book", "user_id": 1,
         "category_id": c_id, "quantity": 1}, "conv-book")))
    code = book.content["tickets"][0]

    first = run(bus.send(make(
        "Test", "TicketAgent", "REQUEST",
        {"action": "check_in", "code": code, "event_id": e_id}, "conv-1")))
    assert first.performative == "AGREE"

    second = run(bus.send(make(
        "Test", "TicketAgent", "REQUEST",
        {"action": "check_in", "code": code, "event_id": e_id}, "conv-2")))
    assert second.performative == "REFUSE"
    assert "already" in second.content["reason"].lower()


# ============================================================
# WaitlistAgent — 1 test (proactive sweep)
# ============================================================

def test_waitlist_agent_expires_holds_and_reoffers(env):
    engine, bus, agents = env
    _, e_id, c_id = _seed(engine, quantity=5)
    with Session(engine) as db:
        w1 = WaitlistEntry(user_id=1, event_id=e_id, category_id=c_id,
                           quantity=1, status="offered",
                           expires_at=datetime.utcnow() - timedelta(minutes=1))
        w2 = WaitlistEntry(user_id=2, event_id=e_id, category_id=c_id,
                           quantity=1, status="waiting")
        db.add_all([w1, w2]); db.commit()
        db.refresh(w1); db.refresh(w2)
        w1_id, w2_id = w1.id, w2.id

    run(agents["WaitlistAgent"].sweep_once())

    with Session(engine) as db:
        r1 = db.get(WaitlistEntry, w1_id)
        r2 = db.get(WaitlistEntry, w2_id)
        assert r1.status == "expired"
        assert r2.status == "offered"
        assert r2.expires_at > datetime.utcnow()


# ============================================================
# NotificationAgent — 1 test
# ============================================================

def test_notification_agent_records_broadcast(env):
    engine, bus, _ = env
    run(bus.send(make(
        "Test", "*", "INFORM",
        {"user_id": 1, "title": "Hi", "body": "Hello"}, "conv-notify")))
    with Session(engine) as db:
        rows = db.exec(select(Notification)).all()
        assert len(rows) == 1
        assert rows[0].user_id == 1
        assert rows[0].title == "Hi"


# ============================================================
# Integration — end-to-end sell-out then REFUSE
# ============================================================

def test_full_pipeline_sells_out_and_refuses(env):
    engine, bus, _ = env
    _, _, c_id = _seed(engine, quantity=2)

    r1 = run(bus.send(make("Test", "BookingAgent", "REQUEST",
        {"action": "book", "user_id": 1,
         "category_id": c_id, "quantity": 2}, "conv-1")))
    assert r1.performative == "AGREE"

    r2 = run(bus.send(make("Test", "BookingAgent", "REQUEST",
        {"action": "book", "user_id": 2,
         "category_id": c_id, "quantity": 1}, "conv-2")))
    assert r2.performative == "REFUSE"
    assert "0 tickets left" in r2.content["reason"].lower()