"""
Entity context management.
Resolves which business entity a document or transaction belongs to.
"""

import logging
from sqlalchemy.orm import Session

from database.models import Entity, EntityType, AccountingMethod
from config.entities import ENTITIES

logger = logging.getLogger(__name__)


def seed_entities(session: Session):
    """
    Create the default entities in the database if they don't exist.
    Called during init-db.
    """
    for slug, cfg in ENTITIES.items():
        existing = session.query(Entity).filter_by(slug=slug).first()
        if existing:
            continue

        entity = Entity(
            name=cfg["name"],
            slug=slug,
            entity_type=EntityType(cfg["entity_type"]),
            state=cfg["state"],
            accounting_method=AccountingMethod(cfg["accounting_method"]),
        )
        session.add(entity)
        logger.info(f"Seeded entity: {cfg['name']} ({slug})")

    session.commit()


def resolve_entity(session: Session, text: str) -> Entity | None:
    """
    Attempt to resolve which entity a document belongs to based on text content.
    Uses keyword matching from entity config. Returns None if ambiguous.

    For more complex cases, the Claude API classifier handles entity assignment.
    """
    text_lower = text.lower()
    matches = []

    for slug, cfg in ENTITIES.items():
        keywords = cfg.get("filing_keywords", [])
        for keyword in keywords:
            if keyword.lower() in text_lower:
                entity = session.query(Entity).filter_by(slug=slug).first()
                if entity:
                    matches.append(entity)
                break

    if len(matches) == 1:
        return matches[0]

    # Ambiguous or no match — caller should use Claude API or prompt user
    return None


def get_entity_by_slug(session: Session, slug: str) -> Entity | None:
    """Get an entity by its slug identifier."""
    return session.query(Entity).filter_by(slug=slug, active=True).first()


def get_all_entities(session: Session) -> list[Entity]:
    """Get all active entities."""
    return session.query(Entity).filter_by(active=True).all()
