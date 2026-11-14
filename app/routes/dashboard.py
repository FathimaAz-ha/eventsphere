from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..db import get_session
from ..models import (
    Event, TicketCategory, Booking, Ticket, MessageLog, User,
)
from ..security import require_role

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(admin: User = Depends(require_role("admin")),
              db: Session = Depends(get_session)):
    events = db.exec(select(Event)).all()
    out = []
    for e in events:
        cats = db.exec(select(TicketCategory)
                       .where(TicketCategory.event_id == e.id)).all()
        revenue = sum(b.amount for b in db.exec(select(Booking).where(
            Booking.event_id == e.id,
            Booking.status == "confirmed")).all())
        checked = len(db.exec(select(Ticket).where(
            Ticket.event_id == e.id, Ticket.status == "used")).all())
        out.append({"event_id": e.id, "title": e.title, "status": e.status,
                    "starts_at": e.starts_at.isoformat(),
                    "sold": sum(c.sold for c in cats),
                    "capacity": sum(c.quantity for c in cats),
                    "revenue": round(revenue, 2),
                    "check_ins": checked})
    return out


@router.get("/messages")
def messages(limit: int = 200,
             admin: User = Depends(require_role("admin")),
             db: Session = Depends(get_session)):
    rows = db.exec(select(MessageLog).order_by(MessageLog.id.desc())
                   .limit(limit)).all()
    return [{"id": m.id, "conversation_id": m.conversation_id,
             "sender": m.sender, "receiver": m.receiver,
             "performative": m.performative, "content": m.content,
             "created_at": m.created_at.isoformat()} for m in rows]