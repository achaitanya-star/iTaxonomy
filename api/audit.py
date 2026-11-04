"""Vercel route /api/audit."""
from vercel_adapter import app_for

app = app_for('/api/audit')
