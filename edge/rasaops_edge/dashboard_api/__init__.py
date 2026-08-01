"""Local FastAPI dashboard API (PR-08)."""

from .app import create_app, DashboardState

__all__ = ["create_app", "DashboardState"]
