# Rapid Stream Technology - AI Server Quotation Agent

An enterprise AI-assisted server quotation API built for **Rapid Stream Technology FZE**. 
The system receives natural-language customer inquiries, extracts hardware specifications, matches them deterministically against an official server catalog, handles multi-turn clarifications, validates real-time inventory and pricing, enforces idempotency, and drafts formal quotation proposals for internal human review and sign-off.

---

## Table of Contents
1. [Business Problem](#1-business-problem)
2. [Architecture & System Design](#2-architecture--system-design)
3. [Technology Stack](#3-technology-stack)
4. [Project Structure](#4-project-structure)
5. [Windows 11 Setup & Installation](#5-windows-11-setup--installation)
6. [Running the Application](#6-running-the-application)
7. [Authentication & Authorization](#7-authentication--authorization)
8. [Mock AI Mode vs LLM Mode](#8-mock-ai-mode-vs-llm-mode)
9. [API Endpoints & Swagger UI](#9-api-endpoints--swagger-ui)
10. [End-to-End API Walkthrough & Examples](#10-end-to-end-api-walkthrough--examples)
11. [Running Automated Tests](#11-running-automated-tests)
12. [Security Architecture & Decisions](#12-security-architecture--decisions)
13. [Failure Scenarios & Edge Cases](#13-failure-scenarios--edge-cases)
14. [Known Limitations & Future Improvements](#14-known-limitations--future-improvements)

---

## 1. Business Problem

High-performance computing (HPC) and AI server procurement involves specialized configurations (GPU models, VRAM capacities, interconnect fabrics, chassis form factors), complex pricing, and tight supply constraints. Sales engineers spend hours manually:
- Deciphering ambiguous customer emails ("I need two RTX servers with 96GB cards").
- Cross-referencing current warehouse inventory vs. factory lead times.
- Calculating commercial quotes while guarding proprietary internal costs.
- Preventing duplicate draft generation when customers retry requests.

**The Solution:**
The RST Quotation Agent automates the intake pipeline. It parses natural-language requests into structured hardware specifications, identifies missing constraints, routes clarification questions back to the client, checks real stock, applies authoritative catalog pricing, and creates drafts in an internal approval queue. Crucially, **the system never automatically dispatches quotations to customers** without human authorization.

---

## 2. Architecture & System Design

The system adheres to a layered, clean-architecture pattern with strict separation of concerns:

```
[ Customer / Client ]               [ Sales / Reviewer ]
        │                                    │
        ▼ (JWT Bearer: customer)             ▼ (JWT Bearer: internal_reviewer)
┌────────────────────────────────────────────────────────┐
│                   FastAPI Layer                        │
│   /api/v1/quotations     /api/v1/conversations         │
│   /api/v1/internal/quotations   /auth/token            │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│                   Service Layer                        │
│ ┌──────────────────────┐   ┌─────────────────────────┐ │
│ │  Extraction Service  │   │     Catalog Matcher     │ │
│ │ (Mock / Gemini Spec) │   │ (Deterministic Rules)   │ │
│ └──────────────────────┘   └─────────────────────────┘ │
│ ┌──────────────────────┐   ┌─────────────────────────┐ │
│ │  Inventory Service   │   │    Approval Service     │ │
│ │  (Stock & Shortage)  │   │ (Reviewer Validation)   │ │
│ └──────────────────────┘   └─────────────────────────┘ │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│         SQLAlchemy ORM & SQLite Persistence            │
│   Conversations (State)   QuotationDrafts (Unique Req) │
│   Messages (Audit Trail)  Catalog Data (Single Source) │
└────────────────────────────────────────────────────────┘
```

### Core Architecture Principles:
1. **Deterministic Catalog Authority**: The LLM or mock extraction provider parses text only. **Prices and stock levels are never determined by an AI.** Authoritative values are strictly joined from the catalog module.
2. **Conversation State Persistence**: Multi-turn dialogue is persisted in SQLite by `conversation_id`. Unresolved fields from Turn 1 are merged with clarifications from Turn 2.
3. **Database-Enforced Idempotency**: `request_id` has a database-level `UNIQUE` constraint. Duplicate submissions return the existing draft record without creating duplicates.
4. **Role-Based Cost Isolation**: Customer responses only ever contain `unit_selling_price` and `total_price`. Internal costs (`unit_internal_cost`, `gross_margin`) are only accessible via the `/api/v1/internal/*` routes by users holding the `internal_reviewer` JWT claim.

---

## 3. Technology Stack

- **Python**: 3.11+
- **Web Framework**: FastAPI 0.111.0 + Uvicorn
- **Data Validation & Schemas**: Pydantic v2
- **ORM & Database**: SQLAlchemy 2.0 with SQLite
- **Security & Tokens**: `python-jose` (Cryptographic JWT HS256)
- **HTTP Client**: HTTPX
- **Testing**: Pytest 8.2 + Pytest-Asyncio
- **Code Quality & Linting**: Ruff 0.4.4

---

## 4. Project Structure

```
rapid-stream-quotation-agent/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI initialization, lifespan & router mounts
│   ├── config.py                # Environment configuration (pydantic-settings)
│   ├── database.py              # SQLite engine, SessionLocal & get_db dependency
│   ├── api/
│   │   ├── __init__.py
│   │   ├── dependencies.py      # Auth and database dependencies
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── health.py        # /health endpoint
│   │       ├── quotations.py    # POST /api/v1/quotations, GET /api/v1/quotations/{id}
│   │       ├── conversations.py # POST /api/v1/conversations/{id}/messages
│   │       └── internal.py      # Reviewer cost view & approval endpoints
│   ├── data/
│   │   ├── __init__.py
│   │   └── catalog.py           # Centralized catalog single-source-of-truth
│   ├── models/
│   │   ├── __init__.py
│   │   ├── database_models.py   # SQLAlchemy ORM entities (Drafts, Conversations, Messages)
│   │   └── schemas.py           # Pydantic request/response models & Enums
│   ├── security/
│   │   ├── __init__.py
│   │   └── authentication.py    # JWT issue, decode, role enforcement
│   └── services/
│       ├── __init__.py
│       ├── extraction.py        # Provider interface, factory & deterministic MockExtractionProvider
│       ├── gemini_provider.py   # Production Google Gemini provider (google-genai SDK, schema output)
│       ├── catalog.py           # Deterministic specification matching
│       ├── inventory.py         # Stock availability & shortage routing logic
│       ├── quotation.py         # Quotation orchestration & idempotency handling
│       └── approval.py          # Internal approval workflow verification
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # Isolated test fixtures with temporary SQLite databases
│   ├── test_extraction.py       # Unit tests for mock NLP extraction
│   ├── test_gemini.py           # Comprehensive mocked Gemini unit & regression tests (23 tests)
│   ├── test_quotations.py       # End-to-end quotation flows
│   ├── test_inventory.py        # Stock shortage & zero-stock rules
│   ├── test_security.py         # JWT verification, RBAC, Swagger auth, customer isolation
│   ├── test_idempotency.py      # Sequential and concurrent duplicate handling
│   ├── test_conversations.py    # Multi-turn clarification state tests
│   └── test_approval.py         # Internal approval & pending-price rejection
├── scripts/
│   ├── create_dev_tokens.py     # CLI helper to print pre-signed dev JWTs
│   ├── demo_walkthrough.py      # Automated 10-step live demo script
│   ├── test_gemini_live.py      # Standalone CLI to test live Google Gemini extraction
│   └── test_swagger_auth.py     # Live validation script for Swagger UI authentication
├── .env.example
├── .gitignore
├── pyproject.toml               # Ruff & pytest configuration
├── requirements.txt
├── README.md
├── INTERVIEW_PREPARATION.md
└── LIVE_CODING_PRACTICE.md
```

---

## 5. Windows 11 Setup & Installation

Follow these exact commands in **Windows PowerShell**:

```powershell
# 1. Clone or navigate to the repository
cd d:\MINE\Assesment\rapid-stream-quotation-agent

# 2. Create Python 3.11 virtual environment
python -m venv .venv

# 3. Activate the virtual environment
.\.venv\Scripts\Activate.ps1

# (Note: If script execution is restricted on Windows, run:)
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process

# 4. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# 5. Initialize environment variables
Copy-Item .env.example .env
```

The application creates and configures `rst_quotation.db` automatically on startup.

---

## 6. Running the Application

Ensure your `.venv` is activated:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Console output:
```
INFO:     Started server process
INFO:     Starting RST Quotation Agent (provider=mock)
INFO:     Database initialized
INFO:     Uvicorn running on http://127.0.0.1:8000
```

---

## 7. Authentication & Authorization

Authentication is implemented via **JSON Web Tokens (JWT HS256)**.
The system provides two pre-configured roles:
- `customer`: Can submit requests, send clarifications, and view retail selling prices.
- `internal_reviewer`: Can access internal cost breakdowns, calculate margins, and approve quotation drafts.

### Development Users
| Username | Password | Role | Permissions |
|---|---|---|---|
| `customer1` | `customerpass` | `customer` | Submit quotes, view customer prices |
| `reviewer1` | `reviewerpass` | `internal_reviewer` | View internal costs, approve drafts |

### Obtaining Tokens
**Option A: Via API**
```powershell
curl -X POST http://127.0.0.1:8000/auth/token `
  -H "Content-Type: application/json" `
  -d '{"username":"customer1", "password":"customerpass"}'
```

**Option B: Using the Development Token Script**
```powershell
python scripts/create_dev_tokens.py
```
This prints signed JWTs for both roles ready to paste into Swagger UI or Postman.

---

## 8. Mock AI Mode vs LLM Mode

The application supports two extraction providers via the `LLM_PROVIDER` environment variable:
- `LLM_PROVIDER=mock`: 100% offline rule-based extraction (default). No API key needed.
- `LLM_PROVIDER=gemini`: Production LLM extraction via Google Gemini using the official `google-genai` SDK.

### Option A: Mock Mode (Default)
```ini
LLM_PROVIDER=mock
```
- **Deterministic Offline Parsing**: Uses regex and domain-specific grammar rules to extract GPU family (`RTX`, `H200`, `B300`), memory sizes (GB/TB), quantities, and SKU patterns.
- **Explicit vs Inferred Attribution**: Distinguishes between explicitly stated parameters (e.g. "8 GPUs per server") vs. inferred defaults.
- **Zero API Keys & Cost**: Requires no external network calls, ensuring 100% test reproducibility and zero operational cost.

### Option B: Google Gemini LLM Mode
```ini
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIzaSy...
GEMINI_MODEL=gemini-3.1-flash-lite
```
- **Official SDK**: Built using Google's current `google-genai` Python library.
- **Structured Schema Enforcement**: Uses `types.GenerateContentConfig` with `response_mime_type="application/json"` and `response_schema=ExtractionResult` for guaranteed JSON conformity.
- **Hard Guardrails**: The LLM ONLY extracts customer requirements into an `ExtractionResult` Pydantic model. It **never** computes prices, checks inventory, matches catalog SKUs, or approves quotations.
- **Defensive Post-Processing**: Recomputes `missing_fields` and confidence deterministically using RST catalog matching rules rather than trusting raw LLM output.
- **Error Handling & Resilience**:
  - `max_output_tokens=1024` hard-caps response size to prevent runaway truncated output.
  - Automatic Function Calling (AFC) is explicitly disabled.
  - Finish reasons like `MAX_TOKENS`, `SAFETY`, or malformed responses raise `GeminiResponseError` (mapped to HTTP 502 Bad Gateway).
  - Transient upstream 503 / high demand errors raise `GeminiUnavailableError` (mapped to HTTP 503).
  - Timeouts raise `GeminiTimeoutError` (mapped to HTTP 504).
  - Bounded retries with exponential backoff handle transient network blips.
- **Live Testing CLI**:
  ```powershell
  python scripts/test_gemini_live.py "I need two RST-RTX8 servers with 8 GPUs each."
  ```

---

## 9. API Endpoints & Swagger UI

FastAPI provides interactive API documentation at:
- **Swagger UI**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc**: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

### Swagger UI Authentication Quickstart
1. Open **POST `/auth/token`** in Swagger UI and click **Try it out**.
2. Submit development credentials (e.g. `customer1` / `customerpass` or `reviewer1` / `reviewerpass`).
3. Copy the returned `access_token` from the response.
4. Click the green **Authorize** button at the top right of the Swagger UI page.
5. In the **Value:** field, paste your token. Both raw token (`eyJhbGci...`) and prefixed token (`Bearer eyJhbGci...`) are accepted.
6. Click **Authorize**, then **Close**. All protected endpoints are now authorized.

### Endpoint Summary
| Method | Endpoint | Access Role | Description |
|---|---|---|---|
| `GET` | `/health` | Public | System health check & active LLM provider |
| `POST` | `/auth/token` | Public | Authenticate and obtain signed JWT |
| `POST` | `/api/v1/quotations` | `customer` / `reviewer` | Submit quotation request (idempotent) |
| `GET` | `/api/v1/quotations/{request_id}` | `customer` / `reviewer` | Retrieve customer-facing quotation |
| `POST` | `/api/v1/conversations/{id}/messages` | `customer` / `reviewer` | Submit clarification response |
| `GET` | `/api/v1/internal/quotations/{id}` | `internal_reviewer` | Retrieve quotation with cost & margin |
| `POST` | `/api/v1/internal/quotations/{id}/approve` | `internal_reviewer` | Formally approve quotation draft |

---

## 10. End-to-End API Walkthrough & Examples

### Example 1: Successful RTX8 Quotation
**Request:**
`POST /api/v1/quotations`
```json
{
  "request_id": "req-rtx-001",
  "conversation_id": "conv-rtx-001",
  "message": "I need two RST-RTX8 servers."
}
```

**Response (HTTP 200):**
```json
{
  "request_id": "req-rtx-001",
  "conversation_id": "conv-rtx-001",
  "status": "PENDING_APPROVAL",
  "extraction": {
    "gpu_family": null,
    "gpu_model": null,
    "gpu_memory_gb": null,
    "gpu_count_per_server": null,
    "num_servers": 2,
    "ram_gb": null,
    "sku": "RST-RTX8",
    "missing_fields": [],
    "confidence": "MEDIUM",
    "explicitly_stated": ["sku", "num_servers"],
    "inferred": []
  },
  "catalog_match": {
    "matched": true,
    "sku": "RST-RTX8",
    "description": "8× RTX PRO 6000 Server Edition, 96 GB/GPU, 512 GB RAM",
    "unit_selling_price": 245000.0,
    "lead_time": "2 business days",
    "stock_available": 2,
    "price_pending": false,
    "match_notes": "Matched by explicit SKU: RST-RTX8"
  },
  "clarification_question": null,
  "stock_check": {
    "sku": "RST-RTX8",
    "requested": 2,
    "available": 2,
    "can_fulfill": true,
    "shortage": 0,
    "requires_sales_review": false,
    "message": "All 2 unit(s) of RST-RTX8 are available. Lead time: 2 business days."
  },
  "unit_selling_price": 245000.0,
  "total_price": 490000.0,
  "price_pending": false,
  "draft_id": "e30521ad-8fa4-4065-98db-fa8d1c76ba1e",
  "message": "Quotation draft created for 2× RST-RTX8 at $245,000 each. Total: $490,000."
}
```

---

### Example 2: Multi-Turn Clarification
**Step 1: Incomplete Customer Inquiry**
`POST /api/v1/quotations`
```json
{
  "request_id": "req-clarify-01",
  "conversation_id": "conv-clarify-01",
  "message": "I need two RTX servers with 96 GB cards."
}
```
**Response (HTTP 200):**
```json
{
  "request_id": "req-clarify-01",
  "conversation_id": "conv-clarify-01",
  "status": "NEEDS_CLARIFICATION",
  "clarification_question": "How many GPUs do you require per server? (e.g., 4 or 8)",
  "catalog_match": null,
  "unit_selling_price": null,
  "total_price": null,
  "draft_id": "b4c26a97-9e32-475a-b68e-9c2941df5d4e",
  "message": "How many GPUs do you require per server? (e.g., 4 or 8)"
}
```

**Step 2: Customer Provides Clarification**
`POST /api/v1/conversations/conv-clarify-01/messages`
```json
{
  "request_id": "req-clarify-02",
  "message": "Eight GPUs per server."
}
```
**Response (HTTP 200):**
```json
{
  "request_id": "req-clarify-02",
  "conversation_id": "conv-clarify-01",
  "status": "PENDING_APPROVAL",
  "catalog_match": {
    "sku": "RST-RTX8",
    "unit_selling_price": 245000.0,
    "lead_time": "2 business days"
  },
  "unit_selling_price": 245000.0,
  "total_price": 490000.0,
  "message": "Quotation draft created for 2× RST-RTX8 at $245,000 each. Total: $490,000."
}
```

---

### Example 3: Stock Shortage Detection
**Request:** Customer asks for 3 units of `RST-RTX8` (warehouse stock = 2):
```json
{
  "request_id": "req-shortage-01",
  "conversation_id": "conv-shortage-01",
  "message": "I need 3 RST-RTX8 servers."
}
```
**Response:**
```json
{
  "status": "PENDING_SALES_REVIEW",
  "stock_check": {
    "sku": "RST-RTX8",
    "requested": 3,
    "available": 2,
    "can_fulfill": false,
    "shortage": 1,
    "requires_sales_review": true,
    "message": "Only 2 of 3 requested unit(s) are currently in stock. 1 unit(s) have been routed to the sales team for confirmation. Lead time for additional units: 2 business days."
  }
}
```

---

### Example 4: Pending Supplier Quote (RST-B3008)
**Request:** Customer asks for 1 unit of `RST-B3008`:
```json
{
  "request_id": "req-b300-01",
  "conversation_id": "conv-b300-01",
  "message": "I need 1 RST-B3008 server."
}
```
**Response:**
```json
{
  "status": "PENDING_SALES_REVIEW",
  "price_pending": true,
  "unit_selling_price": null,
  "total_price": null,
  "message": "The price for RST-B3008 is pending supplier quote. Your request has been routed to the sales team."
}
```
*(Crucial engineering rule: The system never fabricates a price or assumes $0).*

---

### Example 5: Internal Cost Information (Authorized Reviewer Only)
**Request:**
`GET /api/v1/internal/quotations/req-rtx-001`
*(With `internal_reviewer` JWT token)*

**Response (HTTP 200):**
```json
{
  "request_id": "req-rtx-001",
  "sku": "RST-RTX8",
  "unit_selling_price": 245000.0,
  "total_price": 490000.0,
  "unit_internal_cost": 219000.0,
  "total_internal_cost": 438000.0,
  "gross_margin": 52000.0
}
```
*(If called with a customer token, returns `HTTP 403 Forbidden`).*

---

## 11. Running Automated Tests

The test suite covers 80 test scenarios verifying mock NLP extraction, Google Gemini structured extraction, Gemini truncation/error handling, catalog matching logic, inventory, JWT security & customer isolation, Swagger authorization, idempotency, conversation continuity, and reviewer approval workflows. All tests run 100% offline with zero external network dependencies.

Run the full suite:
```powershell
python -m pytest -v
```

Expected output:
```
======================= 80 passed, 2 warnings in 9.42s ========================
```

Run code formatting and style checks:
```powershell
python -m ruff check .
python -m ruff format --check .
```

Run the automated live demonstration:
```powershell
python scripts/demo_walkthrough.py
```

---

## 12. Security Architecture & Decisions

1. **Role Extraction from JWT Only**: The caller role is decoded strictly from the verified JWT claims (`role` payload claim). Any `role` parameter passed in JSON request bodies or customer natural language messages is completely ignored.
2. **Internal Cost Shielding**: The customer schema `QuotationResponse` contains no cost or margin attributes. The internal schema `InternalQuotationResponse` inherits from it and adds cost metrics, exposed only through routes protected by `Depends(require_internal_reviewer)`.
3. **Idempotency with Concurrency Protection**: Sequential and concurrent duplicate submissions with the same `request_id` are caught both at the ORM layer and through SQLite database unique constraints (`UNIQUE(request_id)`). Failed concurrent commits roll back and query the existing draft.
4. **Approval State Machine**:
   - Only drafts in `PENDING_APPROVAL` status can be transitioned to `APPROVED`.
   - Drafts with pending supplier pricing (`selling_price is None`) return `HTTP 409 Conflict`.
   - Already-approved drafts cannot be re-approved (`HTTP 409 Conflict`).

---

## 13. Failure Scenarios & Edge Cases

| Failure Scenario | Handling & Response |
|---|---|
| **Missing specifications** | Transition to `NEEDS_CLARIFICATION`, store partial state in `conversations.accumulated_state`, return targeted question. |
| **Out-of-stock items** | Transition to `PENDING_SALES_REVIEW`, specify shortage count, quote standard lead time without delivery guarantee. |
| **Pending price product** | Keep `unit_selling_price=None` and `total_price=None`, set `price_pending=True`, route to sales team. Never assume $0. |
| **Model name substring collision** (e.g. `RTX8`) | Word boundary regex (`\b`) prevents parsing the "8" inside model name "RTX8" as server quantity. |
| **Concurrent duplicate requests** | Catches `IntegrityError` on unique constraint, rolls back transaction, queries and returns the winning thread's draft. |
| **Privilege escalation attempt** | Body manipulation attempts are ignored; unauthorized calls to internal endpoints return `HTTP 403 Forbidden`. |

---

## 14. Known Limitations & Future Improvements

1. **User Identity Store**: Dev users are configured in memory. In enterprise production, user authentication should integrate with an external identity provider (OAuth2/OIDC, Keycloak, or Active Directory).
2. **Live LLM Rate Limits & Upstream Quota**: When running with Google Gemini in live mode, external API rate limits or upstream 503 spikes may occur. The system implements bounded retries with exponential backoff and semantic HTTP error codes (502/503/504), but an enterprise production deployment would benefit from multi-region provider fallback.
3. **Quotation Expiration**: Quotations currently remain indefinitely in their created status. Adding an `expires_at` column with automatic status transitions to `EXPIRED` after 14 days would be a logical next phase.
4. **Inventory Locking**: Inventory checks are currently read-only. A reservation hold mechanism could lock stock upon draft approval.
