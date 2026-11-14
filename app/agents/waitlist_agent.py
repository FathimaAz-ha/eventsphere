import asyncio
from datetime import datetime, timedelta

from sqlmodel import Session, select

from .bus import Message, MessageBus, make
from ..models import WaitlistEntry, TicketCategory

OFFER_TTL = timedelta(minutes=10)
MAX_CYCLES = 3
SWEEP_INTERVAL_SECONDS = 60


def _now() -> datetime:
    return datetime.utcnow()


class WaitlistAgent:
    name = "WaitlistAgent"
    goal = "Pass freed seats to waiting customers"
    # ↑ proactive: runs sweep_once() on a timer with no user action

    def __init__(self, bus: MessageBus, engine):
        self.bus = bus
        self.engine = engine
        bus.subscribe(self.name, self.handle)
        self._task: asyncio.Task | None = None

    # ---------- entry ----------
    async def handle(self, msg: Message):
        action = msg.content.get("action")
        if action == "add":
            return self._add(msg)
        if action == "freed":
            return await self._on_freed(msg)
        # "freed_hint" from BookingAgent (sell-out) is advisory only
        return None

    # ---------- REQUEST: join waitlist ----------
    def _add(self, msg: Message):
        c = msg.content
        with Session(self.engine) as db:
            entry = WaitlistEntry(
                user_id=int(c["user_id"]),
                event_id=int(c["event_id"]),
                category_id=int(c["category_id"]),
                quantity=int(c["quantity"]),
            )
            db.add(entry)
            db.commit()
            db.refresh(entry)
            n = entry.id
        return make(self.name, msg.sender, "AGREE",
                    {"waitlist_id": n}, msg.conversation_id)

    # ---------- INFORM: seats were freed ----------
    async def _on_freed(self, msg: Message):
        c = msg.content
        await self._offer_next(int(c["event_id"]), int(c["category_id"]))
        return None

    # ---------- proactive: launch from FastAPI startup ----------
    def start_background(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_forever())

    async def _run_forever(self):
        while True:
            try:
                await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
                await self.sweep_once()
            except asyncio.CancelledError:
                return
            except Exception:
                # keep the loop alive — log via dashboard only
                continue

    async def sweep_once(self):
        """Proactive sweep — no user input needed."""
        with Session(self.engine) as db:
            expired = db.exec(select(WaitlistEntry).where(
                WaitlistEntry.status == "offered",
                WaitlistEntry.expires_at < _now())).all()
            for e in expired:
                e.status = "expired"
                db.add(e)
            db.commit()
            pairs = [(e.event_id, e.category_id) for e in expired]

        # re-offer to the next waiter in each (event, category) queue
        for event_id, cat_id in pairs:
            await self._offer_next(event_id, cat_id)

    # ---------- core offer logic ----------
    async def _offer_next(self, event_id: int, category_id: int):
        with Session(self.engine) as db:
            cat = db.get(TicketCategory, category_id)
            available = (cat.quantity - cat.sold) if cat else 0
            if available <= 0:
                return

            # earliest waiter who fits in the available seats
            waiter = db.exec(
                select(WaitlistEntry)
                .where(WaitlistEntry.event_id == event_id,
                       WaitlistEntry.category_id == category_id,
                       WaitlistEntry.status == "waiting",
                       WaitlistEntry.quantity <= available)
                .order_by(WaitlistEntry.id)
            ).first()
            if not waiter:
                return
            if waiter.cycles >= MAX_CYCLES:
                waiter.status = "expired"
                db.add(waiter)
                db.commit()
                return

            waiter.status = "offered"
            waiter.cycles += 1
            waiter.offered_at = _now()
            waiter.expires_at = _now() + OFFER_TTL
            db.add(waiter)
            db.commit()
            user_id = waiter.user_id
            qty = waiter.quantity
            expires_at = waiter.expires_at

        await self.bus.send(make(
            self.name, "*", "INFORM",
            {"user_id": user_id,
             "title": "A seat is free",
             "body": f"{qty} seat(s) held until "
                     f"{expires_at:%H:%M:%S} — book now or it moves on."},
            None,
        ))