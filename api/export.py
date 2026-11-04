"""Vercel route /api/export."""
from vercel_adapter import app_for

app = app_for('/api/export')
