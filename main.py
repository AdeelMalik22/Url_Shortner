from fastapi import FastAPI
from app.api.v1.url_shortner.router import router as url_shortner_router
app = FastAPI()

app.include_router(url_shortner_router)
