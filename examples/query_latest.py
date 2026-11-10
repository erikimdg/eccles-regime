"""Read the private API through an existing tunnel, keeping the token out of argv."""
import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

p = argparse.ArgumentParser()
p.add_argument("--url", default="http://127.0.0.1:18771")
p.add_argument("--token-file", type=Path, required=True)
args = p.parse_args()
token = args.token_file.read_text().strip()
req = Request(args.url.rstrip("/") + "/v1/regime/latest", headers={"Authorization": "Bearer " + token})
with urlopen(req, timeout=20) as response:
    print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
