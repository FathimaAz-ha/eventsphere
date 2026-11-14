import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import Event, Venue, TicketCategory, User
from ..security import require_role
from ..agents.bus import make

router = APIRouter(prefix="/api/events", tags=["events"])


class TicketCategoryIn(BaseModel):
    name: str
    price: float
    quantity: int


class EventIn(BaseModel):
    title: str
    category: str
    venue_id: int
    starts_at: datetime
    ends_at: datetime
    description: str = ""
    tickets: list[TicketCategoryIn]


def _event_dict(db: Session, e: Event) -> dict:
    cats = db.exec(select(TicketCategory).where(
        TicketCategory.event_id == e.id)).all()
    return {"id": e.id, "title": e.title, "category": e.category,
            "venue_id": e.venue_id,
            "starts_at": e.starts_at.isoformat(),
            "ends_at": e.ends_at.isoformat(),
            "description": e.description, "status": e.status,
            "tickets": [{"id": c.id, "name": c.name, "price": c.price,
                         "quantity": c.quantity, "sold": c.sold,
                         "remaining": c.quantity - c.sold} for c in cats]}


@router.get("")
def list_events(q: str = "", category: str = "",
                db: Session = Depends(get_session)):
    events = db.exec(select(Event).where(Event.status == "published")).all()
    if q:
        ql = q.lower()
        events = [e for e in events
                  if ql in e.title.lower() or ql in e.description.lower()]
    if category:
        events = [e for e in events if e.category == category]
    return [_event_dict(db, e) for e in events]


@router.get("/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_session)):
    e = db.get(Event, event_id)
    if not e:
        raise HTTPException(404, "Event not found")
    return _event_dict(db, e)


async def _venue_slot_check(bus, venue_id, start, end, total, ignore=None):
    reply = await bus.send(make(
        "EventRoute", "VenueAgent", "REQUEST",
        {"action": "check_slot", "venue_id": venue_id,
         "start": start.isoformat(), "end": end.isoformat(),
         "total_tickets": total, "ignore_event_id": ignore},
        uuid.uuid4().hex,
    ))
    if not reply:
        raise HTTPException(500, "VenueAgent did not reply")
    if reply.performative != "AGREE":
        raise HTTPException(400, reply.content.get("reason", "Refused by VenueAgent"))


@router.post("")
async def create_event(body: EventIn, request: Request,
                       admin: User = Depends(require_role("admin")),
                       db: Session = Depends(get_session)):
    venue = db.get(Venue, body.venue_id)
    if not venue:
        raise HTTPException(404, "Venue not found")
    total = sum(t.quantity for t in body.tickets)
    if total > venue.capacity:
        raise HTTPException(400,
            f"Total tickets ({total}) exceed venue capacity ({venue.capacity})")

    await _venue_slot_check(request.app.state.bus, body.venue_id,
                            body.starts_at, body.ends_at, total)

    e = Event(title=body.title, category=body.category,
              venue_id=body.venue_id,
              starts_at=body.starts_at, ends_at=body.ends_at,
              description=body.description, status="draft")
    db.add(e); db.commit(); db.refresh(e)
    for t in body.tickets:
        db.add(TicketCategory(event_id=e.id, name=t.name,
                              price=t.price, quantity=t.quantity))
    db.commit()
    return _event_dict(db, e)


@router.post("/{event_id}/publish")
async def publish_event(event_id: int, request: Request,
                        admin: User = Depends(require_role("admin")),
                        db: Session = Depends(get_session)):
    e = db.get(Event, event_id)
    if not e:
        raise HTTPException(404, "Event not found")
    if e.status == "published":
        return {"ok": True, "status": "already published"}
    total = sum(c.quantity for c in db.exec(select(TicketCategory)
                .where(TicketCategory.event_id == e.id)).all())
    await _venue_slot_check(request.app.state.bus, e.venue_id,
                            e.starts_at, e.ends_at, total, ignore=e.id)
    e.status = "published"
    db.add(e); db.commit()
    return {"ok": True, "status": "published"}


@router.post("/{event_id}/cancel")
async def cancel_event(event_id: int, request: Request,
                       admin: User = Depends(require_role("admin")),
                       db: Session = Depends(get_session)):
    e = db.get(Event, event_id)
    if not e:
        raise HTTPException(404, "Event not found")
    e.status = "cancelled"
    db.add(e); db.commit()
    reply = await request.app.state.bus.send(make(
        "EventRoute", "BookingAgent", "REQUEST",
        {"action": "cancel_event", "event_id": e.id},
        uuid.uuid4().hex,
    ))
    refunded = reply.content.get("refunded_bookings", 0) if reply else 0
    return {"ok": True, "refunded_bookings": refunded}