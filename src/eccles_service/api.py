"""Authenticated read-only API. Refresh and activation are operator commands."""
from datetime import date
import hmac
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import __version__
from .pipeline import status
from .storage import Store, active_bundle, load_version


def create_app(models=None, runtime=None, api_key=None):
    models = Path(models or os.environ.get("ECCLES_MODELS", "models"))
    runtime = Path(runtime or os.environ.get("ECCLES_RUNTIME", "runtime"))
    api_key = api_key if api_key is not None else os.environ.get("ECCLES_API_KEY", "")
    if len(api_key) < 32:
        raise RuntimeError("ECCLES_API_KEY must contain at least 32 characters")
    store = Store(runtime)
    active_bundle(models, runtime)  # Refuse corrupt or inactive startup.
    app = FastAPI(title="ECCLES Regime API", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    bearer = HTTPBearer(auto_error=False)

    def authorized(auth: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if auth is None or auth.scheme.lower() != "bearer" or not hmac.compare_digest(auth.credentials, api_key):
            raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"})

    def get_bundle(version=None):
        try:
            return load_version(models, version) if version else active_bundle(models, runtime)
        except (ValueError, FileNotFoundError, KeyError):
            raise HTTPException(status_code=503, detail="Model unavailable") from None

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready", dependencies=[Depends(authorized)])
    def ready():
        out = status(store, get_bundle())
        return JSONResponse(out, status_code=200 if out["data_status"] == "current" else 503)

    @app.get("/v1/status", dependencies=[Depends(authorized)])
    def current_status():
        return status(store, get_bundle())

    @app.get("/v1/models/current", dependencies=[Depends(authorized)])
    def current_model():
        b = get_bundle()
        return {"model_version": b.version, "model_sha256": b.hash, "service_version": __version__,
                "name": b.spec["name"], "features": b.spec["features"], "sources": b.spec["sources"],
                "classes": b.spec["classes"], "training": b.spec["training"], "neon_beta": b.beta}

    @app.get("/v1/regime/latest", dependencies=[Depends(authorized)])
    def latest():
        b = get_bundle(); item = store.latest(b.version)
        if item is None:
            raise HTTPException(status_code=404, detail="No published result for the active model")
        return {"status": status(store, b), "result": item}

    @app.get("/v1/regimes", dependencies=[Depends(authorized)])
    def history(start: date, end: date, limit: int = Query(default=100, ge=1, le=500), model_version: str | None = None):
        if end < start:
            raise HTTPException(status_code=422, detail="End precedes start")
        b = get_bundle(model_version)
        return {"model_version": b.version, "items": store.history(b.version, start.isoformat(), end.isoformat(), limit), "order": "date descending", "limit": limit}

    @app.get("/v1/regimes/{day}", dependencies=[Depends(authorized)])
    def dated(day: date, model_version: str | None = None):
        b = get_bundle(model_version)
        item = store.get(b.version, day.isoformat())
        if item is None:
            raise HTTPException(status_code=404, detail="No published result for this model and date")
        return item

    @app.get("/openapi.json", dependencies=[Depends(authorized)])
    def schema():
        return app.openapi()

    return app
