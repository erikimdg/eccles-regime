# Deployment record

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
