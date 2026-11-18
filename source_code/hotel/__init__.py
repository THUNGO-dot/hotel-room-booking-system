"""Grand Horizon Hotel - room booking system.

Three tiers (see README):
  hotel.web       presentation: Flask blueprints and Jinja templates, no business rules
  hotel.services  business logic: every rule from the report (BR-xx), framework-free
  hotel.db        data access: SQLite, schema in hotel/schema.sql (report section 6.2)
"""
from .app import create_app

__all__ = ["create_app"]
