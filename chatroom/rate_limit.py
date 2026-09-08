#!/usr/bin/env python3
"""Small cross-process rate limiter for model-side API calls."""

import json
import time
from pathlib import Path

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import fcntl
except ImportError:
    fcntl = None


DATA_DIR = Path(__file__).resolve().parent / "data"


def _locked(handle):
    if msvcrt is not None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True
    return False


def _unlock(handle):
    if msvcrt is not None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    elif fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def try_acquire(provider="opencode", min_interval_seconds=300):
    """Return True only if a new model call may start now."""
    if min_interval_seconds <= 0:
        return True
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / ("%s_rate_limit.json" % provider)
    now = time.time()
    handle = None
    locked = False
    try:
        handle = path.open("a+b")
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        locked = _locked(handle)
        if not locked:
            return False
        raw = handle.read().decode("ascii", "ignore").strip() or "0"
        try:
            state = json.loads(raw)
            last = float(state.get("last_attempt", 0))
        except Exception:
            last = float(raw or 0)
        if now - last < min_interval_seconds:
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"last_attempt": now}).encode("ascii"))
        handle.flush()
        return True
    except Exception:
        return False
    finally:
        if handle is not None and locked:
            _unlock(handle)
        if handle is not None:
            handle.close()
