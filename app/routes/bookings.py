import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import (
    Booking, Event, TicketCategory, WaitlistEntry, User,
)
from ..security import require_user
from ..agents.bus import make

router = APIRouter(prefix="/api", tags=["bookings"])


class BookIn(BaseModel):
    category_id: int
    quantity: int


class WaitlistIn(BaseModel):
    category_id: int
    quantity: int


@router.post("/bookings")
async def create_booking(body: BookIn, request: Request,
                         user: User = Depends(require_user),
                         db: Session = Depends(get_session)):
    conv = uuid.uuid4().hex
    reply = await request.app.state.bus.send(make(
        "BookingRoute", "BookingAgent", "REQUEST",
        {"action": "book", "user_id": user.id,
         "category_id": body.category_id, "quantity": body.quantity},
        conv,
    ))
    if not reply:
        raise HTTPException(500, "No reply from BookingAgent")
    if reply.performative != "AGREE":
        raise HTTPException(400, reply.content.get("reason", "Booking refused"))
    return {"ok": True, "conversation_id": conv, **reply.content}


@router.get("/bookings/mine")
def my_bookings(user: User = Depends(require_user),
                db: Session = Depends(get_session)):
    rows = db.exec(select(Booking).where(Booking.user_id == user.id)
                   .order_by(Booking.id.desc())).all()
    out = []
    for b in rows:
        e = db.get(Event, b.event_id)
        c = db.get(TicketCategory, b.category_id)
        out.append({"id": b.id, "event_id": b.event_id,
                    "event_title": e.title if e else "?",
                    "category_name": c.name if c else "?",
                    "quantity": b.quantity, "amount": b.amount,
                    "payment_ref": b.payment_ref, "status": b.status,
                    "refund_amount": b.refund_amount,
                    "created_at": b.created_at.isoformat()})
    return out


@router.post("/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: int, request: Request,
                         user: User = Depends(require_user),
                         db: Session = Depends(get_session)):
    b = db.get(Booking, booking_id)
    if not b:
        raise HTTPException(404, "Booking not found")
    if b.user_id != user.id:
        raise HTTPException(403, "Not your booking")
    conv = uuid.uuid4().hex
    reply = await request.app.state.bus.send(make(
        "BookingRoute", "BookingAgent", "REQUEST",
        {"action": "cancel", "booking_id": booking_id}, conv,
    ))
    if not reply or reply.performative != "AGREE":
        raise HTTPException(400,
            reply.content.get("reason", "Cancel refused") if reply else "No reply")
    return {"ok": True, "conversation_id": conv, **reply.content}


@router.post("/waitlist")
def join_waitlist(body: WaitlistIn,
                  user: User = Depends(require_user),
                  db: Session = Depends(get_session)):
    c = db.get(TicketCategory, body.category_id)
    if not c:
        raise HTTPException(404, "Category not found")
    entry = WaitlistEntry(user_id=user.id, event_id=c.event_id,
                          category_id=c.id, quantity=body.quantity)
    db.add(entry); db.commit(); db.refresh(entry)
    return {"ok": True, "waitlist_id": entry.id}


@router.get("/waitlist/mine")
def my_waitlist(user: User = Depends(require_user),
                db: Session = Depends(get_session)):
    rows = db.exec(select(WaitlistEntry).where(WaitlistEntry.user_id == user.id)
                   .order_by(WaitlistEntry.id.desc())).all()
    return [{"id": w.id, "event_id": w.event_id,
             "category_id": w.category_id, "quantity": w.quantity,
             "status": w.status, "cycles": w.cycles,
             "expires_at": w.expires_at.isoformat() if w.expires_at else None}
            for w in rows]