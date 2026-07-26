"""Local dev server: serves public/ statically alongside the API.

On Vercel these are separate (CDN for static, lambda for /api/*). This file exists
only so `python dev.py` reproduces that shape locally. Vercel never runs it.

Without DATABASE_URL the API uses SQLite, so the whole CRM works offline.
"""

import uvicorn
from fastapi.staticfiles import StaticFiles

from api.index import app

app.mount("/", StaticFiles(directory="public", html=True), name="public")

if __name__ == "__main__":
    import os

    from api import db

    print(f"storage backend: {db.backend_name()}", flush=True)
    if not db.IS_POSTGRES:
        print(f"sqlite file:     {db.SQLITE_PATH}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
