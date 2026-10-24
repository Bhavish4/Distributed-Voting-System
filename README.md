# VoteChain — Distributed Online Voting System

A distributed, fault-tolerant online voting system built with Flask, Cloud Firestore, and Firebase Authentication. Includes an academic prototype voting mechanism with optimistic concurrency transactions, role-based access control, and a modern glassmorphic dark-mode web interface.

---

## 🌟 Features & Modern UI

- **Modern Glassmorphic Dark UI**: Deep space background (`#080c18`) with ambient indigo/cyan glow orbs, frosted translucent glass cards, smooth micro-interactions, and Google Fonts (Inter).
- **Dedicated Authentication Portal (`/login`)**:
  - **Tabbed Sign-In**: Toggle between **Email & Password** and **Sign in with Google**.
  - **Interactive Controls**: Password visibility toggle (👁 / 🙈) and real-time validation error prompts.
  - **1-Click Demo Admin Box**: Built-in credential cards for `demo@admin.com` with instant **Copy** and **⚡ 1-Click Auto-Fill** buttons.
- **Role-Based Access Control (RBAC)**:
  - Role management driven by server-side `roles.json`.
  - Roles (`admin` vs `voter`) are resolved securely server-side and never trusted from client inputs.
  - `demo@admin.com` is configured with full **Administrator** privileges.
- **Elections Dashboard (`/`)**:
  - Live election status badges (◌ Draft, ● Open, ◼ Closed).
  - Voting cards with single-choice selection and one-vote-per-election enforcement.
- **Admin Control Panel (`/admin`)**:
  - Create elections with date/time pickers and candidate roster management.
  - State machine transitions (Draft → Open → Closed → Publish Results).
  - Real-time candidate vote tally chips.
- **Visual Results Leaderboard (`/results`)**:
  - 🏆 Winner spotlight card with animated vote distribution bars and percentage shares.
  - Automatic results privacy gate (results hidden from voters until published by an admin).

---

## 🔑 Demo Credentials

| Role | Email | Password / Provider | Access Privileges |
|---|---|---|---|
| **Administrator** | `demo@admin.com` | `Admin@123` (or Google SSO) | Full admin panel access (`/admin`), create elections, manage candidates, publish results |
| **Voter** | Any other Google account | Google SSO | View open elections, cast 1 vote per election, view published results |

*Roles are configured in [roles.json](file:///roles.json) and dynamically loaded by the backend.*

---

## 📁 Application Structure

```
Distributed_Voting_System/
├── app.py                     # Flask backend, API endpoints, auth middleware & transactions
├── roles.json                 # Role definitions (single source of truth for admin permissions)
├── firestore.rules            # Firestore security rules (denies direct client access)
├── requirements.txt           # Python dependencies (Flask, firebase-admin, etc.)
├── static/
│   ├── firebase.js            # Firebase Web SDK initialization, token management & API helpers
│   └── style.css              # Unified design system tokens, components, and animations
├── templates/
│   ├── index.html             # Homepage / Voter election ballot interface
│   ├── login.html             # Dedicated sign-in portal (Email/Pass + Google SSO + Demo box)
│   ├── admin.html             # Admin dashboard for election lifecycle management
│   └── results.html           # Visual results leaderboard & winner podium
└── tests/
    ├── test_app.py            # Pytest test suite for auth, RBAC, input validation & errors
    └── concurrency_check.py   # Live concurrency test simulating parallel race conditions
```

---

## 🚀 Quickstart & Setup (Windows PowerShell)

### 1. Prerequisites
- Python 3.10+ installed
- A Google Cloud Service Account key JSON file for Firebase Admin SDK

### 2. Installation & Environment
In your PowerShell terminal:
```powershell
# Clone or enter directory
cd Distributed_Voting_System

# Create virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install requirements
pip install -r requirements.txt

# Set required environment variables (replace with your service account key path)
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\secrets\voting-service-account.json"
$env:FIREBASE_PROJECT_ID = "distributed-voting-syste-8559f"
$env:FLASK_DEBUG = "1"

# Verify credentials file exists
Test-Path $env:GOOGLE_APPLICATION_CREDENTIALS
```

### 3. Run the Development Server
```powershell
python app.py
```
Open **`http://localhost:5000`** (or **`http://localhost:5000/login`**) in your browser.

> **Tip:** If opening in a new terminal, reactivate the `.venv` and export `$env:GOOGLE_APPLICATION_CREDENTIALS`. Alternatively, copy `.env.example` to `.env`.

---

## 🌐 Page Navigation & Routes

| Path | Template | Description |
|---|---|---|
| `/` | `index.html` | Voter portal with available elections, voting actions, and account status |
| `/login` | `login.html` | Portal with Email/Password & Google SSO tabs, demo credentials auto-fill |
| `/admin` | `admin.html` | Administrator dashboard for election creation, candidate controls, and publishing |
| `/results?e=<id>` | `results.html` | Visual vote tally, percentage bars, and winner display for election `<id>` |

---

## 🛠 API Reference

All protected endpoints expect `Authorization: Bearer <Firebase_ID_Token>`.

### Public & Diagnostic
- `GET /api/health` — Verifies Firebase Admin SDK, `roles.json`, and Cloud Firestore connectivity.

### User & Authentication
- `GET /api/me` — Returns authenticated user profile (`uid`, `email`, `name`, `picture`, `role`).

### Voter Endpoints
- `GET /api/elections` — List visible elections (draft elections are automatically hidden from non-admins).
- `GET /api/elections/<id>` — Get details for a specific election.
- `POST /api/elections/<id>/vote` — Cast vote (`{"candidate_id": "<id>"}`). Enforces 1 vote per user via Firestore transaction.
- `GET /api/elections/<id>/results` — Get vote tallies (accessible to admins anytime, or to voters when published).

### Admin Endpoints (Requires `admin` role)
- `POST /api/admin/elections` — Create new election draft (`{"title", "description", "start_time", "end_time"}`).
- `PUT /api/admin/elections/<id>` — Update election parameters or toggle `results_published: true|false`.
- `POST /api/admin/elections/<id>/status` — Advance election state (`{"status": "open" | "closed"}`).
- `POST /api/admin/elections/<id>/candidates` — Add candidate to a draft election (`{"name", "description"}`).
- `DELETE /api/admin/elections/<id>/candidates/<cid>` — Remove candidate from draft election.

---

## 🔒 Concurrency & Voting Integrity

Voting utilizes a single atomic Cloud Firestore **transaction**:
1. Reads the election document, candidate document, and the vote receipt document (`votes/<electionId>_<uid>`).
2. Asserts the election status is currently `"open"` and within the active start and end datetime window.
3. If a vote document for `votes/<electionId>_<uid>` already exists, the transaction aborts with **409 Conflict (`already_voted`)**.
4. Otherwise, it writes the vote record and atomically increments candidate and election vote counters.

Contending concurrent votes from the same user are caught by Firestore's optimistic concurrency control; retries detect the newly created vote document and safely reject the duplicate.

---

## 🧪 Testing

Run unit and mock integration tests using `pytest`:
```powershell
.\.venv\Scripts\python -m pytest -v
```

Run the live concurrency check script against an active election:
```powershell
.\.venv\Scripts\python tests/concurrency_check.py <ELECTION_ID> <CANDIDATE_ID>
```
*(Fires 10 parallel vote requests with the same UID; verifies exactly 1 succeeds with 201 and 9 are rejected with 409).*
