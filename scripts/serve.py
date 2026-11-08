"""Local no-Docker launcher; build the Angular frontend before running."""
import os
import signal
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['PYTHONPATH'] = str(ROOT / 'backend')
from app.core import init_db, Session, MODE, ingest_demo
from app.main import app
from fastapi.staticfiles import StaticFiles
import uvicorn
init_db()
if MODE == 'demo':
    with Session() as db:
        ingest_demo(db)
        db.commit()
app.mount('/', StaticFiles(directory=ROOT / 'frontend/dist/haze-watch/browser', html=True), name='frontend')
worker = subprocess.Popen([sys.executable, '-m', 'app.worker'], cwd=ROOT)
try:
    uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '3000')), access_log=False)
finally:
    worker.terminate()
    worker.wait(timeout=20)
