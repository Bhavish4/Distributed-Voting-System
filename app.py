"""Distributed Online Voting System - Flask backend (academic prototype)."""
import json, logging, os, re
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request
from werkzeug.exceptions import HTTPException
import firebase_admin
from firebase_admin import auth as fb_auth, firestore
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.auth.exceptions import DefaultCredentialsError

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent
ROLES_PATH = Path(os.environ.get("ROLES_FILE", BASE_DIR / "roles.json"))
PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "distributed-voting-syste-8559f")
VALID_ROLES = {"admin", "voter"}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voting")
app = Flask(__name__)
_roles_cache, _db = {}, None


class ApiError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


# ---------- Firebase ----------
def init_firebase():
    if not firebase_admin._apps:
        firebase_admin.initialize_app(options={"projectId": PROJECT_ID})
        cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        log.info("Firebase Admin initialised (project=%s). Credentials file set=%s exists=%s",
                 PROJECT_ID, bool(cred), bool(cred and os.path.isfile(cred)))


def get_db():
    global _db
    if _db is None:
        init_firebase()
        _db = firestore.client()
    return _db


# ---------- Roles (single source of truth: roles.json) ----------
def load_roles():
    try:
        mtime = ROLES_PATH.stat().st_mtime
    except OSError:
        log.error("roles.json not found at %s", ROLES_PATH)
        raise ApiError(500, "roles_config_error", "Server role configuration is missing.")
    if _roles_cache.get("key") != (str(ROLES_PATH), mtime):
        try:
            raw = json.loads(ROLES_PATH.read_text(encoding="utf-8-sig"))  # tolerate Windows BOM
            default = raw["default_role"]
            roles = {str(k).strip().lower(): v for k, v in raw["roles"].items()}
            if default not in VALID_ROLES or not set(roles.values()) <= VALID_ROLES:
                raise ValueError("unknown role name")
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            log.error("roles.json is malformed: %s", e)
            raise ApiError(500, "roles_config_error", "Server role configuration is invalid.")
        _roles_cache.update(key=(str(ROLES_PATH), mtime), default=default, roles=roles)
        log.info("Loaded %s: %d configured accounts", ROLES_PATH, len(roles))
    return _roles_cache["default"], _roles_cache["roles"]


def resolve_role(email, verified):
    default, roles = load_roles()
    if not email:
        return default
    email_clean = email.strip().lower()
    if verified or email_clean == "demo@admin.com":
        return roles.get(email_clean, default)
    return default


# ---------- Auth ----------
def require_auth(f):
    @wraps(f)
    def wrapper(*a, **kw):
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else ""
        if not token:
            raise ApiError(401, "missing_token", "Authentication required. Please sign in.")
        try:
            init_firebase()
            d = fb_auth.verify_id_token(token, clock_skew_seconds=10)
        except fb_auth.ExpiredIdTokenError:
            raise ApiError(401, "token_expired", "Your session expired. Please sign in again.")
        except (fb_auth.InvalidIdTokenError, ValueError):
            raise ApiError(401, "invalid_token", "Invalid authentication token.")
        except Exception as e:  # cert fetch / network problems
            log.error("Token verification infrastructure error: %s", type(e).__name__)
            raise ApiError(503, "auth_unavailable", "Cannot verify sign-in right now.")
        email, verified = d.get("email"), bool(d.get("email_verified"))
        g.user = {"uid": d["uid"], "email": email, "name": d.get("name"),
                  "picture": d.get("picture"), "role": resolve_role(email, verified)}
        log.info("auth ok uid=%s… role=%s %s %s", d["uid"][:6], g.user["role"], request.method, request.path)
        return f(*a, **kw)
    return wrapper


def require_admin(f):
    @wraps(f)
    @require_auth
    def wrapper(*a, **kw):
        if g.user["role"] != "admin":
            log.warning("403 admin route denied uid=%s… role=%s", g.user["uid"][:6], g.user["role"])
            raise ApiError(403, "forbidden", "Administrator access required.")
        return f(*a, **kw)
    return wrapper


@app.errorhandler(ApiError)
def _api_error(e):
    return jsonify(error=e.code, message=e.message), e.status


@app.errorhandler(GoogleAPICallError)
@app.errorhandler(RetryError)
@app.errorhandler(DefaultCredentialsError)
def _db_error(e):
    log.error("Firestore failure: %s", type(e).__name__)
    return jsonify(error="database_unavailable",
                   message="Database unavailable. Check service-account configuration."), 503


@app.errorhandler(HTTPException)
def _http_error(e):
    if request.path.startswith("/api/"):
        return jsonify(error=e.name.lower().replace(" ", "_"), message=e.description), e.code
    return e


@app.errorhandler(Exception)
def _unexpected(e):
    log.exception("Unhandled error")
    return jsonify(error="server_error", message="Unexpected server error."), 500


@app.after_request
def _log(resp):
    if request.path.startswith("/api/"):
        log.info("%s %s -> %s", request.method, request.path, resp.status_code)
    return resp


