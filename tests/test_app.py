import json
from unittest.mock import MagicMock, patch
import pytest
from firebase_admin import auth as fb_auth
from google.api_core.exceptions import ServiceUnavailable
import app as m

ROLES = {"default_role": "voter", "roles": {"bunnylucky0422@gmail.com": "admin", "demo@admin.com": "admin"}}
VALID = {"title": "Class rep", "description": "", "start_time": "2030-01-01T00:00:00Z", "end_time": "2030-01-02T00:00:00Z"}
H = {"Authorization": "Bearer x"}

def decoded(email, verified=True): return {"uid": "uid123456", "email": email, "email_verified": verified, "name": "T"}

@pytest.fixture
def client(tmp_path, monkeypatch):
    p = tmp_path / "roles.json"; p.write_text(json.dumps(ROLES))
    monkeypatch.setattr(m, "ROLES_PATH", p); m._roles_cache.clear()
    monkeypatch.setattr(m, "init_firebase", lambda: None); monkeypatch.setattr(m, "save_profile", lambda u: None)
    m.app.config["TESTING"] = True
    return m.app.test_client()

def as_user(email, **kw): return patch.object(m.fb_auth, "verify_id_token", return_value=decoded(email, **kw))

def test_missing_token(client): assert client.get("/api/me").status_code == 401
def test_invalid_token(client):
    with patch.object(m.fb_auth, "verify_id_token", side_effect=fb_auth.InvalidIdTokenError("bad")):
        assert client.get("/api/me", headers=H).get_json()["error"] == "invalid_token"
def test_expired_token(client):
    with patch.object(m.fb_auth, "verify_id_token", side_effect=fb_auth.ExpiredIdTokenError("old", None)):
        assert client.get("/api/me", headers=H).get_json()["error"] == "token_expired"
def test_admin_case_insensitive(client):
    with as_user("BunnyLucky0422@Gmail.com"): assert client.get("/api/me", headers=H).get_json()["role"] == "admin"
def test_demo_admin(client):
    with as_user("demo@admin.com"): assert client.get("/api/me", headers=H).get_json()["role"] == "admin"
def test_default_voter(client):
    with as_user("someone@x.com"): assert client.get("/api/me", headers=H).get_json()["role"] == "voter"
def test_unverified_email_never_admin(client):
    with as_user("bunnylucky0422@gmail.com", verified=False): assert client.get("/api/me", headers=H).get_json()["role"] == "voter"
    with as_user("demo@admin.com", verified=False): assert client.get("/api/me", headers=H).get_json()["role"] == "admin"
def test_voter_cannot_create(client):
    with as_user("someone@x.com"): assert client.post("/api/admin/elections", json=VALID, headers=H).status_code == 403
def test_admin_create_validation(client):
    with as_user("demo@admin.com"): assert client.post("/api/admin/elections", json={"title": "x"}, headers=H).status_code == 400
def test_admin_create_ok(client):
    db = MagicMock(); db.collection.return_value.document.return_value.id = "e1"
    with as_user("demo@admin.com"), patch.object(m, "get_db", return_value=db):
        r = client.post("/api/admin/elections", json=VALID, headers=H)
    assert r.status_code == 201 and r.get_json()["id"] == "e1"
def test_firestore_failure_503(client):
    with as_user("demo@admin.com"), patch.object(m, "get_db", side_effect=ServiceUnavailable("down")):
        assert client.post("/api/admin/elections", json=VALID, headers=H).status_code == 503
def test_roles_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ROLES_PATH", tmp_path / "nope.json"); m._roles_cache.clear()
    with as_user("demo@admin.com"): assert client.get("/api/me", headers=H).get_json()["error"] == "roles_config_error"
def test_roles_malformed(client, tmp_path, monkeypatch):
    p = tmp_path / "bad.json"; p.write_text("{not json"); monkeypatch.setattr(m, "ROLES_PATH", p); m._roles_cache.clear()
    with as_user("demo@admin.com"): assert client.get("/api/me", headers=H).status_code == 500
def test_roles_with_bom(client, tmp_path, monkeypatch):
    p = tmp_path / "bom.json"; p.write_bytes(b"\xef\xbb\xbf" + json.dumps(ROLES).encode()); monkeypatch.setattr(m, "ROLES_PATH", p); m._roles_cache.clear()
    with as_user("demo@admin.com"): assert client.get("/api/me", headers=H).get_json()["role"] == "admin"
def test_invalid_ids(client):
    with as_user("someone@x.com"):
        assert client.post("/api/elections/e1/vote", json={"candidate_id": "../x"}, headers=H).status_code == 400
        assert client.post("/api/elections/bad.id/vote", json={"candidate_id": "c"}, headers=H).status_code == 400
def test_closed_status_logic():
    from datetime import datetime, timezone as tz
    el = {"status": "closed", "start_time": datetime(2020,1,1,tzinfo=tz.utc), "end_time": datetime(2030,1,1,tzinfo=tz.utc)}
    assert m.effective_status(el) == "closed"
    el["status"] = "open"; el["end_time"] = datetime(2021,1,1,tzinfo=tz.utc)
    assert m.effective_status(el) == "ended"
