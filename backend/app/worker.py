"""Run exactly one worker: ingestion + transactional email outbox delivery."""
import argparse
import logging
import smtplib
import time
from email.message import EmailMessage
from .core import *

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('haze-worker')

def deliver():
    with Session() as db:
        rows = db.scalars(select(Mail).where(Mail.status == 'pending', Mail.next_at <= iso(now())).limit(50)).all()
        for row in rows:
            try:
                message = EmailMessage()
                message['From'] = os.getenv('SMTP_FROM', 'alerts@hazewatch.local')
                message['To'] = row.recipient
                message['Subject'] = row.subject
                message['Message-ID'] = f'<hazewatch-{row.id}@{os.getenv("MAIL_ID_DOMAIN", "hazewatch.local")}>'
                message.set_content(row.body)
                with smtplib.SMTP(os.getenv('SMTP_HOST', 'localhost'), int(os.getenv('SMTP_PORT', '1025')), timeout=15) as smtp:
                    if os.getenv('SMTP_TLS', 'false').lower() == 'true':
                        smtp.starttls()
                    if os.getenv('SMTP_USER'):
                        smtp.login(os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'])
                    smtp.send_message(message)
                row.status = 'sent'
                row.body = '[delivered]'
            except Exception as exc:
                row.attempts += 1
                row.next_at = iso(now()+timedelta(seconds=min(3600, 30 * 2**row.attempts)))
                if row.attempts >= 8:
                    row.status = 'failed'
                log.warning('Mail %s attempt %s failed (%s)', row.id, row.attempts, type(exc).__name__)
            db.commit()

def ingest():
    for metric in (['demo'] if MODE == 'demo' else ['pm25', 'psi']):
        with Session() as db:
            try:
                ingest_demo(db) if MODE == 'demo' else ingest_live(db, metric)
                db.add(Run(metric=metric, at=iso(now()), ok=True, detail='complete'))
                db.commit()
            except Exception as exc:
                db.rollback()
                db.add(Run(metric=metric, at=iso(now()), ok=False, detail=type(exc).__name__))
                db.commit()
                log.warning('Ingestion failed for %s (%s)', metric, type(exc).__name__)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--backfill', help='Live historical date YYYY-MM-DD; never sends warnings')
    args = parser.parse_args()
    init_db()
    if args.backfill:
        if MODE != 'live':
            parser.error('Backfill requires DATA_MODE=live')
        with Session() as db:
            for metric in ('pm25', 'psi'):
                ingest_live(db, metric, args.backfill)
            db.commit()
    else:
        next_ingest = 0
        while True:
            if time.monotonic() >= next_ingest:
                ingest()
                next_ingest = time.monotonic() + int(os.getenv('POLL_SECONDS', '900'))
            deliver()
            if args.once:
                break
            time.sleep(10)