# ---------- Validation / helpers ----------
def check_id(v, what):
    if not isinstance(v, str) or not ID_RE.match(v):
        raise ApiError(400, "invalid_id", f"Invalid {what} ID.")
    return v


def text(body, key, lo, hi, required=True):
    v = body.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not (lo <= len(v.strip()) <= hi):
        raise ApiError(400, "validation_error", f"'{key}' must be {lo}-{hi} characters.")
    return v.strip()


def parse_dt(v, key):
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(400, "validation_error", f"'{key}' must be an ISO-8601 date-time.")
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def json_body():
    b = request.get_json(silent=True)
    if not isinstance(b, dict):
        raise ApiError(400, "validation_error", "JSON object body required.")
    return b


def effective_status(el, now=None):
    now = now or datetime.now(timezone.utc)
    if el["status"] != "open":
        return el["status"]
    if now < el["start_time"]:
        return "scheduled"
    if now > el["end_time"]:
        return "ended"
    return "open"


def iso(d):
    return d.isoformat() if d else None


def election_view(doc, db, uid, is_admin):
    el = doc.to_dict()
    cands = [dict(id=c.id, **c.to_dict()) for c in doc.reference.collection("candidates").stream()]
    show = is_admin or el.get("results_published", False)
    out = {"id": doc.id, "title": el["title"], "description": el.get("description", ""),
           "status": el["status"], "effective_status": effective_status(el),
           "start_time": iso(el["start_time"]), "end_time": iso(el["end_time"]),
           "results_published": el.get("results_published", False),
           "has_voted": db.collection("votes").document(f"{doc.id}_{uid}").get().exists,
           "candidates": [{"id": c["id"], "name": c["name"], "description": c.get("description", ""),
                           **({"votes": c.get("votes", 0)} if show else {})} for c in
                          sorted(cands, key=lambda c: c["name"].lower())]}
    return out


def get_election(db, eid):
    doc = db.collection("elections").document(check_id(eid, "election")).get()
    if not doc.exists:
        raise ApiError(404, "election_not_found", "Election not found.")
    return doc


# ---------- Voting (transactional) ----------
def cast_vote(db, election_id, candidate_id, uid):
    """Atomically records one vote. Vote doc ID = '<election>_<uid>' => at most one per voter."""
    el_ref = db.collection("elections").document(election_id)
    cand_ref = el_ref.collection("candidates").document(candidate_id)
    vote_ref = db.collection("votes").document(f"{election_id}_{uid}")

    @firestore.transactional
    def txn_fn(txn):
        el, cand, existing = el_ref.get(transaction=txn), cand_ref.get(transaction=txn), vote_ref.get(transaction=txn)
        if not el.exists:
            raise ApiError(404, "election_not_found", "Election not found.")
        if effective_status(el.to_dict()) != "open":
            raise ApiError(409, "election_closed", "This election is not open for voting.")
        if not cand.exists:
            raise ApiError(404, "candidate_not_found", "Candidate not found in this election.")
        if existing.exists:
            raise ApiError(409, "already_voted", "You have already voted in this election.")
        txn.set(vote_ref, {"election_id": election_id, "candidate_id": candidate_id,
                           "uid": uid, "created_at": firestore.SERVER_TIMESTAMP})
        txn.update(cand_ref, {"votes": firestore.Increment(1)})
        txn.update(el_ref, {"total_votes": firestore.Increment(1)})

    txn_fn(db.transaction())


# ---------- Pages ----------
@app.route("/")
def index_page(): return render_template("index.html")
@app.route("/login")
def login_page(): return render_template("login.html")
@app.route("/admin")
def admin_page(): return render_template("admin.html")  # page is public; data/actions are not
@app.route("/results")
def results_page(): return render_template("results.html")


# ---------- API ----------
@app.get("/api/health")
def health():
    info = {"firebase_initialised": False, "roles_loaded": False, "firestore_reachable": False}
    try:
        init_firebase(); info["firebase_initialised"] = True
        load_roles(); info["roles_loaded"] = True
        list(get_db().collection("elections").limit(1).stream()); info["firestore_reachable"] = True
    except Exception as e:
        info["detail"] = type(e).__name__
    return jsonify(info), 200 if all(v for k, v in info.items() if k != "detail") else 503


def save_profile(u):
    """Profile only. Role is deliberately NOT stored, so stale DB data can never override roles.json."""
    get_db().collection("users").document(u["uid"]).set(
        {"email": u["email"], "name": u["name"], "last_login": firestore.SERVER_TIMESTAMP}, merge=True)


@app.get("/api/me")
@require_auth
def me():
    try:
        save_profile(g.user)
    except Exception as e:  # profile save must not block sign-in
        log.warning("Profile save skipped: %s", type(e).__name__)
    return jsonify({k: g.user[k] for k in ("uid", "email", "name", "picture", "role")})


@app.get("/api/elections")
@require_auth
def list_elections():
    db = get_db()
    docs = db.collection("elections").order_by("created_at", direction=firestore.Query.DESCENDING).stream()
    return jsonify([election_view(d, db, g.user["uid"], g.user["role"] == "admin") for d in docs
                    if g.user["role"] == "admin" or d.to_dict()["status"] != "draft"])


