# Live Coding Practice Guide
## 10 Independent Code Modifications for the Technical Interview

During a 35-minute technical interview, you may be asked to implement a small modification live.
This guide gives you **10 realistic modifications**, showing exactly:
1. The business requirement.
2. Which files and functions to modify.
3. The exact code changes.
4. How to verify with a test.
5. How to explain your implementation to the interviewer.

---

## Exercise 1: Add a New Server SKU to the Catalog (`RST-L40S4`)

### Scenario
*"Rapid Stream Technology just added a new entry to the server catalog: the `RST-L40S4`. It has 4× L40S 48GB GPUs, 256GB RAM, 5 in stock, 3-day lead time, selling for $120,000, internal cost $95,000. Add it to the system."*

### Location
File: `app/data/catalog.py`

### Implementation
1. Add the entry to the `CATALOG` dictionary:
```python
    "RST-L40S4": CatalogEntry(
        sku="RST-L40S4",
        gpu_family="L40S",
        gpu_model="NVIDIA L40S",
        gpu_memory_gb=48,
        gpu_count=4,
        ram_gb=256,
        stock=5,
        lead_time="3 business days",
        selling_price=120_000.0,
        internal_cost=95_000.0,
        description="4× NVIDIA L40S, 48 GB/GPU, 256 GB RAM",
    ),
```

2. Add the GPU pattern to `app/services/extraction.py`:
In `MockExtractionProvider.GPU_PATTERNS`:
```python
    (re.compile(r"\bl40s?\b", re.I), "L40S", "NVIDIA L40S", 48),
```

### Verification
Run in terminal:
```powershell
python -c "from app.data.catalog import get_entry; entry = get_entry('RST-L40S4'); assert entry.selling_price == 120000; print('SUCCESS:', entry)"
```

### How to Explain to Interviewer
> "Because the catalog is completely centralized in `app/data/catalog.py`, adding a new product only requires adding a single `CatalogEntry` dataclass. The extraction service recognizes the family keyword, and the catalog matching service automatically resolves it without modifying database tables or schemas."

---

## Exercise 2: Volume Discount Tier (5% Discount on 5+ Units)

### Scenario
*"Management wants to offer a 5% volume discount on the total selling price if a customer orders 5 or more servers."*

### Location
File: `app/services/quotation.py` -> `_calculate_prices()`

### Implementation
Modify `_calculate_prices`:
```python
def _calculate_prices(sku: str, quantity: int) -> tuple[float | None, float | None]:
    entry = get_entry(sku)
    if entry is None or entry.selling_price is None:
        return None, None
    
    unit_price = entry.selling_price
    total_price = unit_price * quantity
    
    # 5% discount for 5 or more units
    if quantity >= 5:
        total_price = total_price * 0.95
        
    return unit_price, total_price
```

### Verification
Write a quick test in `tests/test_quotations.py` or run via python:
```powershell
python -c "from app.services.quotation import _calculate_prices; unit, total = _calculate_prices('RST-RTX8', 5); assert total == 245000 * 5 * 0.95; print('Discount total:', total)"
```

### How to Explain to Interviewer
> "I applied the discount strictly at the service calculation layer in `_calculate_prices()`. The catalog selling price remains untouched as the single source of truth, while the order-level business rule calculates the discounted total."

---

## Exercise 3: Add a Rejection Reason to Draft Rejection Endpoint

### Scenario
*"Implement a `POST /api/v1/internal/quotations/{request_id}/reject` endpoint that allows an internal reviewer to reject a quotation with a mandatory reason."*

### Locations
1. File: `app/models/schemas.py`
2. File: `app/services/approval.py`
3. File: `app/api/routes/internal.py`

### Implementation
**Step 1**: In `app/models/schemas.py`, add schema:
```python
class RejectionRequest(BaseModel):
    reason: str = Field(..., min_length=5, description="Reason for rejecting the draft")
```

