from sqlalchemy.orm import Session
from app.api.v1.url_shortner.models import URL
from app.utils.common import generate_code



def create_short_url(
        db: Session,
        original_url: str
):

    code = generate_code()


    url = URL(
        short_code=code,
        original_url=original_url
    )


    db.add(url)
    db.commit()
    db.refresh(url)


    return url