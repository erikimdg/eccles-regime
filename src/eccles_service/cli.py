import argparse
import json
import os

from .pipeline import refresh
from .storage import Store, activate, active_bundle, load_version


def main():
    p = argparse.ArgumentParser(description="ECCLES frozen regime service")
    p.add_argument("--models", default=os.environ.get("ECCLES_MODELS", "models"))
    p.add_argument("--runtime", default=os.environ.get("ECCLES_RUNTIME", "runtime"))
    sub = p.add_subparsers(dest="command", required=True)
    for name in ["validate", "activate"]:
        s = sub.add_parser(name); s.add_argument("version")
    sub.add_parser("rollback")
    s = sub.add_parser("refresh"); s.add_argument("--version")
    sub.add_parser("status")
    args = p.parse_args()
    if args.command == "validate":
        b = load_version(args.models, args.version)
        out = {"model_version": b.version, "sha256": b.hash, "golden": "passed"}
    elif args.command == "activate":
        out = activate(args.models, args.runtime, args.version)
    elif args.command == "rollback":
        from pathlib import Path
        current = json.loads((Path(args.runtime) / "active.json").read_text())
        if not current.get("previous"):
            raise SystemExit("No previous model version")
        out = activate(args.models, args.runtime, current["previous"]["version"])
    elif args.command == "refresh":
        out = refresh(args.models, args.runtime, args.version)
    else:
        from .pipeline import status
        out = status(Store(args.runtime), active_bundle(args.models, args.runtime))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.command == "refresh" and out["status"] in {"source_error", "pipeline_error"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
