"""Storage Engine Pro"""
import json
import os
import time
from pathlib import Path
from copy import deepcopy

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

STATE_FILE = DATA_DIR / "state.json"
BACKUP_FILE = DATA_DIR / "state_backup.json"

DEFAULT_STATE = {
    "owner_id": 0,
    "users": {},
    "jobs": {},
    "queue": [],
    "cache": {},

    "stats": {
        "total_videos": 0,
        "total_users": 0,
        "total_errors": 0,

        "providers_used": {},

        "provider_success": {},
        "provider_failed": {},

        "last_success": 0,
        "last_error": 0
    },

    "last_backup": 0,
}


def _load():
    if STATE_FILE.exists():
        try:
            data = json.loads(
                STATE_FILE.read_text(
                    encoding="utf-8"
                )
            )

            state = deepcopy(DEFAULT_STATE)

            if isinstance(data, dict):
                state.update(data)

            return state

        except Exception:
            pass

    return deepcopy(DEFAULT_STATE)


def _save(state):

    try:

        tmp = STATE_FILE.with_suffix(".tmp")

        tmp.write_text(
            json.dumps(
                state,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        os.replace(tmp, STATE_FILE)

    except Exception as e:

        print(
            f"[storage] save failed: {e}"
        )


_state = _load()


def save():
    _save(_state)


def get_state():
    return _state


# ========= OWNER =========

def is_owner(user_id):
    return _state["owner_id"] == int(user_id)


def set_owner(user_id):
    _state["owner_id"] = int(user_id)
    save()


# ========= USERS =========

def add_user(user_id, username=""):

    uid = str(user_id)

    if uid not in _state["users"]:

        _state["users"][uid] = {
            "status": "pending",
            "joined": time.time(),
            "username": username
        }

        _state["stats"]["total_users"] += 1

        save()


def approve_user(user_id):

    uid = str(user_id)

    if uid in _state["users"]:

        _state["users"][uid]["status"] = "approved"

        save()


def ban_user(user_id):

    uid = str(user_id)

    if uid in _state["users"]:

        _state["users"][uid]["status"] = "banned"

        save()


def is_approved(user_id):

    uid = str(user_id)

    return (
        is_owner(user_id)
        or
        _state["users"].get(
            uid,
            {}
        ).get(
            "status"
        ) == "approved"
    )


# ========= JOBS =========

def new_job(user_id, prompt):

    job_id = (
        f"j{int(time.time()*1000)}"
        f"_{user_id}"
    )

    _state["jobs"][job_id] = {
        "user_id": user_id,
        "prompt": prompt,
        "status": "queued",
        "created": time.time()
    }

    _state["queue"].append(job_id)

    save()

    return job_id


def update_job(job_id, **kwargs):

    if job_id in _state["jobs"]:

        _state["jobs"][job_id].update(kwargs)

        save()


def get_job(job_id):
    return _state["jobs"].get(job_id)


def pop_queue():

    if not _state["queue"]:
        return None

    jid = _state["queue"].pop(0)

    save()

    return jid


# ========= PROVIDERS =========

def record_video(provider):

    stats = _state["stats"]

    stats["total_videos"] += 1

    stats["last_success"] = time.time()

    stats["providers_used"][provider] = (
        stats["providers_used"].get(
            provider,
            0
        ) + 1
    )

    stats["provider_success"][provider] = (
        stats["provider_success"].get(
            provider,
            0
        ) + 1
    )

    save()


def record_error(provider="unknown"):

    stats = _state["stats"]

    stats["total_errors"] += 1

    stats["last_error"] = time.time()

    stats["provider_failed"][provider] = (
        stats["provider_failed"].get(
            provider,
            0
        ) + 1
    )

    save()


# ========= BACKUPS =========

def backup():

    try:

        if STATE_FILE.exists():

            BACKUP_FILE.write_text(
                STATE_FILE.read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8"
            )

            _state["last_backup"] = time.time()

            save()

            return True

    except Exception:

        pass

    return False
