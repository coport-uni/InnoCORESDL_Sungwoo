### Smoke test — http://127.0.0.1:17060

| UTC | check | request | HTTP | s | verdict | observed |
|---|---|---|---|---|---|---|
| 2026-07-28T11:13:49+00:00 | health | `GET /v1/health {}` | 200 | 0.0 | pass | - |
| 2026-07-28T11:13:49+00:00 | diagnose | `GET /v1/diagnose {}` | 200 | 0.18 | pass | - |
| 2026-07-28T11:13:49+00:00 | status | `GET /v1/status {}` | 200 | 0.03 | pass | - |
