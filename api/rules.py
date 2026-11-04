"""Vercel route /api/rules."""
from vercel_adapter import app_for

app = app_for('/api/rules')
