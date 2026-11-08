"""Shared persistence, source ingestion, and warning rules."""
import hashlib
import math
import os
import time
from datetime import datetime, timezone, timedelta

import httpx
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, UniqueConstraint, select
from sqlalchemy.orm import declarative_base, sessionmaker

REGIONS = ['north', 'south', 'east', 'west', 'central']
MODE = os.getenv('DATA_MODE', 'demo')
if MODE not in ('live', 'demo'):
    raise RuntimeError('DATA_MODE must be live or demo')
URL = os.getenv('DATABASE_URL', 'sqlite:///./haze.db')
engine = create_engine(URL, connect_args={'check_same_thread': False} if URL.startswith('sqlite') else {}, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()

def now():
    return datetime.now(timezone.utc)

def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()

def parse(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timestamp must include a time zone')
    return dt.astimezone(timezone.utc)

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def level(metric, value):
    bounds = [55, 150, 250] if metric == 'pm25' else [50, 100, 200, 300]
    return sum(value > bound for bound in bounds)

def label(metric, value):
    names = ['Normal', 'Elevated', 'High', 'Very high'] if metric == 'pm25' else ['Good', 'Moderate', 'Unhealthy', 'Very unhealthy', 'Hazardous']
    return names[level(metric, value)]

class Reading(Base):
    __tablename__ = 'readings'
    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)
    region = Column(String, nullable=False, index=True)
    metric = Column(String, nullable=False)
    value = Column(Float, nullable=False)
    observed_at = Column(String, nullable=False, index=True)
    fetched_at = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint('source', 'region', 'metric', 'observed_at'),)

class Subscription(Base):
    __tablename__ = 'subscriptions'
    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False)
    region = Column(String, nullable=False)
    metric = Column(String, nullable=False)
    threshold = Column(Float, nullable=False)
    token_hash = Column(String, nullable=False, unique=True)
    created_at = Column(String, nullable=False)
    verified = Column(Boolean, default=False)
    active = Column(Boolean, default=False)
    severity = Column(Integer, default=-1)
    below_count = Column(Integer, default=0)
    last_at = Column(String, default='')

class Event(Base):
    __tablename__ = 'events'
    id = Column(Integer, primary_key=True)
    subscription_id = Column(Integer, nullable=False, index=True)
    region = Column(String, nullable=False)
    metric = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    value = Column(Float, nullable=False)
    observed_at = Column(String, nullable=False)
    created_at = Column(String, nullable=False)

class Mail(Base):
    __tablename__ = 'outbox'
    id = Column(Integer, primary_key=True)
    subscription_id = Column(Integer, nullable=False)
    recipient = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    body = Column(String, nullable=False)
    status = Column(String, default='pending')
    attempts = Column(Integer, default=0)
    next_at = Column(String, nullable=False)

class Run(Base):
    __tablename__ = 'ingestion_runs'
    id = Column(Integer, primary_key=True)
    metric = Column(String, nullable=False)
    at = Column(String, nullable=False)
    ok = Column(Boolean, nullable=False)
    detail = Column(String, nullable=False)

def init_db():
    Base.metadata.create_all(engine)

def serial(r):
    return dict(region=r.region, metric=r.metric, value=r.value, observed_at=r.observed_at,
                fetched_at=r.fetched_at, status=label(r.metric, r.value),
                stale=(now() - parse(r.observed_at)).total_seconds() > 7200)

def latest(db, region, metric):
    return db.scalar(select(Reading).where(Reading.source == MODE, Reading.region == region, Reading.metric == metric).order_by(Reading.observed_at.desc()).limit(1))

