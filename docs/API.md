# API and operations

Service: `eccles-api.service`. Daily updater: `eccles-refresh.service` and `eccles-refresh.timer`. Default server directory: `/root/eccles-regime`. Default endpoint on that server: `http://127.0.0.1:18771`.

Deployment state: [DEPLOYMENT.md](DEPLOYMENT.md). The API and timer are active; original-source refresh, authentication and explicit stale-data responses are verified.

Since November 23, collector v1.0.4 can publish from the homepage's own public USD/Price values. Such new payloads use `origin=live_original_sources_with_msci_homepage_api` and `input_quality.msci_homepage_api`, with three-decimal precision, affected sources and automatic-collection provenance. The frozen model version remains 1.0. One-minute morning checks trigger the existing publication process; all eighteen source requirements still apply. Dated results stay immutable, and source/publication/readiness times remain separately recorded. [Operation and qualification](../../runs/2026-11-23-homepage-automation/README.md).

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

## Training and validation history

Verified November 16, 2026 at 16:09 KST. The existing dated and range endpoints now serve the following complete archived observations:

| Period | Date range available | Records | Metadata |
| --- | --- | --- | --- |
| Training | 2001-01-02 to 2022-12-30 | 5,391 | `sample_period: training`, `in_sample: true` |
| Examined validation | 2023-01-03 to 2024-12-30 | 490 | `sample_period: validation`, `in_sample: false` |

```text
GET /v1/regimes?start=2001-01-01&end=2022-12-31&limit=500
GET /v1/regimes/2008-09-15
GET /v1/regimes?start=2023-01-01&end=2024-12-31&limit=500
```

Use the existing Bearer token. Range queries return at most 500 records, newest first. To retrieve all training dates, repeat with `end` set to the calendar day before the oldest returned `as_of`; stop on an empty page or when `end` precedes `start`. Keep `start` fixed. Only complete model observations are stored.

Each historical response includes the same regime, closest competitor, three squared distances, margin, training percentiles, flags, all 26 feature values and raw readings, signed NEON relevance, and English/Korean interpretation. The frozen v1.0 training transforms and centers are reused.

Historical rows carry `origin: historical_replay`, `observed_at: null`, `historical_vintage_verified: false`, and the period fields above. `published_at` is the actual import time. `snapshot_sha256` identifies the archived input/provenance manifest. Training rows are fitted-sample reconstructions. The 2023-2024 rows are previously examined validation observations. Original vendor arrival timestamps are unavailable in these archives.

The November 16 import added 5,881 rows. All existing six live records were preserved byte for byte. The API process, daily timer, v1.0 model hash and service v1.0.1 remained unchanged. 2025 remains warmup-only. The separate 209-row 2026 research replay is still in the research archive; live API history begins November 6, 2026.

Operator code: `scripts/backfill_history.py`. It checks the expected manifest hash, file/model hashes, aligned finite inputs, date ranges, saved clusters, distances and NEON contributions before publishing. One database transaction inserts the batch under the existing writer lock. Identical repeats are skipped; conflicts abort and roll back the batch. No fitting or source downloads occur.

```bash
python scripts/backfill_history.py --models models --runtime runtime \
  --archive runtime/history-imports/2026-11-16-training-validation/archive \
  --manifest-sha256 2bf194d01eb8c03b0d13c0acc959d91d476baa3b6200d1f5b412ba5408fd87c9
```

Run the operator with the same installed engine as the live service and one numerical thread. Server import receipt and the full authenticated API check are stored next to the archive. The pre-import SQLite backup is `runtime/history-imports/2026-11-16-before-history.sqlite3`.

## Result interpretation

`as_of` is the completed US market date. `observed_at` is when live source collection was complete, or null for historical replay; `published_at` is when the result was stored. `snapshot_sha256` identifies the saved source manifest. `model_version`, `model_sha256` and `service_version` identify the exact computation.

`regime` and `label` state the nearest-center decision. `closest_competitor` is the second closest center. `squared_distances` uses the frozen weighted 26-dimensional space. `winning_margin` is runner-up distance minus winner distance. `normalized_gap` divides that difference by their distance sum.

`margin_training_percentile` and `distance_training_percentile` compare with training dates assigned to the same class. `boundary_flag` means margin percentile <=20. `atypical_distance` compares with the original frozen pooled 99th-percentile nearest-distance threshold. These display flags leave the class unchanged.

`neon.features` contains all 26 signed assigned-class contributions. Each row includes the model input `value`, underlying `raw_value`/`raw_unit`, a fixed-training empirical percentile, and whether the model input exceeds training ranges. For rank features, `value * 100` is its trailing percentile; `training_percentile` uses the fixed training distribution of those ranks. Actual-return inputs retain return units in `value` and `raw_value`.

