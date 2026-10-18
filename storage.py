"""إدارة الحالة والمستخدمين — تخزين دائم في state.json"""
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
    "stats": {"total_videos": 0, "total_users": 0, "total_errors": 0, "providers_used": {}},
    "last_backup": 0,
}

def _load():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            state = deepcopy(DEFAULT_STATE)
            if isinstance(data, dict): state.update(data)
            for k in ("users", "jobs", "cache", "stats"):
                if not isinstance(state.get(k), dict): state[k] = {}
            if not isinstance(state.get("queue"), list): state["queue"] = []
            return state
        except Exception: pass
    return deepcopy(DEFAULT_STATE)

def _save(state):
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except Exception as e: print(f"[storage] save failed: {e}")

_state = _load()

def get_state(): return _state
def save(): _save(_state)

# ═══ المستخدمين ═══
def is_owner(user_id): return _state.get("owner_id") == int(user_id)
def set_owner(user_id): _state["owner_id"] = int(user_id); save()
def is_approved(user_id): return is_owner(user_id) or (_state["users"].get(str(user_id), {}).get("status") == "approved")
def is_banned(user_id): return _state["users"].get(str(user_id), {}).get("status") == "banned"

def add_user(user_id, username=""):
    uid = str(user_id)
    if uid not in _state["users"]:
        _state["users"][uid] = {"status": "pending", "joined": time.time(), "username": username}
        _state["stats"]["total_users"] += 1; save()

def approve_user(user_id):
    if str(user_id) in _state["users"]: _state["users"][str(user_id)]["status"] = "approved"; save()

def ban_user(user_id):
    if str(user_id) in _state["users"]: _state["users"][str(user_id)]["status"] = "banned"; save()

# ═══ الطابور والوظائف ═══
def new_job(user_id, prompt):
    job_id = f"j{int(time.time()*1000)}_{user_id}"
    _state["jobs"][job_id] = {"user_id": user_id, "status": "queued", "prompt": prompt, "created": time.time()}
    _state["queue"].append(job_id)
    save()
    return job_id

def update_job(job_id, **kwargs):
    if job_id in _state["jobs"]: _state["jobs"][job_id].update(kwargs); save()

def get_job(job_id): return _state["jobs"].get(job_id)
def pop_queue():
    if _state["queue"]:
        jid = _state["queue"].pop(0)
        save()
        return jid
    return None

# ═══ الإحصائيات ═══
def record_video(provider):
    _state["stats"]["total_videos"] += 1
    _state["stats"]["providers_used"][provider] = _state["stats"]["providers_used"].get(provider, 0) + 1
    save()
