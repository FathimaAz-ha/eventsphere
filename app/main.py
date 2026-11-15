from contextlib import asynccontextmanager

from fastapi import FastAPI

from .db import init_db, seed, engine
from .agents.registry import build_agents
from .routes import (
    auth, venues, events, bookings, tickets, hire, dashboard, agents_api, pages,
)
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse     # ← add this line

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed()
    bus, agents = build_agents(engine)
    app.state.bus = bus
    app.state.agents = agents
    agents["WaitlistAgent"].start_background()
    yield


app = FastAPI(title="EventSphere", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(venues.router)
app.include_router(events.router)
app.include_router(bookings.router)
app.include_router(tickets.router)
app.include_router(tickets.scan_router)
app.include_router(hire.router)
app.include_router(dashboard.router)
app.include_router(agents_api.router)
app.include_router(pages.router)          # ← HTML pages

@app.get("/")
def root():
    return RedirectResponse("/events", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok", "service": "eventsphere"}

