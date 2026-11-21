"""End-to-end quotation workflow tests."""


def test_successful_rtx8_quotation(client, customer_headers):
    """Test 1: Successful RST-RTX8 quotation with all details."""
    resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-001",
            "conversation_id": "conv-001",
            "message": "I need 2 RST-RTX8 servers.",
        },
        headers=customer_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "PENDING_APPROVAL"
    assert data["unit_selling_price"] == 245000.0
    assert data["total_price"] == 490000.0
    assert data["catalog_match"]["sku"] == "RST-RTX8"
    assert data["draft_id"] is not None
    assert data["price_pending"] is False


def test_clarification_requested_missing_gpu_count(client, customer_headers):
    """Test 2: Returns NEEDS_CLARIFICATION when GPU count is missing."""
    resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-002",
            "conversation_id": "conv-002",
            "message": "I need two RTX servers with 96 GB cards.",
        },
        headers=customer_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "NEEDS_CLARIFICATION"
    assert data["clarification_question"] is not None
    assert "gpu" in data["clarification_question"].lower()


def test_pending_price_product(client, customer_headers):
    """Test 8: RST-B3008 has pending price — no total should be calculated."""
    resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-b300",
            "conversation_id": "conv-b300",
            "message": "I need 1 RST-B3008 server.",
        },
        headers=customer_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["price_pending"] is True
    assert data["unit_selling_price"] is None
    assert data["total_price"] is None
    assert data["status"] == "PENDING_SALES_REVIEW"


def test_unsupported_configuration(client, customer_headers):
    """Test 5: Unsupported config returns no catalog match."""
    resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-unsupported",
            "conversation_id": "conv-unsupported",
            "message": "I need a server with 999 GPUs per server.",
        },
        headers=customer_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    # Either no catalog match or pending sales review
    assert data["status"] in ("PENDING_SALES_REVIEW", "NEEDS_CLARIFICATION")


def test_invalid_request_body(client, customer_headers):
    """Test 20: Missing required fields returns 422."""
    resp = client.post(
        "/api/v1/quotations",
        json={"message": "hello"},  # Missing request_id and conversation_id
        headers=customer_headers,
    )
    assert resp.status_code == 422


def test_missing_auth_token(client):
    """Test 11: No token returns 401."""
    resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-noauth",
            "conversation_id": "conv-noauth",
            "message": "I need a server.",
        },
    )
    assert resp.status_code == 401


def test_no_internal_costs_in_customer_response(client, customer_headers):
    """Test 10: Customer response must not contain internal cost fields."""
    resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-costcheck",
            "conversation_id": "conv-costcheck",
            "message": "I need 1 RST-RTX8 server.",
        },
        headers=customer_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    # These keys must not appear in customer response
    assert "unit_internal_cost" not in data
    assert "total_internal_cost" not in data
    assert "gross_margin" not in data
    assert "internal_cost" not in str(data)
