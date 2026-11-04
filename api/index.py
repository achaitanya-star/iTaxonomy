"""Vercel function at /api (kept for diagnostics and API base requests)."""
from vercel_adapter import app_for

app = app_for("/api")
