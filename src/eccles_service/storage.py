"""Immutable dated records, serialized refresh and atomic bundle activation."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .model import Bundle, VERSION


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("w") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False, allow_nan=False)
            f.write("\n")
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def exclusive(runtime):
    runtime = Path(runtime); runtime.mkdir(parents=True, exist_ok=True)
    with (runtime / "writer.lock").open("a") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another refresh or activation is running") from None
        yield


class Store:
    def __init__(self, runtime):
        self.root = Path(runtime)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "regimes.sqlite3"
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE IF NOT EXISTS models (version TEXT PRIMARY KEY, hash TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS results (version TEXT NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(version, day))")
            c.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, version TEXT NOT NULL, started TEXT NOT NULL, payload TEXT NOT NULL)")

    def connect(self):
        c = sqlite3.connect(self.db, timeout=15)
        c.execute("PRAGMA busy_timeout=15000")
        return c

    def register(self, bundle):
        with self.connect() as c:
            row = c.execute("SELECT hash FROM models WHERE version=?", (bundle.version,)).fetchone()
            if row and row[0] != bundle.hash:
                raise ValueError("Published model version is immutable; use a new version")
            c.execute("INSERT OR IGNORE INTO models VALUES (?,?)", (bundle.version, bundle.hash))

    def publish(self, payload):
        with self.connect() as c:
            row = c.execute("SELECT hash FROM models WHERE version=?", (payload["model_version"],)).fetchone()
            if not row or row[0] != payload["model_sha256"]:
                raise ValueError("Unregistered model hash")
            n = c.execute("INSERT OR IGNORE INTO results VALUES (?,?,?)", (payload["model_version"], payload["as_of"], json.dumps(payload, allow_nan=False, ensure_ascii=False))).rowcount
            return bool(n)

    def latest(self, version):
        with self.connect() as c:
            row = c.execute("SELECT payload FROM results WHERE version=? ORDER BY day DESC LIMIT 1", (version,)).fetchone()
        return json.loads(row[0]) if row else None

    def get(self, version, day):
        with self.connect() as c:
            row = c.execute("SELECT payload FROM results WHERE version=? AND day=?", (version, day)).fetchone()
        return json.loads(row[0]) if row else None

    def history(self, version, start, end, limit=100):
        with self.connect() as c:
            rows = c.execute("SELECT payload FROM results WHERE version=? AND day>=? AND day<=? ORDER BY day DESC LIMIT ?", (version, start, end, limit)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def attempt(self, obj):
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO attempts VALUES (?,?,?,?)", (obj["run_id"], obj["model_version"], obj["started_at"], json.dumps(obj, allow_nan=False)))

    def last_attempt(self, version):
        with self.connect() as c:
            row = c.execute("SELECT payload FROM attempts WHERE version=? ORDER BY started DESC LIMIT 1", (version,)).fetchone()
        return json.loads(row[0]) if row else None


def load_version(models, version):
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError("Invalid model version")
    return Bundle(Path(models) / version)


def active_bundle(models, runtime):
    pointer = json.loads((Path(runtime) / "active.json").read_text())
    bundle = load_version(models, pointer["version"])
    if pointer["sha256"] != bundle.hash:
        raise ValueError("Active bundle hash changed")
    return bundle


def activate(models, runtime, version):
    with exclusive(runtime):
        b = load_version(models, version)
        store = Store(runtime); store.register(b)
        previous_path = Path(runtime) / "active.json"
        previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
        if previous and previous["version"] == version and previous["sha256"] == b.hash:
            return previous
        # A replacement must already have a valid, version-specific result.
        if previous:
            replacement = store.latest(version)
            current = store.latest(previous["version"])
            if replacement is None or (current and replacement["as_of"] < current["as_of"]):
                raise ValueError("Refresh the replacement through the current published date before activation")
        obj = dict(version=version, sha256=b.hash, activated_at=utcnow(), previous=previous)
        atomic_json(previous_path, obj)
        with (Path(runtime) / "activation-history.jsonl").open("a") as f:
            f.write(json.dumps({k: v for k, v in obj.items() if k != "previous"}) + "\n")
        return obj