def evaluate(db, sub, r):
    """New fresh observations only; a repeated poll cannot advance recovery."""
    if not sub.verified or r.observed_at <= (sub.last_at or ''):
        return
    if not -300 <= (now() - parse(r.observed_at)).total_seconds() <= 7200:
        return
    sub.last_at = r.observed_at
    severity = level(r.metric, r.value)
    kind = None
    if r.value >= sub.threshold:
        if not sub.active:
            kind = 'warning'
        elif severity > sub.severity:
            kind = 'escalation'
        sub.active, sub.severity, sub.below_count = True, severity, 0
    elif sub.active:
        sub.below_count += 1
        if sub.below_count >= 2:
            kind = 'recovery'
            sub.active, sub.severity, sub.below_count = False, -1, 0
    if kind:
        event = Event(subscription_id=sub.id, region=r.region, metric=r.metric, kind=kind,
                      value=r.value, observed_at=r.observed_at, created_at=iso(now()))
        db.add(event)
        db.flush()
        prefix = '[DEMO] ' if MODE == 'demo' else ''
        db.add(Mail(subscription_id=sub.id, recipient=sub.email,
                    subject=f'{prefix}HazeWatch: {kind} in {r.region.title()}',
                    body=f'{r.metric.upper()}: {r.value} {"µg/m³" if r.metric == "pm25" else "PSI"}. Observed {r.observed_at}.\nThreshold: {sub.threshold}.\nEvent {event.id}.\nView {os.getenv("PUBLIC_URL", "http://localhost:4200")}\nOfficial guidance: https://www.haze.gov.sg/\nUse your original verification email to manage or unsubscribe.', next_at=iso(now())))

def save_reading(db, region, metric, value, timestamp, evaluate_rules=True):
    if region not in REGIONS or metric not in ('pm25', 'psi') or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid observation')
    observed = parse(timestamp)
    if observed > now() + timedelta(minutes=5):
        raise ValueError('Future observation')
    timestamp = iso(observed)
    old = db.scalar(select(Reading).where(Reading.source == MODE, Reading.region == region, Reading.metric == metric, Reading.observed_at == timestamp))
    if old:
        return False
    r = Reading(source=MODE, region=region, metric=metric, value=value, observed_at=timestamp, fetched_at=iso(now()))
    db.add(r)
    db.flush()
    if evaluate_rules:
        for sub in db.scalars(select(Subscription).where(Subscription.verified.is_(True), Subscription.region == region, Subscription.metric == metric).with_for_update()):
            evaluate(db, sub, r)
    return True

def decode(payload, metric):
    if payload.get('code', 0) != 0:
        raise ValueError('Upstream returned an error code')
    data = payload.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise ValueError('Unexpected upstream schema')
    rows = []
    for item in data['items']:
        values = item['readings']['pm25_one_hourly' if metric == 'pm25' else 'psi_twenty_four_hourly']
        for region in REGIONS:
            if region in values:
                rows.append((region, metric, values[region], item['timestamp']))
    return sorted(rows, key=lambda r: parse(r[3]))

def ingest_live(db, metric, date=None):
    token = None
    seen = set()
    count = 0
    with httpx.Client(timeout=20, headers={'x-api-key': os.getenv('DATA_GOV_API_KEY', '')}) as client:
        while True:
            params = {'date': date} if date else {}
            if token:
                params['paginationToken'] = token
            for attempt in range(3):
                try:
                    response = client.get(f'https://api-open.data.gov.sg/v2/real-time/api/{metric}', params=params)
                    response.raise_for_status()
                    break
                except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in (429, 500, 502, 503, 504):
                        raise
                    if attempt == 2:
                        raise
                    delay = 2 ** attempt
                    if isinstance(exc, httpx.HTTPStatusError):
                        retry = exc.response.headers.get('Retry-After', '')
                        if retry and not retry.isdigit():
                            raise
                        if retry.isdigit():
                            if int(retry) > 60:
                                raise
                            delay = max(delay, int(retry))
                    time.sleep(delay)
            payload = response.json()
            for row in decode(payload, metric):
                count += save_reading(db, *row, evaluate_rules=date is None)
            token = payload['data'].get('paginationToken')
            if not token:
                return count
            if token in seen:
                raise ValueError('Repeated upstream pagination token')
            seen.add(token)

def ingest_demo(db):
    end = now().replace(minute=0, second=0, microsecond=0)
    # Stable hourly fixtures; only new current observations trigger warnings.
    for hours in range(167, -1, -1):
        stamp = end - timedelta(hours=hours)
        phase = stamp.timestamp() / 3600
        for i, region in enumerate(REGIONS):
            pm = round(max(4, 35 + i * 8 + 25 * math.sin(phase / 4 + i / 2)))
            for metric, value in [('pm25', pm), ('psi', round(38 + i * 6 + 10 * math.sin(phase / 12)))]:
                save_reading(db, region, metric, value, iso(stamp), evaluate_rules=hours == 0)