**Step 2**: In `app/services/approval.py`, add function:
```python
def reject_quotation(request_id: str, reviewer_id: str, reason: str, db: Session) -> QuotationDraft:
    draft = db.query(QuotationDraft).filter(QuotationDraft.request_id == request_id).first()
    if not draft:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    if draft.status in (QuotationStatus.APPROVED, QuotationStatus.REJECTED):
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot reject already finalized draft")
    
    draft.status = QuotationStatus.REJECTED
    draft.reviewer_id = reviewer_id
    draft.notes = (draft.notes or "") + f"\nRejected by {reviewer_id}: {reason}"
    db.commit()
    db.refresh(draft)
    return draft
```

**Step 3**: In `app/api/routes/internal.py`, add endpoint:
```python
@router.post("/quotations/{request_id}/reject", summary="Reject a quotation draft")
def reject(
    request_id: str,
    body: RejectionRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_internal_reviewer),
):
    from app.services.approval import reject_quotation
    draft = reject_quotation(request_id, user["sub"], body.reason, db)
    return {"request_id": request_id, "status": draft.status, "reason": body.reason}
```

---

## Exercise 4: Add Quotation Expiration Date (`valid_until`)

### Scenario
*"Every created quotation draft must include a `valid_until` date set to exactly 14 days after creation."*

### Locations
1. File: `app/models/database_models.py`
2. File: `app/models/schemas.py`
3. File: `app/services/quotation.py`

### Implementation
**Step 1**: In `app/models/database_models.py` in `QuotationDraft`:
```python
valid_until = Column(DateTime, nullable=True)
```

**Step 2**: In `app/models/schemas.py` in `QuotationResponse`:
```python
valid_until: Optional[datetime] = None
```

**Step 3**: In `app/services/quotation.py` in `process_quotation()`:
```python
from datetime import timedelta

valid_until = datetime.utcnow() + timedelta(days=14)
draft = QuotationDraft(
    ...,
    valid_until=valid_until,
)
```
Pass `valid_until=valid_until` into the returned `QuotationResponse`.

---

## Exercise 5: Customer Currency Conversion Parameter (e.g. AED or EUR)

### Scenario
*"Allow the customer to supply an optional currency query parameter `?currency=AED` on `GET /api/v1/quotations/{request_id}` (1 USD = 3.67 AED)."*

### Location
File: `app/api/routes/quotations.py`

### Implementation
```python
@router.get("/{request_id}", response_model=QuotationResponse)
def get_quotation(
    request_id: str,
    currency: str = "USD",
    db: Session = Depends(get_db),
    user: dict = Depends(require_customer_or_reviewer),
) -> QuotationResponse:
    draft = get_draft_by_request_id(request_id, db)
    if draft is None:
        raise HTTPException(status_code=404, detail="Not found")
    
    resp = _draft_to_response(draft, db)
    if currency.upper() == "AED":
        conversion_rate = 3.67
        if resp.unit_selling_price:
            resp.unit_selling_price = round(resp.unit_selling_price * conversion_rate, 2)
        if resp.total_price:
            resp.total_price = round(resp.total_price * conversion_rate, 2)
    return resp
```

---

## Exercise 6: Enforce Maximum Server Quantity Limit (Cap at 20 Units)

### Scenario
*"Any customer order exceeding 20 servers must be automatically flagged as `PENDING_SALES_REVIEW` with note: 'Bulk enterprise orders over 20 units require special executive pricing approval'."*

### Location
File: `app/services/quotation.py` -> `process_quotation()`

### Implementation
In `process_quotation()` around line 185:
```python
            if quantity > 20:
                status = QuotationStatus.PENDING_SALES_REVIEW
                agent_message = "Bulk enterprise orders over 20 units require special executive pricing approval."
            elif price_pending:
                status = QuotationStatus.PENDING_SALES_REVIEW
...
```

---

## Exercise 7: List All Pending Quotations for Internal Reviewer

### Scenario
*"Create an endpoint `GET /api/v1/internal/quotations` that returns a list of all quotation drafts currently awaiting approval (`PENDING_APPROVAL`)."*

### Location
File: `app/api/routes/internal.py`

