# Verification record

Verified on 7 November 2026 using Python 3.12 and Node 24.

| Check | Result |
|---|---|
| Python unit and API tests | 33 passed |
| Angular production build | Passed; strict template/type compilation |
| Python compilation | Passed |
| Actual PM2.5 source ingestion | Passed; five regional observations stored |
| Actual PSI source ingestion | Passed; five regional observations stored |
| Built Angular assets with FastAPI | HTTP 200 for index and main.js |
| Demo ingestion and history | Ten latest readings; 168 hourly readings for a region |
| Compose YAML | Parsed and checked for service wiring |
| Docker/PostgreSQL integration | Not run: Docker unavailable in authoring environment |
| Browser visual/interaction review | Not run: preview relay unavailable |
| Terraform formatting and provider initialization | Passed |
| Terraform provider-schema validation | Blocked: environment prohibits provider Unix sockets |
| AWS provisioning | Not run; no AWS resources created |

Tests use temporary SQLite databases and mock SMTP delivery; no real email was sent. The CI workflow includes Compose startup and a live local API smoke check, but the workflow itself has not been executed on GitHub. Container images, production SMTP, DNS/HTTPS bootstrapping and AWS operations require validation in the target environment.