The signed relevance sum equals the winning margin within numerical tolerance. `positive` and `negative` are the largest supporting/opposing features. `competitor_allocation` gives NEON explanation weights. It is distinct from calibrated class probability. An exact tie returns `tie_or_numeric_boundary`, with null assigned-class relevance.

`interpretation.en/ko` is a deterministic summary of the calculation, with no daily language-model call. Selected-index participation counts the five overlapping MSCI Cyclical, Defensive, Japan, EM and EMU series. It is not broad stock-market breadth; read the specified five-series denominator.

## November 20 homepage publication

November 19 is published as C2 at 10:06:15 KST. Authenticated latest/ready checks passed at 10:06:19. The existing `/v1/regime/latest` contract is unchanged. The result includes `origin: original_msci_homepage_displayed_values` and `input_quality` for six same-index USD/Price homepage observations, displayed to two decimal places. Later original full-precision values matched their rounding and preserved C2. The immutable payload retains its original reconciliation status; the completed comparison is in the [operations record](../../runs/2026-11-20-msci-homepage/README.md).

Collector v1.0.2 accepts a dated verified homepage capture. The API process remains v1.0.1, model v1.0. Future direct homepage acquisition is not automatic because the server receives a browser challenge. No client-side website refresh was performed.

## Automatic public-original extension, service v1.0.3

The collector can append the target date from MSCI's official public XLS download when the graph history is delayed. New assisted publications use `origin: live_original_sources_with_msci_public_download` and `input_quality.msci_public_download` with affected source IDs, three-decimal precision and automatic collection. This preserves existing full-precision history and immutable results. Source identity and at least three overlap dates must pass. A failed or stale download cannot turn missing inputs into a current result.

The existing 19 November payload remains the earlier homepage publication. Production model v1.0 and the API process remain unchanged. The timer adds 08:58, and the operational ledger records 09:00 outcomes separately from 10:00. Before-09:00 availability is still unverified. [Evidence and current limits](../../runs/2026-11-20-before09-original-feed/README.md).

## Daily source contract

The latest eligible US session is its scheduled XNYS close plus 90 minutes, accounting for DST and early closes. A 1,200-calendar-day original-history window supplies native returns and complete 252-observation rank warmup. Requests run sequentially with 45-second timeouts, bounded responses and no credential-bearing redirects.

From November 19, 2026, the timer runs Monday-Saturday at **07:40, 08:00, 08:20, 08:40, 08:50, 08:55, 09:20, 09:40, 09:50, 09:55, 09:58, 10:40, 12:20 and 16:20 Asia/Seoul**, targeting prior-US-session publication before **10:00 KST**. Four 09:40-09:58 retries were added, preserving all previous slots. Accuracy is one second, randomized delay is zero, and missed-run persistence remains. The oneshot service and writer lock prevent overlapping collection.

The API returns precomputed results, including NEON, without starting collection on a GET request. A ready morning result requires `data_status == "current"`, `fresh == true`, and `result.as_of == status.expected_as_of`. Readiness returns 200 for this condition and 503 for incomplete or failed collection. After weekends and US holidays, the expected date remains the latest eligible US trading session.

Source timing remains a constraint: on November 19 at 09:20, six MSCI series still ended November 17; the November 18 result was published at 10:06:30 KST. The current objective is before 10:00, with dense observation from 09:40 to 09:58. Completion still depends on original-source arrival. [Deployment evidence](../../runs/2026-11-19-api-before10/README.md).

 WSL/Docker and the existing company host must be running; this service does not alter the host's existing WSL startup arrangement.

Timing records: `/root/eccles-regime/runtime/timing/events/` stores each refresh receipt; `daily/` stores JSON and Markdown summaries by KST date and required US-session date. `latest-summary.json` includes each source's first observed presence and preceding missing-date observation, `result_published_at`, `first_api_ready_observed_at` and `deadline_status`. The recorder verifies both authenticated readiness and latest-result endpoints after collection. It preserves unknown retrieval states and never alters inference. November 19 is commissioning; prospective observations begin November 20. [Record definitions](../../runs/2026-11-19-api-before10/README.md#recorded-evidence).

Every run records source identities, request date bounds, body hashes, observed timestamps, saved original responses, features, exclusions and revisions under `runtime/snapshots/<run-id>`. Request credentials are omitted. Per-version/date results in SQLite are immutable. A later revised snapshot is logged while the original published result remains available.

On first installation, publish only the latest complete eligible date. Subsequent runs catch up from the last published date. The verified historical import above adds 2001-2024 results. Examined January-November 5, 2026 research outputs remain in the research archive. 2025 is warmup only and remains unpublished.

