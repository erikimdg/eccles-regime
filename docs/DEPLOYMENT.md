# Deployment record

## November 23: homepage read action and one-minute checks

Collector image `eccles-regime:1.0.4-homepage-auto` deployed at 10:06:15 KST. The exact-original homepage route is automatic, preceding XLS/manual fallbacks when graph data lag. The same serialized service runs every minute 07:40-10:40 KST, with original full-refresh slots preserved and intervening ticks limited to small homepage checks. It skips completed sessions and triggers inference/publication on newly available missing values. Model v1.0 and the existing API process remain unchanged. 103 checks and actual-server twelve-source replay passed. First eligible before-09:00 observation is November 24. [Evidence and rollback](../../runs/2026-11-23-homepage-automation/README.md).

## November 20: automatic original download, 09:00 target

Collector v1.0.3 adds the official MSCI public XLS route for all twelve exact USD/STRD indices. All 936 common values over 78 dates per index match three-decimal rounding. Server-side missing-date replay and 68 checks passed. Added 08:58 collection and separate 09:00 timing fields; the 10:00 records remain. The API is still current for November 19, C2. **Before-09:00 delivery is unverified, with zero prospective mornings for this route.** Both public routes can update late. No MSCI API/SFTP contract exists; licensed access and a verified early final-EOD delivery commitment remain required for that route. [Implementation and limits](../../runs/2026-11-20-before09-original-feed/README.md), [provider request](../../runs/2026-11-20-before09-original-feed/OFFICIAL_DATA_REQUEST-ko.txt).


## November 20: November 19 result published, homepage input deployed

**2026-11-19: C2, Weak / stressed.** Published 10:06:15 KST, authenticated API current/fresh at 10:06:19. Used six exact MSCI USD/Price homepage observations while the old graph route was delayed. Later full-precision originals matched all six after two-decimal rounding and retained C2. Collector v1.0.2 now accepts a verified dated homepage capture; direct server browser acquisition remains blocked by reCAPTCHA, so daily homepage acquisition is still manual. Model v1.0 and the existing API process are unchanged. The 10:00 target was missed, and the one-day follow-up was closed. [Result, timing, tests and limits](../../runs/2026-11-20-msci-homepage/README.md).


## API before 10:00 and timing records, November 19

User decision: retain original indices and target prior-US-session API readiness **before 10:00 KST**. The server now runs Monday-Saturday at **07:40, 08:00, 08:20, 08:40, 08:50, 08:55, 09:20, 09:40, 09:50, 09:55, 09:58, 10:40, 12:20 and 16:20 KST**. The four new 09:40-09:58 slots are deployed. Collection stays serialized through the existing service and writer lock.

All 18 sources now have persistent first-observation records, alongside result publication and authenticated API-ready timestamps. Daily JSON and readable tables are written under `/root/eccles-regime/runtime/timing/`. Source arrival is bounded by polling observations. Failed retrieval, missing dates and stale API responses are distinguished. The recorder uses the existing credentials inside the API container, with no credential transfer or output.

Ten fixture checks and one live refresh passed. The refresh took 15.07 seconds; at 2026-11-19 15:38:32 KST, the API was current for 2026-11-18 and all 18 observation records were present. This is commissioning evidence. Prospective morning observations start **November 20**. Production Global26 + NEON v1.0, source definitions, service image and API process are unchanged. [Deployment and timing records](../../runs/2026-11-19-api-before10/README.md).


## November 19, 2026: API before-09:00 schedule

The existing server timer is enabled and active. Morning runs are 07:40, 08:00, 08:20, 08:40, 08:50 and 08:55 Asia/Seoul, Monday-Saturday. Later retries remain 09:20, 10:40, 12:20 and 16:20. Timer accuracy is one second with no randomized delay. The API serves precomputed results. The first new morning sequence is November 20.

The old 07:20 run targeted the older session in US winter because the current engine waits until scheduled XNYS close plus 90 minutes, or 07:30 KST. All eight winter/summer/weekend/holiday/early-close checks passed. Only the timer changed. The API container, production model v1.0 and service v1.0.1 are preserved.

