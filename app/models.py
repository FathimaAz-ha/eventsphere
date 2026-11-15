from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime
from sqlmodel import SQLModel, Field


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    name: str
    password_hash: str
    role: str = "attendee"


class Venue(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    location: str
    capacity: int
    description: str = ""
    image_url: str = ""
    hourly_rate: float = 0.0
    open_for_hire: bool = False


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    category: str
    venue_id: int
    starts_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    ends_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    description: str = ""
    status: str = "draft"


class TicketCategory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(index=True)
    name: str
    price: float
    quantity: int
    sold: int = 0


class Booking(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    event_id: int
    category_id: int
    quantity: int
    amount: float
    payment_ref: str
    status: str = "confirmed"
    refund_amount: float = 0.0
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False),
    )


class Ticket(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    booking_id: int
    event_id: int
    code: str = Field(index=True, unique=True)
    status: str = "valid"
    used_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True))


class HireRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    venue_id: int
    starts_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    ends_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    purpose: str
    guests: int
    status: str = "pending"
    price: float = 0.0
    alternatives: str = ""
    decided_by: str = ""

    @property
    def title(self) -> str:
        return f"Private Hire ({self.purpose})"


class WaitlistEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    event_id: int
    category_id: int
    quantity: int
    status: str = "waiting"
    cycles: int = 0
    offered_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True))
    expires_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True))


class Notification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    title: str
    body: str
    read: bool = False
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False),
    )


class MessageLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True)
    sender: str
    receiver: str
    performative: str
    content: str
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False),
    )


class AgentState(SQLModel, table=True):
    agent: str = Field(primary_key=True)
    data: str = "{}"
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False),
    )


class SessionToken(SQLModel, table=True):
    id: str = Field(primary_key=True)
    user_id: int
    csrf: str
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False),
    )