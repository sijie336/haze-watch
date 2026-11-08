import csv
import io
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, func, delete
from .core import *

@asynccontextmanager
async def lifespan(app):
    init_db()
    yield

app = FastAPI(title='HazeWatch SG API', version='1.0.0', lifespan=lifespan)
Region = Literal['north', 'south', 'east', 'west', 'central']
Metric = Literal['pm25', 'psi']

@app.get('/health/live')
def live():
    return {'status': 'ok'}

@app.get('/health/ready')
def ready():
    with Session() as db:
        db.execute(select(1))
    return {'status': 'ok'}

@app.get('/api/v1/readings/latest')
def readings():
    with Session() as db:
        rows = [r for region in REGIONS for metric in ('pm25', 'psi') if (r := latest(db, region, metric))]
        runs = [db.scalar(select(Run).where(Run.metric == metric).order_by(Run.id.desc()).limit(1))
                for metric in (['demo'] if MODE == 'demo' else ['pm25', 'psi'])]
        run = max((r for r in runs if r), key=lambda r: r.at, default=None)
        return {'mode': MODE, 'readings': [serial(r) for r in rows], 'last_checked': run.at if run else None,
                'source_ok': all(r is not None and r.ok for r in runs) if run else None}

@app.get('/api/v1/readings/history')
def history(region: Region = 'central', metric: Metric = 'pm25', hours: int = Query(24, ge=1, le=744), end: str | None = None):
    try:
        stop = parse(end) if end else now()
    except ValueError:
        raise HTTPException(422, 'Invalid end timestamp; include timezone')
    with Session() as db:
        rows = db.scalars(select(Reading).where(Reading.source == MODE, Reading.region == region,
            Reading.metric == metric, Reading.observed_at >= iso(stop-timedelta(hours=hours)),
            Reading.observed_at <= iso(stop)).order_by(Reading.observed_at)).all()
        vals = [r.value for r in rows]
        threshold = 56 if metric == 'pm25' else 101
        return {'readings': [serial(r) for r in rows], 'summary': {'peak': max(vals) if vals else None,
            'mean': round(sum(vals)/len(vals), 1) if vals else None,
            'observations_above': sum(v >= threshold for v in vals), 'count': len(vals)}}

@app.get('/api/v1/readings/export')
def export(region: Region = 'central', metric: Metric = 'pm25', hours: int = Query(24, ge=1, le=744), end: str | None = None):
    rows = history(region, metric, hours, end)['readings']
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(['mode', 'region', 'metric', 'value', 'observed_at_utc'])
    writer.writerows([MODE, r['region'], r['metric'], r['value'], r['observed_at']] for r in rows)
    return Response(stream.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="haze-readings.csv"'})

class Subscribe(BaseModel):
    email: EmailStr
    region: Region
    metric: Metric = 'pm25'
    threshold: float = Field(56, ge=1, le=1000, allow_inf_nan=False)

class Token(BaseModel):
    token: str = Field(min_length=32, max_length=200)

def resolve(db, token):
    sub = db.scalar(select(Subscription).where(Subscription.token_hash == digest(token)).with_for_update())
    if not sub:
        raise HTTPException(404, 'Invalid management link')
    return sub

@app.post('/api/v1/subscriptions', status_code=202)
def subscribe(body: Subscribe):
    with Session() as db:
        recent = db.scalar(select(func.count()).select_from(Subscription).where(Subscription.created_at >= iso(now()-timedelta(hours=1))))
        per_email = db.scalar(select(func.count()).select_from(Subscription).where(Subscription.email == str(body.email).lower(), Subscription.created_at >= iso(now()-timedelta(hours=1))))
        if recent >= 100 or per_email >= 3:
            raise HTTPException(429, 'Please wait before requesting another subscription')
        token = secrets.token_urlsafe(32)
        sub = Subscription(email=str(body.email).lower(), region=body.region, metric=body.metric,
            threshold=body.threshold, token_hash=digest(token), created_at=iso(now()))
        db.add(sub)
        db.flush()
        link = os.getenv('PUBLIC_URL', 'http://localhost:4200').rstrip('/') + '/#token=' + token
        db.add(Mail(subscription_id=sub.id, recipient=sub.email, subject='Verify your HazeWatch subscription',
            body=f'Confirm your {body.region} {body.metric} warning subscription (threshold {body.threshold}).\nOpen {link}\nVerification expires in 24 hours. Keep this link to manage or unsubscribe. If you did not request this, ignore this email.', next_at=iso(now())))
        db.commit()
    return {'message': 'Check your inbox to verify your subscription.'}

@app.post('/api/v1/subscriptions/verify')
def verify(body: Token):
    with Session() as db:
        sub = resolve(db, body.token)
        if not sub.verified and now()-parse(sub.created_at) > timedelta(hours=24):
            raise HTTPException(410, 'Verification expired; subscribe again')
        sub.verified = True
        r = latest(db, sub.region, sub.metric)
        if r:
            evaluate(db, sub, r)
        db.commit()
    return {'message': 'Subscription verified. Warnings are enabled.'}

@app.post('/api/v1/subscriptions/history')
def events(body: Token):
    with Session() as db:
        sub = resolve(db, body.token)
        rows = db.scalars(select(Event).where(Event.subscription_id == sub.id).order_by(Event.id.desc()).limit(100))
        return {'region': sub.region, 'metric': sub.metric, 'threshold': sub.threshold, 'verified': sub.verified,
                'events': [dict(kind=r.kind, value=r.value, observed_at=r.observed_at) for r in rows]}

@app.post('/api/v1/subscriptions/unsubscribe')
def unsubscribe(body: Token):
    with Session() as db:
        sub = resolve(db, body.token)
        db.execute(delete(Mail).where(Mail.subscription_id == sub.id))
        db.execute(delete(Event).where(Event.subscription_id == sub.id))
        db.delete(sub)
        db.commit()
    return {'message': 'Unsubscribed. Your subscription and delivery records were deleted.'}