Authenticated check at 10:06:43 KST: latest November 18, C2, `current`, readiness 200, all 26 NEON entries. Collection and publication took 19.20 seconds. Across the 28 completed attempts inspected from November 13 through this manual check, median duration was 17.28 seconds and maximum 37.78 seconds.

The November 19 09:20 snapshot lacked six original MSCI inputs. They became available by the 10:06 collection. Completion before 09:00 remains dependent on source publication timing. A stricter service commitment needs a qualified earlier channel for those same indices. The API retains the previous dated observation with explicit missing/stale status until complete inputs arrive.

Server rollback: `/root/eccles-regime/runtime/operations/2026-11-19-api-morning/timer-before`, plus `deploy-timer-before`. Restore the timer, reload systemd and restart only `eccles-refresh.timer`. Model and API process need no restart. Evidence: [verification](validation/morning-schedule.json).


## November 16, 2026: historical API coverage

Verified at 16:09 KST. Training 5,391 dates and examined validation 490 dates are now available through the existing date/range endpoints. All 5,881 HTTP results match saved cluster IDs, distances, raw values, model inputs and NEON relevance. English/Korean interpretations are present on every date. Authentication and pagination limits pass; 2025 remains unpublished.

Existing six live records are unchanged. Latest: November 13, C2, data status `current`. API and timer are active. The API container was not restarted. Model v1.0 and service v1.0.1 remain in use. The server's existing VIX source fallback and container network connections were preserved.

32 tests passed against a snapshot of the deployed v1.0.1 source. Historical input rebuild: maximum absolute difference 1.11e-16. Local full import/API check: 9.44 seconds, 267.44 MiB sampled peak RSS. Server import: 14.49 seconds under one CPU and 1 GiB container limits, zero fits. Batch writes are transactional and idempotent, with a pre-import SQLite backup.

Evidence: [import receipt](validation/training-api-import.json), [full server API check](validation/training-api-live.json). Usage: [API history](API.md#training-and-validation-history).

## November 10, 2026: original release

Verified: 2026-11-10, 10:57 KST. **The company-server API and daily timer are running.**

| Item | Verified state |
| --- | --- |
| Original-model replay | 6,090/6,090 labels, all assigned-class relevance reproduced |
| Original-input rebuild | Complete historical and examined 2026 feature rows reproduced |
| Local service checks | 18 passed |
| Linux server service checks | 18 passed |
| Portable model | v1.0, bundle SHA256 `23ee686ab3d4403e48ac887644f1d754ee806246e772ddebfb3313175235f764` |
| Container image | Built, uid 10001, one numerical thread, API 512 MiB, updater 1 GiB limits |
| API | `eccles-api.service`, enabled, running on loopback 18771 |
| Timer | `eccles-refresh.timer`, enabled, active |
| First original-source refresh | Completed successfully on the server |
| Authentication | Unauthenticated latest request 401, authenticated request 200 |
| Readiness | 503, correctly identifies missing November 9 VIX |
| Latest published observation | 2026-11-06, C2, closest alternative C1 |
| API resource sample | 76.08 MiB / 512 MiB, 0.27% CPU at verification |
| Existing services | Telegram receiver, ETF API and Dashboard backend remain active |

The 18 original sources were fetched independently by the server. Seventeen include November 9. The official daily Cboe VIX file ends November 6, so November 9 is withheld and the previous complete result remains available with `missing_inputs`. The updater finished successfully. Later scheduled runs retry the source.

November 6 server distances, NEON contributions and regime reproduce the independent local result. Source manifests and original response bodies are retained on the server. Credentials were transferred after the user's explicit authorization and stored with file mode 0600, excluded from Git and logs.

Daily collection: Monday-Saturday, **07:20, 09:20, 12:20, 16:20 Asia/Seoul**, up to 30 seconds jitter. The next timer shown at verification was November 10, 12:20 KST. The service runs independently of this Mac using the company's existing WSL/Docker host.

Container image: `sha256:db310ca03f4b9662122c70cc4d15cb64d0671f840e21bb152844bfd5a1a6f95b`. The Dockerfile pins the resolved Python 3.12 base digest used for this build.

Evidence: [live API](validation/server-live.json), [Linux tests](validation/server-validation.json). Access, updates and rollback: [API.md](API.md). Existing Telegram and Dashboard deployments were not changed. No Telegram message was sent.
