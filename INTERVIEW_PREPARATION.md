# Technical Interview Preparation Guide
## AI Server Quotation Agent — Rapid Stream Technology FZE

This document is prepared specifically for technical interviewers reviewing this project. It is structured to help you explain the architecture, walk through data flows, explain edge cases and failure modes, defend technical decisions, and confidently perform live coding modifications during a 35-minute video interview.

---

## 1. System Architecture & High-Level Narrative

### The "30-Second Elevator Pitch"
> "The RST Server Quotation Agent is a FastAPI and SQLite backend that receives natural language inquiries for enterprise servers. Rather than delegating critical business logic to an unpredictable LLM, the system uses an AI extraction interface to parse customer intent into structured models, while delegating catalog matching, stock validation, pricing calculation, and quotation drafting to deterministic Python services. Multi-turn dialogue is maintained in SQLite, idempotency is enforced at the database constraint level, and strict JWT role-based access control prevents customers from accessing internal cost metrics. No quotation is ever dispatched automatically without internal reviewer approval."

### Architectural Diagram
```
                     Customer Request (Natural Language)
                                     │
                                     ▼
                           [ POST /quotations ]
                                     │
                 ┌───────────────────┴───────────────────┐
                 │ 1. Idempotency Check (request_id)     │
                 │    If existing: Return stored draft   │
                 └───────────────────┬───────────────────┘
                                     │
                 ┌───────────────────┴───────────────────┐
                 │ 2. Retrieve / Create Conversation     │
                 │    Load accumulated state from SQLite │
                 └───────────────────┬───────────────────┘
                                     │
                 ┌───────────────────┴───────────────────┐
                 │ 3. Extraction Service (Mock/LLM)      │
                 │    Extract: family, GPUs, RAM, count  │
                 │    Merge with accumulated state       │
                 └───────────────────┬───────────────────┘
                                     │
                        Are key fields missing?
                                    ╱ ╲
                             YES  ╱     ╲  NO
                                ╱         ╲
                              ▼             ▼
       ┌────────────────────────┐      ┌─────────────────────────────┐
       │ Return Clarification   │      │ 4. Deterministic Catalog    │
       │ Question & Save State  │      │    Matching (SKU, Specs)    │
       │ (NEEDS_CLARIFICATION)  │      └──────────────┬──────────────┘
       └────────────────────────┘                     │
                                                      ▼
                                       ┌─────────────────────────────┐
                                       │ 5. Stock & Price Validation │
                                       │    Check stock vs requested │
                                       │    Calculate prices (catalog│
                                       │    selling price only)      │
                                       └──────────────┬──────────────┘
                                                      │
                                                      ▼
                                       ┌─────────────────────────────┐
                                       │ 6. Create Quotation Draft   │
                                       │    Status: PENDING_APPROVAL │
                                       │    or PENDING_SALES_REVIEW  │
                                       └──────────────┬──────────────┘
                                                      │
                                                      ▼
                                    Reviewer approves via internal endpoint
                                    [ POST /internal/quotations/{id}/approve ]
                                    Status -> APPROVED
```

---

## 2. Responsibilities of Each Module

| Module | File | Core Responsibility |
|---|---|---|
| **API Entry** | `app/main.py` | FastAPI application factory, lifespan startup, router aggregation, dev login route. |
| **Config** | `app/config.py` | Pydantic Settings reading `.env` (JWT secrets, provider, database URL). |
| **Database** | `app/database.py` | SQLAlchemy engine configuration, SQLite connection flags, session dependency. |
| **Catalog Data** | `app/data/catalog.py` | **Single source of truth** for all server configurations, stock numbers, selling prices, and internal costs. |
| **ORM Models** | `app/models/database_models.py` | Database schema definitions: `Conversation`, `Message`, `QuotationDraft`. |
| **Schemas** | `app/models/schemas.py` | Pydantic request/response schemas, Enums (`QuotationStatus`, `ExtractionConfidence`). |
| **Extraction** | `app/services/extraction.py` | `ExtractionProvider` interface + deterministic `MockExtractionProvider`. |
| **Catalog Service** | `app/services/catalog.py` | Matches extracted hardware specifications to official catalog entries. |
| **Inventory** | `app/services/inventory.py` | Real-time stock checking, shortage calculation, sales routing messages. |
| **Quotation** | `app/services/quotation.py` | Main orchestrator: idempotency, state merge, draft persistence. |
| **Approval** | `app/services/approval.py` | Enforces human approval rules, rejects pending price approvals, sets timestamps. |
| **Security** | `app/security/authentication.py` | JWT token minting, verification, role verification dependencies. |

---

## 3. Walkthrough of Major Endpoints

