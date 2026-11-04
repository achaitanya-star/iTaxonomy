"""Vercel route /api/dbstatus. Reports connection target without credentials."""
from vercel_adapter import app_for

app = app_for('/api/dbstatus')
