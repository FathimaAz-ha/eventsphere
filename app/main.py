from fastapi import FastAPI

app = FastAPI(title="EventSphere")


@app.get("/health")
def health():
    return {"status": "ok", "service": "eventsphere"}