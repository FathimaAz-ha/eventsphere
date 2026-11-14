import json

from sqlmodel import Session

from .bus import MessageBus
from .venue_agent import VenueAgent
from .booking_agent import BookingAgent
from .ticket_agent import TicketAgent
from .waitlist_agent import WaitlistAgent
from .notification_agent import NotificationAgent
from ..models import MessageLog


def build_agents(engine):
    """Wire all agents to a single bus and persist every message."""
    bus = MessageBus()

    async def log_writer(msg):
        with Session(engine) as db:
            db.add(MessageLog(
                conversation_id=msg.conversation_id,
                sender=msg.sender,
                receiver=msg.receiver,
                performative=msg.performative,
                content=json.dumps(msg.content),
            ))
            db.commit()

    bus.set_logger(log_writer)

    agents = {
        "VenueAgent": VenueAgent(bus, engine),
        "BookingAgent": BookingAgent(bus, engine),
        "TicketAgent": TicketAgent(bus, engine),
        "WaitlistAgent": WaitlistAgent(bus, engine),
        "NotificationAgent": NotificationAgent(bus, engine),
    }
    return bus, agents