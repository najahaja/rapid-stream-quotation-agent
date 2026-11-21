"""
RST Quotation Agent — Demonstration Script.

Runs end-to-end walkthrough of all required scenarios:
1. Health check
2. Dev user authentication (customer & internal reviewer)
3. Successful RST-RTX8 quotation draft
4. Clarification question workflow (turn 1: missing GPU count -> turn 2: continuation)
5. Stock shortage detection (RST-RTX8 requested: 3, stock: 2)
6. Zero-stock product handling (RST-H2004 stock: 0)
7. Pending supplier quote (RST-B3008 price: None)
8. Role-based access control (customer denied internal costs, reviewer allowed)
9. Idempotency verification (duplicate request ID)
10. Internal human approval of quotation draft
"""

import json
import os
import sys
import uuid

# Add root directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app.database import init_db
from app.main import app

client = TestClient(app)


def print_section(title: str):
    print("\n" + "=" * 70)
    print(f" {title}")
    print("=" * 70)


def main():
    init_db()
    run_suffix = uuid.uuid4().hex[:6]
    rtx_req_id = f"demo-rtx8-{run_suffix}"
    rtx_conv_id = f"demo-conv-{run_suffix}"

    print_section("1. HEALTH CHECK")
    res = client.get("/health")
    print(f"Status: {res.status_code}")
    print(json.dumps(res.json(), indent=2))

    print_section("2. AUTHENTICATION")
    # Customer login
    res_cust = client.post(
        "/auth/token", json={"username": "customer1", "password": "customerpass"}
    )
    cust_token = res_cust.json()["access_token"]
    cust_headers = {"Authorization": f"Bearer {cust_token}"}
    print(f"Customer login HTTP {res_cust.status_code} — Token: {cust_token[:25]}...")

    # Reviewer login
    res_rev = client.post(
        "/auth/token", json={"username": "reviewer1", "password": "reviewerpass"}
    )
    rev_token = res_rev.json()["access_token"]
    rev_headers = {"Authorization": f"Bearer {rev_token}"}
    print(f"Reviewer login HTTP {res_rev.status_code} — Token: {rev_token[:25]}...")

    print_section("3. SUCCESSFUL RST-RTX8 QUOTATION")
    payload = {
        "request_id": rtx_req_id,
        "conversation_id": rtx_conv_id,
        "message": "I need two RST-RTX8 servers.",
    }
    res = client.post("/api/v1/quotations", json=payload, headers=cust_headers)
    print(f"HTTP {res.status_code}")
    print(json.dumps(res.json(), indent=2))

    print_section("4. CLARIFICATION WORKFLOW (MULTI-TURN)")
    # Turn 1: Customer asks without GPU count
    p1 = {
        "request_id": f"demo-clarify-001-{run_suffix}",
        "conversation_id": f"demo-conv-clarify-{run_suffix}",
        "message": "I need two RTX servers with 96 GB cards.",
    }
    res1 = client.post("/api/v1/quotations", json=p1, headers=cust_headers)
    print("--- Turn 1: Customer asks missing GPU count ---")
    print(f"Status: {res1.json()['status']}")
    print(f"Question: {res1.json()['clarification_question']}")

    # Turn 2: Customer provides clarification
    p2 = {
        "request_id": f"demo-clarify-002-{run_suffix}",
        "message": "Eight GPUs per server.",
    }
    res2 = client.post(
        f"/api/v1/conversations/demo-conv-clarify-{run_suffix}/messages",
        json=p2,
        headers=cust_headers,
    )
    print("\n--- Turn 2: Customer clarifies 'Eight GPUs per server' ---")
    print(f"Status: {res2.json()['status']}")
    print(f"Matched SKU: {res2.json()['catalog_match']['sku']}")
    print(f"Unit price: ${res2.json()['unit_selling_price']:,.2f}")
    print(f"Total price: ${res2.json()['total_price']:,.2f}")

    print_section("5. STOCK SHORTAGE HANDLING")
    p_short = {
        "request_id": f"demo-shortage-001-{run_suffix}",
        "conversation_id": f"demo-conv-shortage-{run_suffix}",
        "message": "I need 3 RST-RTX8 servers.",
    }
    res_short = client.post("/api/v1/quotations", json=p_short, headers=cust_headers)
    print("Requested: 3 | In Stock: 2")
    print(f"Status: {res_short.json()['status']}")
    print(f"Message: {res_short.json()['message']}")

    print_section("6. ZERO STOCK HANDLING")
    p_zero = {
        "request_id": f"demo-zero-001-{run_suffix}",
        "conversation_id": f"demo-conv-zero-{run_suffix}",
        "message": "I need 1 RST-H2004 server.",
    }
    res_zero = client.post("/api/v1/quotations", json=p_zero, headers=cust_headers)
    print("Requested: 1 | In Stock: 0")
    print(f"Status: {res_zero.json()['status']}")
    print(f"Message: {res_zero.json()['message']}")

    print_section("7. PENDING SUPPLIER PRICE (RST-B3008)")
    p_pending = {
        "request_id": f"demo-pending-001-{run_suffix}",
        "conversation_id": f"demo-conv-pending-{run_suffix}",
        "message": "I need 1 RST-B3008 server.",
    }
    res_pen = client.post("/api/v1/quotations", json=p_pending, headers=cust_headers)
    print(f"Status: {res_pen.json()['status']}")
    print(f"Price Pending: {res_pen.json()['price_pending']}")
    print(f"Unit Price: {res_pen.json()['unit_selling_price']}")
    print(f"Total Price: {res_pen.json()['total_price']}")
    print(f"Message: {res_pen.json()['message']}")

    print_section("8. SECURITY: ACCESS CONTROL & COST VISIBILITY")
    # Customer attempts to view internal costs
    res_denied = client.get(
        f"/api/v1/internal/quotations/{rtx_req_id}", headers=cust_headers
    )
    print(
        f"Customer accessing internal endpoint: HTTP {res_denied.status_code} ({res_denied.json()['detail']})"
    )

    # Internal Reviewer views internal costs
    res_cost = client.get(
        f"/api/v1/internal/quotations/{rtx_req_id}", headers=rev_headers
    )
    print(f"Reviewer accessing internal endpoint: HTTP {res_cost.status_code}")
    cost_data = res_cost.json()
    print(f"Unit Selling Price: ${cost_data['unit_selling_price']:,.2f}")
    print(f"Unit Internal Cost:  ${cost_data['unit_internal_cost']:,.2f}")
    print(f"Total Internal Cost: ${cost_data['total_internal_cost']:,.2f}")
    print(f"Gross Margin:        ${cost_data['gross_margin']:,.2f}")

    print_section("9. IDEMPOTENCY: DUPLICATE SUBMISSION")
    # Resubmit demo-rtx8-001
    res_dup = client.post("/api/v1/quotations", json=payload, headers=cust_headers)
    print(f"First submission draft_id:  {res.json()['draft_id']}")
    print(f"Second submission draft_id: {res_dup.json()['draft_id']}")
    assert res.json()["draft_id"] == res_dup.json()["draft_id"]
    print("SUCCESS: Exact same draft returned, no duplicates created.")

    print_section("10. INTERNAL HUMAN APPROVAL")
    res_app = client.post(
        f"/api/v1/internal/quotations/{rtx_req_id}/approve",
        json={"notes": "Approved for enterprise customer discount policy."},
        headers=rev_headers,
    )
    print(f"Approval HTTP {res_app.status_code}")
    print(json.dumps(res_app.json(), indent=2))

    print("\n" + "=" * 70)
    print(" ALL 10 DEMONSTRATION WORKFLOWS COMPLETED SUCCESSFULLY!")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
