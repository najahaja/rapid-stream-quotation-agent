"""Stock management tests."""

from app.services.inventory import check_stock


def test_full_stock_available():
    """Test: RST-RTX8 has 2 in stock — requesting 2 should fully fulfill."""
    result = check_stock("RST-RTX8", 2)
    assert result.can_fulfill is True
    assert result.shortage == 0
    assert result.requires_sales_review is False


def test_stock_shortage(client, customer_headers):
    """Test 6: Requesting 3 RST-RTX8 (stock=2) → shortage of 1."""
    resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-shortage",
            "conversation_id": "conv-shortage",
            "message": "I need 3 RST-RTX8 servers.",
        },
        headers=customer_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "PENDING_SALES_REVIEW"
    assert data["stock_check"]["shortage"] == 1
    assert data["stock_check"]["available"] == 2


def test_zero_stock_product(client, customer_headers):
    """Test 7: RST-H2004 has 0 stock — must route to sales."""
    resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-zerostock",
            "conversation_id": "conv-zerostock",
            "message": "I need 1 RST-H2004 server.",
        },
        headers=customer_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "PENDING_SALES_REVIEW"
    assert data["stock_check"]["available"] == 0


def test_zero_stock_no_false_promise():
    """Test: Zero stock must report requires_sales_review=True."""
    result = check_stock("RST-H2004", 1)
    assert result.can_fulfill is False
    assert result.requires_sales_review is True


def test_stock_shortage_function():
    """Test 6 (unit): requesting 3 of RST-RTX8 (stock=2)."""
    result = check_stock("RST-RTX8", 3)
    assert result.shortage == 1
    assert result.can_fulfill is False
    assert result.requires_sales_review is True
