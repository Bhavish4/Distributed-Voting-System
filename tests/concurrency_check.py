"""LIVE check against your real Firestore (needs GOOGLE_APPLICATION_CREDENTIALS).
Usage: python tests/concurrency_check.py <OPEN_ELECTION_ID> <CANDIDATE_ID>
Use a throwaway election: fires 10 simultaneous votes from ONE uid; expects exactly 1 success."""
import sys, threading
sys.path.insert(0, ".")
import app as m
eid, cid = sys.argv[1:3]; db = m.get_db(); res = []
def go():
    try: m.cast_vote(db, eid, cid, "concurrency-test-uid"); res.append("ok")
    except m.ApiError as e: res.append(e.code)
ts = [threading.Thread(target=go) for _ in range(10)]
[t.start() for t in ts]; [t.join() for t in ts]
print(res); assert res.count("ok") == 1, "DUPLICATE VOTE ACCEPTED"; print("PASS: exactly one vote recorded")
db.collection("votes").document(f"{eid}_concurrency-test-uid").delete()
print("Counters keep +1 from this test; delete the throwaway election afterwards.")
