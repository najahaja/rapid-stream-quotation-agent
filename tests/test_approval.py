"""Approval workflow tests."""


def _create_rtx8_quotation(
    client, customer_headers, request_id="req-approve-01", conv_id="conv-approve-01"
):
    """Helper: create a fully matched RTX8 quotation in PENDING_APPROVAL state."""
    resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": request_id,
            "conversation_id": conv_id,
            "message": "I need 1 RST-RTX8 server.",
        },
        headers=customer_headers,
    )
    assert resp.status_code == 200
    return resp.json()


def test_valid_approval(client, customer_headers, reviewer_headers):
    """Test 16: Reviewer can approve a PENDING_APPROVAL draft."""
    draft = _create_rtx8_quotation(client, customer_headers)
    assert draft["status"] == "PENDING_APPROVAL"

    resp = client.post(
        f"/api/v1/internal/quotations/{draft['request_id']}/approve",
        json={"notes": "Looks good."},
        headers=reviewer_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "APPROVED"
    assert data["reviewer_id"] == "reviewer1"


def test_customer_cannot_approve(client, customer_headers):
    """Test 17: Customer role must not be able to approve."""
    draft = _create_rtx8_quotation(
        client, customer_headers, "req-unauth-approve", "conv-unauth-approve"
    )
    resp = client.post(
        f"/api/v1/internal/quotations/{draft['request_id']}/approve",
        json={},
        headers=customer_headers,
    )
    assert resp.status_code == 403


def test_approval_of_pending_price_draft_rejected(
    client, customer_headers, reviewer_headers
):
    """Test 18: Approving a pending-price draft must fail."""
    # Create B3008 quotation (pending price)
    resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-pending-approve",
            "conversation_id": "conv-pending-approve",
            "message": "I need 1 RST-B3008 server.",
        },
        headers=customer_headers,
    )
    data = resp.json()
    assert data["price_pending"] is True

    # Try to approve
    approve_resp = client.post(
        f"/api/v1/internal/quotations/{data['request_id']}/approve",
        json={},
        headers=reviewer_headers,
    )
    assert approve_resp.status_code == 409


def test_approve_nonexistent_quotation(client, reviewer_headers):
    """Approving a nonexistent request_id returns 404."""
    resp = client.post(
        "/api/v1/internal/quotations/nonexistent-req/approve",
        json={},
        headers=reviewer_headers,
    )
    assert resp.status_code == 404


def test_double_approval_rejected(client, customer_headers, reviewer_headers):
    """Approving an already-approved draft returns 409."""
    _create_rtx8_quotation(
        client, customer_headers, "req-double-approve", "conv-double-approve"
    )
    client.post(
        "/api/v1/internal/quotations/req-double-approve/approve",
        json={},
        headers=reviewer_headers,
    )
    resp2 = client.post(
        "/api/v1/internal/quotations/req-double-approve/approve",
        json={},
        headers=reviewer_headers,
    )
    assert resp2.status_code == 409
