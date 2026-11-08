# HazeWatch SG

An Angular 21 + Python FastAPI haze monitoring and application for Singapore. It integrates NEA's PM2.5 and PSI APIs on data.gov.sg

Open:

- Dashboard: http://localhost:4200
- API documentation: http://localhost:8000/docs
- Local test inbox: http://localhost:8025

The default demo mode seeds seven days of simulated hourly observations. Select a region, explore a chart, download CSV.
The single background worker polls every 900 seconds. It separately fetches:

- https://api-open.data.gov.sg/v2/real-time/api/pm25
- https://api-open.data.gov.sg/v2/real-time/api/psi


Python 3.12 and Node 24 are supported. From the project root:

```bash
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
PUBLIC_URL=http://localhost:3000 .venv/bin/python scripts/serve.py
```

Open http://localhost:3000


## Features

- Responsive regional dashboard with a keyboard-accessible schematic map.
- Separate 1-hour PM2.5 and 24-hour PSI readings, source timestamps, stale-data flags.
- Region, metric, time-range and end-date filters; SVG charts, accessible data table, CSV export.
- Summary peak, observed mean, number of readings above threshold.
- Transactional outbox, bounded email retries, source retries, duplicate observation protection.
- Health checks and ingestion logs.

## Documentation

PM2.5 source: https://data.gov.sg/datasets/d_e1058d6974c877257e32048ab128ad83/view

PSI source: https://data.gov.sg/datasets/d_fe37906a0182569d891506e815e819b7/view