Missing source requests preserve the last valid result and set `source_error`. Missing required same-day values set `missing_inputs`. Verified calendar closures set `scheduled_closure`. Never display the old result as a new-date regime. `GET /v1/regime/latest` is still available with explicit status; readiness returns 503 when the latest expected date is incomplete.

## Production source inventory

Timing audit update, November 19: 43 historical attempts cover eight eligible mornings, with four observed current results before 10:00 and four deadline states unknown. The 09:40-09:58 observation schedule and source/API recorder were subsequently approved and deployed. [Timing and six-source audit](../../runs/2026-11-19-msci-delivery/NON_LSEG.md#six-delayed-inputs-0900-versus-1000).

Qualification update, November 19: the user excluded LSEG. A separate MSCI public table matches three original USD price-index levels on one date. MSCI Index API and ICE remain full-route candidates; no alternate feed has passed the coverage/value/timing gate. [External-source findings](../../runs/2026-11-19-msci-delivery/NON_LSEG.md).

Verified against the deployed model and collector hashes on November 19, 2026 at 10:53 KST, and the saved 09:20 and 10:06 collection manifests. Global26 derives 26 features from 18 original index series. The currently deployed pipeline uses these collection routes:

| Series | Collector | Identifier |
| --- | --- | --- |
| S&P 500 | FMP historical index API | `^GSPC` |
| Nasdaq 100 | FMP historical index API | `^NDX` |
| Russell 2000 | Yahoo Finance chart endpoint | `^RUT` |
| FTSE 100 | FMP historical index API | `^FTSE` |
| VIX | Official Cboe daily CSV, with qualified trailing FMP extension | `VIX`, fallback `^VIX` |
| VSTOXX | Official STOXX historical text file | `V2TX` |
| MSCI USA | MSCI public index-level endpoint | `984000` |
| MSCI UK | MSCI public index-level endpoint | `982600` |
| MSCI Japan | MSCI public index-level endpoint | `939200` |
| MSCI Canada | MSCI public index-level endpoint | `912400` |
| MSCI Australia | MSCI public index-level endpoint | `903600` |
| MSCI Germany | MSCI public index-level endpoint | `928000` |
| MSCI France | MSCI public index-level endpoint | `925000` |
| MSCI Switzerland | MSCI public index-level endpoint | `975600` |
| MSCI Emerging Markets | MSCI public index-level endpoint | `891800` |
| MSCI EMU | MSCI public index-level endpoint | `106400` |
| MSCI World Cyclical Sectors | MSCI public index-level endpoint | `704856` |
| MSCI World Defensive Sectors | MSCI public index-level endpoint | `704855` |

All twelve MSCI requests use `currency_symbol=USD`, `index_variant=STRD`, `data_frequency=DAILY`; the collector reads `calc_date` and `level_eod`. These are index-level inputs. Five/21-session returns, volatility, correlations, relative performance and percentiles are derived by the model's frozen feature recipes.

Collection endpoints:

- FMP: `https://financialmodelingprep.com/stable/historical-price-eod/full`, using `symbol`, `from`, `to` and the existing credential. The collector reads `close`. [Provider documentation](https://site.financialmodelingprep.com/developer/docs/stable/index-historical-price-eod-full).
- Yahoo: `https://query1.finance.yahoo.com/v8/finance/chart/%5ERUT`, daily `close`, preserving the exchange date.
- Cboe: [VIX_History.csv](https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv), `DATE` and `CLOSE`. [Official download page](https://www.cboe.com/tradable-products/vix/vix-historical-data).
- STOXX: [h_v2tx.txt](https://www.stoxx.com/document/Indices/Current/HistoricalData/h_v2tx.txt), `Date` and `Indexvalue`.
- MSCI: `https://app2.msci.com/products/service/index/indexmaster/getLevelDataForGraph`, using the codes above with start/end dates. This is the public website's index-level query route. [MSCI end-of-day data page](https://www.msci.com/end-of-day-data-search).

VIX is the only configured trailing fallback. It extends dates after the official file's last date using FMP's same `^VIX` series, after at least three overlapping dates agree within 0.005 index points. Both November 19 manifests record **November 18 VIX from FMP**, while retaining the Cboe history. This is an already deployed v1.0.1 rule.

At 09:20, MSCI USA, Canada, EM, EMU, Cyclical and Defensive ended November 17; the other twelve sources included November 18, counting the VIX extension. At 10:06, all eighteen included November 18. The observed lag is specific to the current public collection route. Publication timing through LSEG or MSCI's other distribution products remains unverified.

The production pipeline has no LSEG, company CDS or macro data input. B27-equal's CDS input remains in the separate research workflow. [Read-only server evidence](../../runs/2026-11-19-api-morning/source-inventory.json). No source, model or scheduler changed during this inventory check.

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
