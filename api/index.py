"""Single Vercel API function; rewrites preserve the original route in __path."""
from vercel_adapter import app_for

app = app_for("/api", dynamic=True)
