from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .db import init_db, seed, engine
from .agents.registry import build_agents
from .routes import (
    auth, venues, events, bookings, tickets, hire, dashboard, agents_api,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed()
    bus, agents = build_agents(engine)
    app.state.bus = bus
    app.state.agents = agents
    agents["WaitlistAgent"].start_background()   # proactive behaviour
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


@app.get("/health")
def health():
    return {"status": "ok", "service": "eventsphere"}


# --- HTML routes: stubs for now, replaced in Stage 4 ---

def _shell(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title} — EventSphere</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:800px;"
        "margin:1rem auto;padding:1rem;line-height:1.5}"
        "code{background:#eee;padding:2px 4px;border-radius:3px}</style>"
        f"</head><body><h1>{title}</h1>{body}"
        "<p><a href='/docs'>/docs</a> · <a href='/api/auth/me'>/me</a></p>"
        "</body></html>"
    )


@app.get("/", response_class=HTMLResponse)
def home():
    return _shell("EventSphere", "<p>EventSphere API is live. Full UI in Stage 4.</p>")


@app.get("/events", response_class=HTMLResponse)
def events_page():
    return _shell("Events", "<p>UI coming in Stage 4. Try <code>GET /api/events</code>.</p>")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page():
    return _shell("Dashboard", "<p>UI coming in Stage 4. Try <code>GET /api/dashboard</code> (admin).</p>")