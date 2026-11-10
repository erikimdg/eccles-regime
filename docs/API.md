# API and operations

Service: `eccles-api.service`. Daily updater: `eccles-refresh.service` and `eccles-refresh.timer`. Default server directory: `/root/eccles-regime`. Default endpoint on that server: `http://127.0.0.1:18771`.

Deployment state: [DEPLOYMENT.md](DEPLOYMENT.md). The API and timer are active; original-source refresh, authentication and explicit stale-data responses are verified.

Access from another machine uses an authenticated SSH tunnel or an approved private gateway. The deployed bind is loopback. The templates do not create a public endpoint or change existing Telegram/Dashboard routes.

## Authentication and endpoints

Send `Authorization: Bearer <ECCLES_API_KEY>`. Keep the token in a backend secret or owner-only file. Avoid URLs, browser bundles and logs.

| Endpoint | Response |
| --- | --- |
| `GET /health/live` | Process liveness, no authentication, no model/data details |
| `GET /health/ready` | Authenticated readiness, 200 when current, 503 for missing/stale/error data |
| `GET /v1/status` | Expected US date, available date, source dates, last refresh, missing features and closures |
| `GET /v1/models/current` | Active model version/hash, classes, exact features, sources and training metadata |
| `GET /v1/regime/latest` | `{status, result}`, including stale status when retaining an older result |
| `GET /v1/regimes/{YYYY-MM-DD}` | Immutable result for a date, 404 when unpublished |
| `GET /v1/regimes?start=2026-01-01&end=2026-11-10&limit=100` | Newest first, limit 1-500; optional `model_version=1.0` |
| `GET /openapi.json` | Authenticated machine-readable API contract |

To fetch longer history, request another window ending the day before the oldest returned date. Date bounds are inclusive. There are no external inference, refresh or model-upload write endpoints.

## Result interpretation

`as_of` is the completed US market date. `observed_at` is when the source collection was complete; `published_at` is when the result was stored. `snapshot_sha256` identifies the saved source manifest. `model_version`, `model_sha256` and `service_version` identify the exact computation.

`regime` and `label` state the nearest-center decision. `closest_competitor` is the second closest center. `squared_distances` uses the frozen weighted 26-dimensional space. `winning_margin` is runner-up distance minus winner distance. `normalized_gap` divides that difference by their distance sum.

`margin_training_percentile` and `distance_training_percentile` compare with training dates assigned to the same class. `boundary_flag` means margin percentile <=20. `atypical_distance` compares with the original frozen pooled 99th-percentile nearest-distance threshold. These display flags leave the class unchanged.

`neon.features` contains all 26 signed assigned-class contributions. Each row includes the model input `value`, underlying `raw_value`/`raw_unit`, a fixed-training empirical percentile, and whether the model input exceeds training ranges. For rank features, `value * 100` is its trailing percentile; `training_percentile` uses the fixed training distribution of those ranks. Actual-return inputs retain return units in `value` and `raw_value`.

The signed relevance sum equals the winning margin within numerical tolerance. `positive` and `negative` are the largest supporting/opposing features. `competitor_allocation` gives NEON explanation weights. It is distinct from calibrated class probability. An exact tie returns `tie_or_numeric_boundary`, with null assigned-class relevance.

`interpretation.en/ko` is a deterministic summary of the calculation, with no daily language-model call. Selected-index participation counts the five overlapping MSCI Cyclical, Defensive, Japan, EM and EMU series. It is not broad stock-market breadth; read the specified five-series denominator.

## Daily source contract

The latest eligible US session is its scheduled XNYS close plus 90 minutes, accounting for DST and early closes. A 1,200-calendar-day original-history window supplies native returns and complete 252-observation rank warmup. Requests run sequentially with 45-second timeouts, bounded responses and no credential-bearing redirects.

The timer runs Monday-Saturday at **07:20, 09:20, 12:20, 16:20 Asia/Seoul**, with up to 30 seconds of jitter. Later runs retry delayed sources. It is persistent across missed timer events. WSL/Docker and the existing company host must be running; this service does not alter the host's existing WSL startup arrangement.

Every run records source identities, request date bounds, body hashes, observed timestamps, saved original responses, features, exclusions and revisions under `runtime/snapshots/<run-id>`. Request credentials are omitted. Per-version/date results in SQLite are immutable. A later revised snapshot is logged while the original published result remains available.

On first installation, publish only the latest complete eligible date. Subsequent runs catch up from the last published date. The research archive remains the historical source of 2001-2024 and examined 2026 outputs. The API does not imply earlier dates were observed live. 2025 is warmup only and remains unpublished.

Missing source requests preserve the last valid result and set `source_error`. Missing required same-day values set `missing_inputs`. Verified calendar closures set `scheduled_closure`. Never display the old result as a new-date regime. `GET /v1/regime/latest` is still available with explicit status; readiness returns 503 when the latest expected date is incomplete.

## Deployment

Use the included Dockerfile and systemd unit files on a Linux host with Docker. Container identity is uid 10001. API is limited to 512 MiB, daily updater to 1 GiB, each with one CPU and one numerical thread. Models mount read-only, root filesystems are read-only, Linux capabilities are dropped, and runtime records are a separate writable volume.

Create `runtime` owned by uid 10001. Create `secrets/service.env` with owner-only mode 0600 containing `ECCLES_API_KEY` and `FMP_API_KEY`. Keep the parent server directory private. Build `eccles-regime:1.0.0`, validate/activate the initial bundle, copy the three `deploy/eccles-*` units into `/etc/systemd/system`, reload, then enable the API and timer.

```bash
systemctl status eccles-api.service eccles-refresh.timer
systemctl start eccles-refresh.service
journalctl -u eccles-refresh.service -n 60 --no-pager
```

No Telegram messages are sent. Runtime secrets and records stay outside the private Git repository. Back up `models`, `runtime/active.json`, activation history, snapshots and SQLite together. Stop the updater for a filesystem-consistent backup or use SQLite's online backup interface.

## Model update and rollback

1. Create and review an immutable new bundle and report, for example `models/1.1`. Upload it into a staging directory, verify its hashes, then move the complete directory into `models/1.1`.
2. In the service container, run `eccles validate 1.1`.
3. Run `eccles refresh --version 1.1` with the updater's credentials. The active model stays unchanged.
4. Compare dated outputs and required review gates. Run `eccles activate 1.1`. It requires a candidate result through the active model's latest date.
5. Check `/v1/models/current`, `/v1/regime/latest`, and readiness. The next API request reads the new pointer, with no API restart.

To restore the preceding version, refresh that version if its last date is behind, then run `eccles rollback`. Keep both versions' records. Mutating files in an active version is rejected by bundle hashes and the database version registry.

For host commands, use the same `docker run` mounts/env file as `deploy/eccles-refresh.service`, replacing `eccles refresh` with the desired CLI command. A model-only update requires no image rebuild when the existing engine supports all recipes. A new operator needs a compatible service image first.

## Validation

```bash
python -m pytest tests -q
eccles validate 1.0
```

The release evidence under `docs/validation` covers all 6,090 frozen classifications, every assigned-class relevance, independent source-feature reconstruction and API/state-management tests on both local and Linux environments. Live server collection and authenticated responses are verified in DEPLOYMENT.md.