### Implementation
```python
@router.get("/quotations", response_model=list[InternalQuotationResponse], summary="List all quotations")
def list_quotations(
    status_filter: Optional[str] = "PENDING_APPROVAL",
    db: Session = Depends(get_db),
    user: dict = Depends(require_internal_reviewer),
):
    query = db.query(QuotationDraft)
    if status_filter:
        query = query.filter(QuotationDraft.status == status_filter)
    drafts = query.order_by(QuotationDraft.created_at.desc()).all()
    
    results = []
    for d in drafts:
        base = _draft_to_response(d, db)
        entry = get_entry(d.sku) if d.sku else None
        unit_cost = entry.internal_cost if entry else None
        total_cost = (unit_cost * d.quantity) if unit_cost and d.quantity else None
        margin = ((d.unit_selling_price - unit_cost) * d.quantity) if d.unit_selling_price and unit_cost and d.quantity else None
        results.append(InternalQuotationResponse(
            **base.model_dump(),
            unit_internal_cost=unit_cost,
            total_internal_cost=total_cost,
            gross_margin=margin,
        ))
    return results
```

---

## Exercise 8: Add Minimum Gross Margin Floor Protection (10% Minimum Margin)

### Scenario
*"Before an internal reviewer approves a quotation, verify that the gross margin is at least 10%. If the selling price is less than 1.10 × internal cost, reject approval with an error."*

### Location
File: `app/services/approval.py` -> `approve_quotation()`

### Implementation
```python
    # Check minimum gross margin floor
    entry = get_entry(draft.sku)
    if entry and entry.internal_cost and draft.unit_selling_price:
        min_allowed_price = entry.internal_cost * 1.10
        if draft.unit_selling_price < min_allowed_price:
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Quotation cannot be approved: selling price (${draft.unit_selling_price:,.2f}) is below minimum 10% margin threshold (${min_allowed_price:,.2f})."
            )
```

---

## Exercise 9: Audit Log of All State Transitions

### Scenario
*"Whenever a quotation draft transitions its status (e.g. from `NEEDS_CLARIFICATION` to `PENDING_APPROVAL` to `APPROVED`), record an audit entry in a dedicated `status_history` JSON field or message log."*

### Location
File: `app/models/database_models.py` & `app/services/approval.py`

### Implementation
In `app/services/approval.py`:
```python
    now_iso = datetime.now(timezone.utc).isoformat()
    audit_log = f"\n[AUDIT {now_iso}] Status changed from {draft.status} to APPROVED by {reviewer_id}"
    draft.notes = (draft.notes or "") + audit_log
```

---

## Exercise 10: Extract RAM in Terabytes from Natural Language

### Scenario
*"Enhance the mock NLP parser to extract RAM when specified as '2 TB RAM' or '2048 GB RAM' and validate it against the catalog RAM specification."*

### Location
File: `app/services/extraction.py` -> `MockExtractionProvider.extract()`

### Implementation
Verify regex:
```python
    RAM_PATTERN = re.compile(r"(\d+)\s*(tb|gb)\s+(?:ram|memory)", re.I)
```
In `extract()`:
```python
    ram_match = self.RAM_PATTERN.search(message)
    if ram_match:
        val = int(ram_match.group(1))
        unit = ram_match.group(2).lower()
        result.ram_gb = val * 1024 if unit == "tb" else val
        explicitly_stated.append("ram_gb")
```
For `RST-B3008`, `ram_gb` is 2048. When a customer says "2 TB RAM", it parses `val=2`, `unit='tb'`, resulting in `2048 GB`, successfully matching `RST-B3008.ram_gb`.

---

## Summary Advice for Live Coding
1. **Always run pytest first**: Before making changes, run `pytest tests/` to confirm your baseline is clean.
2. **Make the smallest correct change**: Don't over-engineer. Locate the exact layer (data, model, service, or route) and modify only what is asked.
3. **Run Ruff**: After modifying code, run `ruff check --fix app/ tests/` to keep imports sorted and formatting clean.
4. **Speak before typing**: State your plan to the interviewer: *"I will first update the schema in `schemas.py`, then add the business logic check in `services/approval.py`, and test it using pytest."*
