"""FastAPI server — starts both the API and Telegram bot."""
import sys
import os

_LIBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "python_libs")
if os.path.isdir(_LIBS_DIR) and _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

from contextlib import asynccontextmanager
import secrets
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import logging
import os

import db as database
import bot as bot_module

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s %(message)s'
)
# HTTPX includes full request URLs in INFO logs. Telegram API URLs contain the
# bot token, so keep those request logs quiet to avoid leaking credentials.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    log.info("Database initialized")
    try:
        await bot_module.start_bot()
        log.info("Telegram bot started")
    except Exception as e:
        log.error(f"Bot start failed: {e}")
    yield
    try:
        await bot_module.stop_bot()
    except Exception as e:
        log.warning(f"Bot stop error: {e}")


app = FastAPI(lifespan=lifespan, title="Vault-Pay Escrow")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from admin_api import router as admin_router
app.include_router(admin_router)

@app.post("/internal/ltc-regtest/wallet-event", status_code=202)
async def ltc_wallet_event(authorization: str = Header(default="")):
    """Wake the watcher. Amounts and transaction state always come from Core RPC."""
    from auto_ltc import enabled
    from auto_ltc_bot import monitor
    secret = os.environ.get("AUTO_LTC_NOTIFY_TOKEN", "")
    if not enabled() or not secret:
        raise HTTPException(404)
    if not secrets.compare_digest(authorization.encode(), ("Bearer " + secret).encode()):
        raise HTTPException(403)
    bot_app = bot_module._bot_app
    if not bot_app or not bot_app.running:
        raise HTTPException(503, "Bot is starting")
    name = "ltc-wallet-event"
    if not bot_app.job_queue.get_jobs_by_name(name):
        bot_app.job_queue.run_once(monitor, when=1, name=name)
    return {"queued": True}


@app.get("/api/health")
async def health():
    return {"ok": True}

# Serve React frontend static files if built
DIST = os.path.join(os.path.dirname(__file__), "dist")
if os.path.isdir(DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        index = os.path.join(DIST, "index.html")
        if os.path.isfile(index):
            return FileResponse(index)
        return {"detail": "Frontend not built yet"}
else:
    @app.get("/")
    async def root():
        return {"status": "ok", "message": "Vault-Pay Escrow API running. Frontend not built."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

