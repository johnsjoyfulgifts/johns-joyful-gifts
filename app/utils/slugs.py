from slugify import slugify
from sqlalchemy.orm import Session


def unique_slug(db: Session, model, name: str, exclude_id: int | None = None) -> str:
    base = slugify(name) or "item"
    slug = base
    counter = 2
    while True:
        query = db.query(model).filter(model.slug == slug)
        if exclude_id is not None:
            query = query.filter(model.id != exclude_id)
        if query.first() is None:
            return slug
        slug = f"{base}-{counter}"
        counter += 1
