"""Vercel route /api/state."""
from vercel_adapter import app_for

app = app_for('/api/state')
