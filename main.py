from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.api.v1.url_shortner.router import router as url_shortner_router

app = FastAPI(title="SnapLink URL Shortener")

# Serve static assets (CSS, JS, images if added later)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include API routes
app.include_router(url_shortner_router)


@app.get("/", response_class=FileResponse)
def index():
    """Serve the URL shortener UI."""
    return FileResponse("static/index.html")
