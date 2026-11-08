# Architecture and behaviour

## Requirement mapping

| Requirement | Implementation |
|---|---|
| Public data.gov.sg dataset | Live PM2.5 and PSI adapters in backend/app/core.py |
| Clear user interface | Angular standalone component, responsive CSS, regional map and cards |
| Data exploration | Regional selection, metric switch, date/range filters, chart, table, CSV |
| Optional insights | Peak, observed mean, threshold exceedance count |
| Frontend/backend design | Angular HTTP client via same-origin proxy; FastAPI API and separate worker |
| AWS IaC | Terraform in infra; optional single EC2 host with PostgreSQL and HTTPS |
| Automated testing | pytest unit and API integration tests; Angular strict compilation |
| Local execution | Docker Compose; optional Python launcher and SQLite |
| Technical documentation | README, architecture, infra operations, OpenAPI |

## Components

Browser → Nginx Angular application → FastAPI → PostgreSQL.

A single worker fetches data.gov.sg, writes validated readings, evaluates subscriptions, and dispatches the database outbox through SMTP. API requests do not call the upstream API. Nginx and the backend use the same origin, so CORS is unnecessary. In AWS, Caddy terminates TLS in front of Nginx. Locally, Mailpit captures email.

## Data model

`readings`: source, region, metric, value, observed_at, fetched_at. The source/region/metric/observation timestamp has a unique constraint. Upstream corrections to an existing timestamp are currently ignored; they are not treated as new observations.

`subscriptions`: email, selected region/metric/threshold, token hash, creation time, verification status, active warning state, severity, recovery count, last evaluated observation time.

`events`: subscription identifier, kind, metric, region, value, observation and creation timestamps.

`outbox`: recipient, subject, message, status, attempt count, next attempt timestamp. Sent bodies are redacted. Pending verification bodies contain the management token; protect database access and backups.

`ingestion_runs`: source metric, run timestamp, status, failure class. Details intentionally omit source payloads and recipient data.

This initial version creates schema idempotently at API startup using SQLAlchemy metadata. There is no upgrade migration chain yet; introduce Alembic migrations before modifying a deployed schema. API becomes healthy before the worker starts, preventing startup table creation races in Compose.

## Classification and state machine

PM2.5 bands: ≤55 Normal, >55–150 Elevated, >150–250 High, >250 Very high. PSI bands: ≤50 Good, >50–100 Moderate, >100–200 Unhealthy, >200–300 Very unhealthy, >300 Hazardous. Data.gov.sg supplies integer readings; the inequality implementation also has no gaps for decimal input.

Default subscription triggers: PM2.5 >=56, PSI >=101. These are configurable application warnings, not an official warning service.

A new fresh observation at or above the threshold starts a warning. A higher band while active triggers escalation. Two consecutive evaluated observations below the threshold cause recovery. Repeated polls, unverified subscriptions, observations older than two hours, and out-of-order observations cannot advance the state. Missing observations never count as recovery. This policy counts successive available fresh observations, not necessarily uninterrupted hourly coverage.

On verification, the latest fresh observation is evaluated once. Historical backfills explicitly disable evaluation. State, event, and outbox entry commit together; source failures roll back the ingestion transaction. PostgreSQL row locks serialize updates of subscription state. Deploy exactly one worker; database unique constraints reject concurrent duplicate inserts rather than silently duplicating warnings.

SMTP is at-least-once. A crash after acceptance but before marking the outbox row sent can cause duplicate delivery; stable Message-ID helps email clients but does not guarantee exactly-once delivery. Failures retry with exponential delay, capped at one hour, and become failed after eight attempts. Monitor failed outbox rows. Unsubscribe deletes stored subscription, events, and queued mail; mail already in flight cannot be recalled.

## Source and freshness rules

Fetch PM2.5 and PSI independently. Retry transient transport errors and 429/selected 5xx responses three times with exponential delay, honouring numeric Retry-After up to 60 seconds. Longer or non-numeric Retry-After values are deferred to the next periodic poll rather than blocking the worker. No UI makes freshness claims from poll time; staleness uses source observation time.

Reject negative, non-finite, boolean, string and null values, unknown regions, naive timestamps and timestamps more than five minutes in the future. Missing regions remain missing. Canonical UTC ISO timestamps sort chronologically in storage. UI dates use Asia/Singapore. Date-filter requests use Singapore end-of-day explicitly.

Historical charts do not connect gaps over 90 minutes. Summaries count available observations, not exposure hours. The chart unit and label switch together; PSI is not computed from PM2.5. A maximum 744-hour query window bounds API responses.

## Security and limitations

Email verification is required before warning delivery. A random 256-bit management token is sent in the URL fragment, not in a request query or access log. The API receives it in a POST body and stores its SHA-256 digest. Unverified tokens expire after 24 hours for activation; management access remains available for verified subscriptions. Keep links private.

Basic per-email and global subscription throttles plus Nginx per-IP limits reduce misuse. These are suitable for an assessment, not a substitute for production abuse controls. No user account system, token rotation, administrator dashboard or delivery-status UI is included. Alert history shows events, not SMTP delivery proof.

The single-host AWS option has a host-level failure domain and no zero-downtime rollout. PostgreSQL data lives on an encrypted persistent host volume; snapshots are crash-consistent, not application-consistent backups. Add pg_dump backups and test restores before depending on it. Pin container image digests and run dependency/security scans before production. Secrets Manager configuration uses the default AWS-managed key unless additional KMS permissions are added.