### 1. `POST /auth/token`
- **Purpose**: Authenticate and retrieve a signed JWT.
- **Roles**: `customer` or `internal_reviewer`.
- **Key Detail**: Tokens contain `sub` (username) and `role` claims, signed with `HS256`.

### 2. `POST /api/v1/quotations`
- **Purpose**: Intake customer natural language requests.
- **Parameters**: `request_id`, `conversation_id`, `message`.
- **Workflow**:
  1. Checks for existing `request_id` in SQLite. If present, returns stored draft immediately.
  2. Loads previous conversation state if existing.
  3. Extracts hardware requirements from message.
  4. Merges previous state with new extractions.
  5. If fields are missing (e.g. GPU count), returns `NEEDS_CLARIFICATION` and saves partial state.
  6. If complete, performs catalog matching.
  7. Validates inventory (shortage routing if requested > stock).
  8. Calculates total from catalog selling price only.
  9. Commits draft and returns `QuotationResponse`.

### 3. `POST /api/v1/conversations/{conversation_id}/messages`
- **Purpose**: Multi-turn dialogue continuation.
- **Behavior**: Customer replies with clarification (e.g. "Eight GPUs per server"). System looks up `conversation_id`, merges new parameters with previously accumulated state, and completes the quote.

### 4. `GET /api/v1/quotations/{request_id}`
- **Purpose**: Customer retrieval of quotation status.
- **Security**: Only returns public attributes (`unit_selling_price`, `total_price`, lead time, status). Internal costs are stripped.

### 5. `GET /api/v1/internal/quotations/{request_id}`
- **Purpose**: Internal sales/engineering analysis.
- **Security**: Requires `internal_reviewer` JWT role. Returns `unit_internal_cost`, `total_internal_cost`, and `gross_margin`.

### 6. `POST /api/v1/internal/quotations/{request_id}/approve`
- **Purpose**: Formal sign-off on quotation drafts.
- **Guardrails**:
  - Rejects approval if caller is not an `internal_reviewer` (`403`).
  - Rejects approval if quotation has pending pricing (`409`).
  - Rejects approval if already approved (`409`).
  - Rejects approval if status is not `PENDING_APPROVAL` (`409`).

---

## 4. Deep Dive: Key Engineering Decisions

### Why Deterministic Catalog Matching over LLM Function Calling?
> **Interviewer Question**: "Why didn't you just let the LLM generate the JSON with prices directly?"
>
> **Answer**: "LLMs are probabilistic generative models. They hallucinate numbers, miscalculate arithmetic ($245,000 \times 2$), and could accidentally invent discounts or fabricate stock. In financial and commercial systems, pricing and stock authority must remain strictly deterministic. We treat the AI solely as an NLP parser to understand customer intent. All pricing, inventory checks, and business calculations are executed by verified Python code using the catalog as the single source of truth."

### Idempotency & Concurrency Strategy
- **Client Request ID**: Clients supply a unique `request_id` (UUIDv4) with every submission.
- **Database Level Constraint**: The `quotation_drafts` table enforces `UNIQUE(request_id)`.
- **Concurrent Race Protection**:
  ```python
  try:
      db.add(draft)
      db.commit()
  except Exception:
      db.rollback()
      db.expunge_all()
      # Query the draft created by the winning thread
      existing = db.query(QuotationDraft).filter(QuotationDraft.request_id == request.request_id).first()
      if existing:
          return _draft_to_response(existing, db)
      raise
  ```
  If two identical requests hit the API simultaneously on different threads, the second commit fails with an integrity conflict, rolls back cleanly, and queries the row committed by the winning thread.

### Multi-Turn Conversation State Management
- Stored in SQLite under `conversations.accumulated_state` as JSON.
- If a customer mentions "RTX with 96GB" in turn 1, `accumulated_state` stores `{"gpu_family": "RTX", "gpu_memory_gb": 96, "num_servers": 2}`.
- When turn 2 arrives saying "8 GPUs per server", `_merge_extractions()` overlays the new fields onto the stored state.
- Once a quote successfully resolves to a catalog SKU, the state is cleared.

---

## 5. The Interview "Failure Case" Walkthrough

You will be asked to explain a real failure case encountered during design/implementation and how you diagnosed and resolved it. Here is the exact case:

### The Failure: Substring Regex Collision on Server Quantities
- **The Bug**: When a customer sent `"I need 2 RST-RTX8 servers."`, the system unexpectedly routed the quotation to sales review reporting a stock shortage of 6 units!
- **Root Cause Analysis**:
  1. The regex pattern for server count was initially:
     `r"(\d+|one|two|...)\s+(?:servers?|units?)"`
  2. In the string `"I need 2 RST-RTX8 servers."`, the pattern skipped `"2"` because `"RST-RTX8"` sat between `"2"` and `"servers"`.
  3. Instead, the regex evaluated `"RTX8 servers"`.
  4. Because `\d+` did not have a word boundary check (`\b`), the trailing character `"8"` in `"RTX8"` was matched as the requested server count!
  5. The catalog stock for `RST-RTX8` is 2. The system believed the customer requested 8 units, resulting in a shortage of 6 units and an improper status of `PENDING_SALES_REVIEW`.
