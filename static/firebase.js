// Firebase init + auth helpers. Requires firebase-app-compat and firebase-auth-compat loaded first.
const firebaseConfig = {
  apiKey: "AIzaSyCHc8x5LF2uHG6DYRuOSodeqoR8svRhEA8",
  authDomain: "distributed-voting-syste-8559f.firebaseapp.com",
  projectId: "distributed-voting-syste-8559f",
  storageBucket: "distributed-voting-syste-8559f.firebasestorage.app",
  messagingSenderId: "743411407521",
  appId: "1:743411407521:web:1b83c089c327c91a7c6ee0",
  measurementId: "G-T12HFL251E"
};
firebase.initializeApp(firebaseConfig);
console.info("[auth] Firebase initialised for project", firebaseConfig.projectId);
const auth = firebase.auth();

// Resolves ONCE with the restored user (or null) - every API call waits on this,
// so nothing is sent before Firebase has finished restoring the session.
const authReady = new Promise(resolve => {
  const off = auth.onAuthStateChanged(u => { off(); resolve(u); });
});

function esc(s) { const d = document.createElement("div"); d.textContent = s ?? ""; return d.innerHTML; }

async function signIn() {
  try {
    await auth.signInWithPopup(new firebase.auth.GoogleAuthProvider());
  } catch (e) {
    console.error("[auth] sign-in failed:", e.code);
    const m = { "auth/popup-blocked": "Popup blocked. Allow popups for this site and retry.",
      "auth/popup-closed-by-user": "Sign-in window was closed.",
      "auth/unauthorized-domain": "This domain is not authorised in Firebase Console (Authentication > Settings > Authorized domains). Use localhost.",
      "auth/operation-not-allowed": "Google provider is not enabled in Firebase Console.",
      "auth/network-request-failed": "Network error during sign-in." };
    throw new Error(m[e.code] || "Sign-in failed (" + e.code + ").");
  }
}
async function signOut() { await auth.signOut(); location.href = "/"; }

class ApiFailure extends Error { constructor(status, code, msg) { super(msg); this.status = status; this.code = code; } }

async function apiFetch(path, opts = {}, retried = false) {
  await authReady;
  const user = auth.currentUser;
  if (!user) throw new ApiFailure(401, "not_signed_in", "Please sign in first.");
  const token = await user.getIdToken(retried);          // force refresh on retry
  let res;
  try {
    res = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", ...(opts.headers || {}), Authorization: "Bearer " + token } });
  } catch (e) {                                          // this is what a Flask-log status 0 looks like in the browser
    console.error("[api] network failure for", path);
    throw new ApiFailure(0, "network_error", "Cannot reach the server (not running, request cancelled, or blocked). See DevTools > Network.");
  }
  const body = await res.json().catch(() => ({}));
  console.debug("[api]", opts.method || "GET", path, res.status, body.error || "");
  if (res.status === 401 && body.error === "token_expired" && !retried) return apiFetch(path, opts, true);
  if (!res.ok) throw new ApiFailure(res.status, body.error, body.message || "Request failed (" + res.status + ").");
  return body;
}

function showMsg(el, text, ok = false) { el.textContent = text; el.className = "msg " + (ok ? "ok" : "err"); el.hidden = !text; }
