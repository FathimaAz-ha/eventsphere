import secrets
from datetime import datetime, timedelta

from sqlmodel import Session, select
from sqlalchemy import text

from .bus import Message, MessageBus, make
from ..models import Booking, Event, TicketCategory, Ticket

MAX_TICKETS_PER_BOOKING = 6


def _now() -> datetime:
    return datetime.utcnow()


def refund_ratio(starts_at: datetime, when: datetime) -> float:
    """Documented refund policy. Report this table verbatim."""
    delta = starts_at - when
    if delta >= timedelta(days=7):
        return 1.0
    if delta >= timedelta(days=2):
        return 0.5
    return 0.0


class BookingAgent:
    name = "BookingAgent"
    goal = "Sell tickets fairly without overselling"

    def __init__(self, bus: MessageBus, engine):
        self.bus = bus
        self.engine = engine
        bus.subscribe(self.name, self.handle)

    # ---------- entry ----------
    async def handle(self, msg: Message):
        action = msg.content.get("action")
        try:
            if action == "book":
                return await self._book(msg)
            if action == "cancel":
                return await self._cancel(msg)
            if action == "cancel_event":
                return await self._cancel_event(msg)
        except Exception as e:
            return make(self.name, msg.sender, "FAILURE",
                        {"error": f"{type(e).__name__}: {e}"}, msg.conversation_id)
        return self._refuse(msg, f"Unknown action '{action}'")

    # ---------- helpers ----------
    def _refuse(self, msg, reason):
        return make(self.name, msg.sender, "REFUSE",
                    {"reason": reason}, msg.conversation_id)

    async def _notify(self, user_id, title, body, conv_id):
        # Broadcast — NotificationAgent is subscribed to "*"
        await self.bus.send(make(
            self.name, "*", "INFORM",
            {"user_id": user_id, "title": title, "body": body},
            conv_id,
        ))

    # ---------- REQUEST: book N tickets ----------
    async def _book(self, msg: Message):
        c = msg.content
        qty = int(c["quantity"])
        cat_id = int(c["category_id"])
        user_id = int(c["user_id"])

        if qty < 1:
            return self._refuse(msg, "Quantity must be at least 1")
        if qty > MAX_TICKETS_PER_BOOKING:
            return self._refuse(
                msg, f"Limit is {MAX_TICKETS_PER_BOOKING} tickets per booking")

        with Session(self.engine) as db:
            cat = db.get(TicketCategory, cat_id)
            if not cat:
                return self._refuse(msg, "Ticket category not found")
            event = db.get(Event, cat.event_id)
            if not event or event.status != "published":
                return self._refuse(msg, "Event is not on sale")
            if event.starts_at <= _now():
                return self._refuse(msg, "Event has already started")

            # ATOMIC seat allocation — the whole anti-overbooking story
            res = db.execute(
                text("UPDATE ticketcategory SET sold = sold + :q "
                     "WHERE id = :id AND sold + :q <= quantity"),
                {"q": qty, "id": cat_id},
            )
            if res.rowcount == 0:
                db.rollback()
                # sold out → hand the user to the waitlist
                await self.bus.send(make(
                    self.name, "WaitlistAgent", "INFORM",
                    {"action": "freed_hint",
                     "event_id": event.id, "category_id": cat_id},
                    msg.conversation_id,
                ))
                return self._refuse(
                    msg, f"Only {cat.quantity - cat.sold} tickets left "
                         f"in '{cat.name}' — added to waitlist? (offer)")

            amount = round(qty * cat.price, 2)
            ref = "PAY-" + secrets.token_hex(6).upper()
            booking = Booking(
                user_id=user_id, event_id=event.id, category_id=cat_id,
                quantity=qty, amount=amount, payment_ref=ref,
            )
            db.add(booking)
            db.commit()
            db.refresh(booking)

        # ask TicketAgent to mint the QR codes
        ticket_reply = await self.bus.send(make(
            self.name, "TicketAgent", "REQUEST",
            {"action": "issue", "booking_id": booking.id,
             "event_id": event.id, "quantity": qty},
            msg.conversation_id,
        ))
        codes = (ticket_reply.content or {}).get("codes", []) if ticket_reply else []

        await self._notify(
            user_id,
            "Booking confirmed",
            f"{qty} × {cat.name} for '{event.title}'. "
            f"Paid £{amount:.2f} (ref {ref}). {len(codes)} ticket(s) issued.",
            msg.conversation_id,
        )

        return make(self.name, msg.sender, "AGREE", {
            "booking_id": booking.id,
            "payment_ref": ref,
            "amount": amount,
            "tickets": codes,
        }, msg.conversation_id)

    # ---------- REQUEST: cancel one booking ----------
    async def _cancel(self, msg: Message):
        booking_id = int(msg.content["booking_id"])
        with Session(self.engine) as db:
            booking = db.get(Booking, booking_id)
            if not booking:
                return self._refuse(msg, "Booking not found")
            if booking.status == "cancelled":
                return self._refuse(msg, "Booking already cancelled")

            event = db.get(Event, booking.event_id)
            ratio = refund_ratio(event.starts_at, _now())
            refund = round(booking.amount * ratio, 2)

            booking.status = "cancelled"
            booking.refund_amount = refund
            db.add(booking)

            # give the seats back — no conditional update needed, we own them
            db.execute(
                text("UPDATE ticketcategory SET sold = sold - :q "
                     "WHERE id = :id"),
                {"q": booking.quantity, "id": booking.category_id},
            )
            # cancel the individual tickets so scans are refused
            for t in db.exec(select(Ticket).where(
                    Ticket.booking_id == booking.id)).all():
                t.status = "cancelled"
                db.add(t)
            db.commit()

            event_id = booking.event_id
            cat_id = booking.category_id
            user_id = booking.user_id
            qty = booking.quantity
            amount = booking.amount

        # tell WaitlistAgent there are now `qty` seats free
        await self.bus.send(make(
            self.name, "WaitlistAgent", "INFORM",
            {"action": "freed", "event_id": event_id,
             "category_id": cat_id, "quantity": qty},
            msg.conversation_id,
        ))
        await self._notify(
            user_id, "Booking cancelled",
            f"Refund £{refund:.2f} of £{amount:.2f} "
            f"({int(ratio * 100)}% policy).",
            msg.conversation_id,
        )
        return make(self.name, msg.sender, "AGREE",
                    {"refund": refund, "ratio": ratio}, msg.conversation_id)

    # ---------- REQUEST: event cancelled by admin → full refunds ----------
    async def _cancel_event(self, msg: Message):
        event_id = int(msg.content["event_id"])
        with Session(self.engine) as db:
            bookings = db.exec(select(Booking).where(
                Booking.event_id == event_id,
                Booking.status == "confirmed")).all()
            for b in bookings:
                b.status = "cancelled"
                b.refund_amount = b.amount          # 100%
                db.add(b)
                for t in db.exec(select(Ticket).where(
                        Ticket.booking_id == b.id)).all():
                    t.status = "cancelled"
                    db.add(t)
            db.commit()
            affected = [(b.user_id, b.amount) for b in bookings]

        for user_id, amount in affected:
            await self._notify(
                user_id, "Event cancelled",
                f"Full refund of £{amount:.2f} issued.", msg.conversation_id)

        return make(self.name, msg.sender, "AGREE",
                    {"refunded_bookings": len(affected)}, msg.conversation_id)