- **The Fix**:
  1. Added word boundary assertion `\b` before the number pattern so that digits attached to letters (`RTX8`) cannot match:
     `r"\b(\d+|one|two|...)\b"`
  2. Expanded the pattern to optionally accommodate model qualifiers between the count and the noun:
     `r"\b(\d+|one|two|...)\s+(?:[A-Za-z0-9\-_]+\s+)?(?:servers?|units?|machines?)\b"`
  3. Added regression unit test `test_successful_rtx8_quotation` to guarantee that `"I need 2 RST-RTX8 servers."` extracts `num_servers=2` and reaches `PENDING_APPROVAL`.

---

## 6. 20 Likely Technical Interview Questions & Answers

### Architecture & System Design
**Q1: Why did you choose SQLite instead of PostgreSQL for this project?**
> **A**: For an assessment running locally on Windows 11 with zero external service dependencies, SQLite is lightweight, self-contained, and requires no daemon or Docker. Furthermore, by using SQLAlchemy ORM, the database layer is completely portable: changing `DATABASE_URL` in `.env` to a PostgreSQL connection string requires zero changes to application queries or models.

**Q2: What is the purpose of the `lifespan` handler in `main.py`?**
> **A**: FastAPI 0.111+ deprecated `@app.on_event("startup")` in favor of Starlette's `lifespan` async context manager. It manages application initialization (running `init_db()` to ensure tables exist) and teardown cleanly within a single context.

**Q3: How do you prevent customer requests from polluting each other's conversation states?**
> **A**: Conversation state is isolated by `conversation_id` in the `conversations` table. Every message and partial extraction is associated strictly with that primary key. An inquiry in conversation `conv-A` never reads or writes to `conv-B`.

**Q4: How does the system handle zero-stock items?**
> **A**: For zero-stock items like `RST-H2004` (stock = 0), the inventory service sets `can_fulfill = False` and `requires_sales_review = True`. The quotation status is set to `PENDING_SALES_REVIEW`. The response clearly indicates that 0 units are available and routes the request to sales, citing the estimated lead time without making a delivery promise.

**Q5: What happens when a product has a pending supplier quote (like RST-B3008)?**
> **A**: The catalog entry has `selling_price=None` and `internal_cost=None`. The quotation engine sets `price_pending=True`, leaves `unit_selling_price` and `total_price` as `None`, and sets status to `PENDING_SALES_REVIEW`. It never assumes $0 or creates a fabricated quote.

### Security & Authentication
**Q6: How do you prevent a malicious customer from injecting `role=internal_reviewer` in their JSON body?**
> **A**: The Pydantic request models (`QuotationRequest`, `ClarificationMessage`) do not accept a `role` field. Even if an attacker injects one into raw JSON, FastAPI ignores extra fields or validates against schemas that exclude it. The caller's role is extracted exclusively from the cryptographically signed JWT via `Depends(get_current_user)`.

**Q7: How do you prevent customer endpoints from leaking internal costs?**
> **A**: Through strict response schema segregation. The customer endpoint returns `QuotationResponse`, which lacks internal cost fields entirely. The internal endpoint returns `InternalQuotationResponse`, which inherits from `QuotationResponse` and adds `unit_internal_cost`, `total_internal_cost`, and `gross_margin`. The internal route is guarded by `Depends(require_internal_reviewer)`.

**Q8: What algorithm and library are you using for JWTs?**
> **A**: `python-jose` with the `HS256` HMAC-SHA256 signature algorithm. Tokens include `exp` (expiration), `iat` (issued at), `sub` (subject), and `role` claims, signed with an environment-configured secret key.

**Q9: Why not use session cookies instead of JWTs?**
> **A**: JWTs are stateless and standard for REST APIs, making them ideal for mobile apps, external clients, and microservices. They eliminate the need for server-side session lookup tables in Redis or the database.

**Q10: What HTTP status code is returned when a customer calls an internal endpoint?**
> **A**: `HTTP 403 Forbidden` with the message `"Internal reviewer role required"`. If the token is missing or expired, `HTTP 401 Unauthorized` is returned.

### AI & NLP Extraction
**Q11: Why is a mock extraction provider mandatory?**
> **A**: In production engineering, test suites must run in CI/CD without network dependencies, paid API keys, or flakiness caused by third-party outages. The mock provider satisfies all assessment criteria offline deterministically.

