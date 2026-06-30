from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse
from app.api.v1.url_shortner.models import URL
from app.api.v1.url_shortner.schema import URLCreate
from app.services.url_shortner import create_short_url
from app.utils.db_connection import Base, engine, get_db

router = APIRouter()
Base.metadata.create_all(bind=engine)



@router.post("/shorten")
def shorten(
    data: URLCreate,
    db: Session = Depends(get_db)
):

    url = create_short_url(
        db,
        str(data.url)
    )


    return {
        "short_url":
        f"http://localhost:8000/{url.short_code}"
    }



@router.get("/{code}")
def redirect(
    code: str,
    db: Session = Depends(get_db)
):

    url = (
        db.query(URL)
        .filter(
            URL.short_code == code
        )
        .first()
    )


    if not url:
        raise HTTPException(
            status_code=404,
            detail="URL not found"
        )


    return RedirectResponse(
        url.original_url
    )