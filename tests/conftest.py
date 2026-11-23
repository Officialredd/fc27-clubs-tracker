"""pytest runs every test file in one process: give each file its own empty database and state files."""
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True, scope="module")
def _fresh_data_dir():
    from clubs import cloud, db, sync
    fresh = Path(tempfile.mkdtemp())
    saved = db.DB_PATH, cloud.STATE_PATH, sync.STATE_PATH
    db.DB_PATH, cloud.STATE_PATH, sync.STATE_PATH = fresh / "clubs.db", fresh / "online_state.json", fresh / "sync_state.json"
    db.init_db()
    yield
    db.DB_PATH, cloud.STATE_PATH, sync.STATE_PATH = saved