**Q12: How would you connect a real LLM like Google Gemini?**
> **A**: We implement the `ExtractionProvider` abstract base class. To add Gemini, we create `GeminiExtractionProvider(ExtractionProvider)` that calls `genai.GenerativeModel.generate_content` with Pydantic structured output (`response_schema=ExtractionResult`). Swapping providers only requires changing `LLM_PROVIDER=gemini` in `.env`.

**Q13: How does the mock extractor handle confidence levels?**
> **A**: It counts the number of critical fields identified (family/SKU, memory, GPU count, server count). If all 4 are present, it assigns `HIGH`; for 2–3 fields, `MEDIUM`; for 1 or fewer, `LOW`.

**Q14: How does the system handle an unsupported configuration?**
> **A**: If a customer requests a configuration not present in the catalog (e.g. "999 GPUs per server"), `match_catalog()` returns `matched=False` with notes explaining no catalog entry matches. The quote is marked `PENDING_SALES_REVIEW`.

### Idempotency & Database
**Q15: What is idempotency and why is it critical here?**
> **A**: Idempotency ensures that making the same request multiple times produces the exact same outcome as making it once. If a customer's network disconnects and they resubmit `"req-001"`, the system returns the existing quotation draft rather than creating duplicate database records or reserving duplicate stock.

**Q16: How do you test concurrent duplicate requests?**
> **A**: Using Python's `threading` module to fire 5 concurrent HTTP POST requests with the same `request_id`. We assert that zero unhandled exceptions occur and that all 5 responses return the exact same `draft_id`.

**Q17: Why did you use `expire_on_commit=False` in SQLAlchemy?**
> **A**: By default, SQLAlchemy expires instance attributes upon `commit()`. Accessing an attribute on an expired instance triggers an automatic refresh query. Under high concurrency or multi-threading, this can cause `ObjectDeletedError` or database lock contention. Setting `expire_on_commit=False` preserves attributes in memory after commit.

**Q18: What is the purpose of `db.expunge_all()` in the rollback handler?**
> **A**: When a concurrent transaction fails due to a unique constraint violation, rolling back leaves uncommitted model objects in the session's identity map. `expunge_all()` clears the identity map so subsequent queries fetch clean records directly from the database.

### Workflow & Business Logic
**Q19: Can a quotation draft be approved if pricing is pending?**
> **A**: No. The approval service explicitly checks `if draft.unit_selling_price is None:` and raises an `HTTP 409 Conflict`. Drafts with pending supplier quotes cannot be approved until sales updates the price.

**Q20: What are all the statuses in the quotation lifecycle?**
> **A**:
> 1. `NEEDS_CLARIFICATION`: Missing information; question sent to customer.
> 2. `PENDING_SALES_REVIEW`: Stock shortage, zero stock, custom specs, or pending price.
> 3. `PENDING_APPROVAL`: Matched catalog entry with full stock and valid price ready for reviewer sign-off.
> 4. `APPROVED`: Formally signed off by an internal reviewer.
> 5. `REJECTED`: Declined by reviewer.

---

## 7. How to Demonstrate During Screen Sharing

Follow these steps during your live 35-minute interview:

### Step 1: Show Project Structure & Config (2 minutes)
1. Open VS Code in `rapid-stream-quotation-agent`.
2. Open `app/data/catalog.py`: Show the centralized catalog (RTX8, H2004, B3008).
3. Open `app/models/schemas.py`: Show clean separation of customer vs internal schemas.

### Step 2: Run the Automated Test Suite (2 minutes)
In the terminal, run:
```powershell
pytest -v
```
Point out:
- 42 passing tests.
- High-coverage areas: multi-turn clarification, concurrency idempotency, RBAC, pending pricing.

### Step 3: Run the Demonstration Walkthrough (3 minutes)
Run:
```powershell
python scripts/demo_walkthrough.py
```
Walk the interviewer through the console output:
1. Health check.
2. Token generation for customer and reviewer.
3. RTX8 quote calculation ($490,000 for 2 units).
4. Multi-turn clarification (Turn 1: missing count -> Turn 2: continuation).
5. Stock shortage detection (3 requested vs 2 in stock).
6. Zero-stock product routing.
7. Pending price preservation.
8. Security check (403 for customer, 200 with margin for reviewer).
9. Idempotent deduplication.
10. Reviewer approval.

### Step 4: Show Interactive Swagger UI (3 minutes)
1. Run `uvicorn app.main:app --reload`
2. Open `http://127.0.0.1:8000/docs`
3. Execute `POST /auth/token` with `reviewer1` / `reviewerpass`.
4. Click **Authorize** at top right, paste token.
5. Execute `GET /api/v1/internal/quotations/demo-rtx8-001` to show internal costs and margin live.
