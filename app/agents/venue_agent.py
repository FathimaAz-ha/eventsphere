import json
from datetime import datetime, timedelta
from sqlmodel import Session, select

from .bus import Message, MessageBus, make
from ..models import Venue, Event, AgentState

BUFFER = timedelta(minutes=30)
WEEKEND_MULTIPLIER = 1.2


class VenueAgent:
    name = "VenueAgent"
    goal = "Keep every venue's calendar free of clashes"

    def __init__(self, bus: MessageBus, engine):
        self.bus = bus
        self.engine = engine
        bus.subscribe(self.name, self.handle)

    # ---------- entry point ----------
    async def handle(self, msg: Message) -> Message:
        action = msg.content.get("action")
        if action == "check_slot":
            return self._check_slot(msg)
        if action == "hire_request":
            return await self._hire_request(msg)
        return self._refuse(msg, f"Unknown action '{action}'")

    # ---------- helpers ----------
    def _refuse(self, msg, reason):
        return make(self.name, msg.sender, "REFUSE", {"reason": reason}, msg.conversation_id)

    def _propose(self, msg, reason, alternatives):
        return make(self.name, msg.sender, "PROPOSE",
                    {"reason": reason, "alternatives": alternatives}, msg.conversation_id)

    def _clashes(self, db, venue_id, start, end, ignore_event_id=None):
        q = select(Event).where(Event.venue_id == venue_id,
                                Event.status == "published")
        for e in db.exec(q):
            if ignore_event_id and e.id == ignore_event_id:
                continue
            if start < e.ends_at + BUFFER and e.starts_at - BUFFER < end:
                return e
        return None

    def _free_slots(self, db, venue_id, duration, after, want=3):
        events = db.exec(select(Event).where(Event.venue_id == venue_id,
                                             Event.status == "published")).all()
        slots, t, limit = [], after, after + timedelta(days=14)
        while t < limit and len(slots) < want:
            end = t + duration
            if not any(t < e.ends_at + BUFFER and e.starts_at - BUFFER < end for e in events):
                slots.append({"start": t.isoformat(), "end": end.isoformat()})
            t += timedelta(minutes=30)
        return slots

    def _price(self, venue, start, end):
        hours = (end - start).total_seconds() / 3600
        mult = WEEKEND_MULTIPLIER if start.weekday() >= 5 else 1.0
        return round(hours * venue.hourly_rate * mult, 2)

    # ---------- REQUEST: can this event take this slot? ----------
    def _check_slot(self, msg: Message) -> Message:
        c = msg.content
        start, end = datetime.fromisoformat(c["start"]), datetime.fromisoformat(c["end"])
        if end <= start:
            return self._refuse(msg, "End time must be after start time")

        with Session(self.engine) as db:
            venue = db.get(Venue, c["venue_id"])
            if not venue:
                return self._refuse(msg, f"Venue {c['venue_id']} does not exist")
            if c.get("total_tickets", 0) > venue.capacity:
                return self._refuse(
                    msg, f"Total tickets ({c['total_tickets']}) exceed venue "
                         f"capacity ({venue.capacity})")
            clash = self._clashes(db, venue.id, start, end, c.get("ignore_event_id"))
            if clash:
                return self._refuse(
                    msg, f"Clashes with '{clash.title}' "
                         f"({clash.starts_at:%d %b %H:%M}–{clash.ends_at:%H:%M}); "
                         f"venues need a {BUFFER.seconds // 60}-minute turnaround buffer")

        return make(self.name, msg.sender, "AGREE",
                    {"venue_id": c["venue_id"], "start": c["start"], "end": c["end"]},
                    msg.conversation_id)

    # ---------- REQUEST: private hire ----------
    async def _hire_request(self, msg: Message) -> Message:
        c = msg.content
        start, end = datetime.fromisoformat(c["start"]), datetime.fromisoformat(c["end"])
        duration = end - start

        with Session(self.engine) as db:
            venue = db.get(Venue, c["venue_id"])
            if not venue:
                return self._refuse(msg, f"Venue {c['venue_id']} does not exist")
            if not venue.open_for_hire:
                return self._refuse(msg, f"'{venue.name}' is not open for private hire")

            # too many guests -> propose other venues
            if c["guests"] > venue.capacity:
                others = db.exec(select(Venue).where(Venue.open_for_hire == True,
                                                     Venue.capacity >= c["guests"])).all()
                if not others:
                    return self._refuse(msg, f"No venue can hold {c['guests']} guests")
                return self._propose(
                    msg,
                    f"'{venue.name}' holds only {venue.capacity} guests",
                    [{"venue_id": v.id, "venue_name": v.name,
                      "start": c["start"], "end": c["end"]} for v in others[:3]])

            clash = self._clashes(db, venue.id, start, end)
            if clash:
                alts = [{"venue_id": venue.id, "venue_name": venue.name, **s}
                        for s in self._free_slots(db, venue.id, duration, start)]
                return self._propose(
                    msg,
                    f"Venue booked by '{clash.title}' at that time",
                    alts)

            price = self._price(venue, start, end)

            # escalation rules -> admin decides
            escalate = (c["guests"] > venue.capacity * 0.8
                        or start - datetime.utcnow() < timedelta(hours=24)
                        or duration > timedelta(hours=6))
            if escalate:
                reason = ("large booking" if c["guests"] > venue.capacity * 0.8 else
                          "short notice" if start - datetime.utcnow() < timedelta(hours=24) else
                          "long duration")
                return make(self.name, msg.sender, "PROPOSE",
                            {"reason": f"Escalated for admin approval: {reason}",
                             "escalate": True, "price": price,
                             "alternatives": []},
                            msg.conversation_id)

        return make(self.name, msg.sender, "AGREE",
                    {"price": price, "venue_id": venue.id,
                     "start": c["start"], "end": c["end"]},
                    msg.conversation_id)