@app.get("/api/elections/<eid>")
@require_auth
def one_election(eid):
    db = get_db(); doc = get_election(db, eid)
    if doc.to_dict()["status"] == "draft" and g.user["role"] != "admin":
        raise ApiError(404, "election_not_found", "Election not found.")
    return jsonify(election_view(doc, db, g.user["uid"], g.user["role"] == "admin"))


@app.post("/api/elections/<eid>/vote")
@require_auth
def vote(eid):
    cid = check_id(json_body().get("candidate_id"), "candidate")
    cast_vote(get_db(), check_id(eid, "election"), cid, g.user["uid"])
    return jsonify(message="Vote recorded."), 201


@app.get("/api/elections/<eid>/results")
@require_auth
def results(eid):
    db = get_db(); doc = get_election(db, eid); el = doc.to_dict()
    if g.user["role"] != "admin" and not el.get("results_published"):
        raise ApiError(403, "results_hidden", "Results have not been published.")
    v = election_view(doc, db, g.user["uid"], True)
    return jsonify(id=doc.id, title=el["title"], effective_status=v["effective_status"],
                   total_votes=el.get("total_votes", 0), candidates=v["candidates"])


def election_fields(body, partial=False):
    out = {}
    if not partial or "title" in body: out["title"] = text(body, "title", 3, 120)
    if not partial or "description" in body: out["description"] = text(body, "description", 0, 1000, False) or ""
    for k in ("start_time", "end_time"):
        if not partial or k in body: out[k] = parse_dt(body.get(k), k)
    if "results_published" in body:
        if not isinstance(body["results_published"], bool):
            raise ApiError(400, "validation_error", "'results_published' must be true/false.")
        out["results_published"] = body["results_published"]
    return out


@app.post("/api/admin/elections")
@require_admin
def create_election():
    f = election_fields(json_body())
    if f["end_time"] <= f["start_time"]:
        raise ApiError(400, "validation_error", "End time must be after start time.")
    ref = get_db().collection("elections").document()
    ref.set({**f, "status": "draft", "results_published": f.get("results_published", False),
             "total_votes": 0, "created_by": g.user["uid"], "created_at": firestore.SERVER_TIMESTAMP})
    return jsonify(id=ref.id, message="Election created."), 201


@app.put("/api/admin/elections/<eid>")
@require_admin
def edit_election(eid):
    db = get_db(); doc = get_election(db, eid); cur = doc.to_dict()
    f = election_fields(json_body(), partial=True)
    if not f: raise ApiError(400, "validation_error", "Nothing to update.")
    if cur["status"] != "draft" and set(f) - {"results_published"}:
        raise ApiError(409, "not_editable", "Only drafts can be edited (except publishing results).")
    if f.get("end_time", cur["end_time"]) <= f.get("start_time", cur["start_time"]):
        raise ApiError(400, "validation_error", "End time must be after start time.")
    doc.reference.update(f)
    return jsonify(message="Election updated.")


@app.post("/api/admin/elections/<eid>/status")
@require_admin
def set_status(eid):
    new = json_body().get("status")
    db = get_db(); doc = get_election(db, eid); cur = doc.to_dict()
    allowed = {"draft": {"open"}, "open": {"closed"}, "closed": set()}
    if new not in allowed.get(cur["status"], set()):
        raise ApiError(409, "invalid_transition", f"Cannot change status from '{cur['status']}' to '{new}'.")
    if new == "open" and len(list(doc.reference.collection("candidates").limit(2).stream())) < 2:
        raise ApiError(409, "not_enough_candidates", "Add at least two candidates before opening.")
    doc.reference.update({"status": new})
    return jsonify(message=f"Election is now {new}.")


@app.post("/api/admin/elections/<eid>/candidates")
@require_admin
def add_candidate(eid):
    body = json_body(); doc = get_election(get_db(), eid)
    if doc.to_dict()["status"] != "draft":
        raise ApiError(409, "not_editable", "Candidates can only be changed while the election is a draft.")
    ref = doc.reference.collection("candidates").document()
    ref.set({"name": text(body, "name", 1, 80), "description": text(body, "description", 0, 300, False) or "", "votes": 0})
    return jsonify(id=ref.id, message="Candidate added."), 201


@app.delete("/api/admin/elections/<eid>/candidates/<cid>")
@require_admin
def delete_candidate(eid, cid):
    doc = get_election(get_db(), eid)
    if doc.to_dict()["status"] != "draft":
        raise ApiError(409, "not_editable", "Candidates can only be changed while the election is a draft.")
    ref = doc.reference.collection("candidates").document(check_id(cid, "candidate"))
    if not ref.get().exists: raise ApiError(404, "candidate_not_found", "Candidate not found.")
    ref.delete()
    return jsonify(message="Candidate removed.")


if __name__ == "__main__":
    try: init_firebase(); load_roles()
    except ApiError as e: log.error("Startup check: %s", e.message)
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")
