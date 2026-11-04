"""Vercel route /api/profile."""
from vercel_adapter import app_for

app = app_for('/api/profile')
