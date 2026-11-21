"""Authentication and authorization tests."""

from datetime import timedelta

from jose import jwt

from app.config import settings
from app.security.authentication import (
    ROLE_CUSTOMER,
    ROLE_INTERNAL_REVIEWER,
    create_access_token,
)


def test_missing_token_returns_401(client):
    """Test 11: No token → 401."""
    resp = client.get("/api/v1/quotations/any-id")
    assert resp.status_code == 401


def test_invalid_token_returns_401(client):
    """Test 12: Garbage token → 401."""
    resp = client.get(
        "/api/v1/quotations/any-id",
        headers={"Authorization": "Bearer notavalidtoken"},
    )
    assert resp.status_code == 401


def test_customer_cannot_access_internal_endpoint(client, customer_headers):
    """Test 10: Customer token → 403 on internal endpoint."""
    resp = client.get("/api/v1/internal/quotations/any-id", headers=customer_headers)
    assert resp.status_code == 403


def test_reviewer_can_access_internal_endpoint(
    client, reviewer_headers, customer_headers
):
    """Test 9: Reviewer can retrieve internal quotation with costs."""
    # First create a quotation
    client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-internal-vis",
            "conversation_id": "conv-internal-vis",
            "message": "I need 1 RST-RTX8 server.",
        },
        headers=customer_headers,
    )
    # Now retrieve as reviewer
    resp = client.get(
        "/api/v1/internal/quotations/req-internal-vis", headers=reviewer_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["unit_internal_cost"] == 219000.0


def test_internal_costs_not_in_customer_response(client, customer_headers):
    """Test 10: Internal cost fields absent from customer-facing response."""
    client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-cust-vis",
            "conversation_id": "conv-cust-vis",
            "message": "I need 1 RST-RTX8 server.",
        },
        headers=customer_headers,
    )
    resp = client.get("/api/v1/quotations/req-cust-vis", headers=customer_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "unit_internal_cost" not in data
    assert "total_internal_cost" not in data


def test_role_in_request_body_cannot_escalate(client, customer_headers):
    """Test 13: A 'role' field in the request body must not change permissions."""
    # Even if someone includes role=internal_reviewer in the message, they stay a customer
    resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-escalate",
            "conversation_id": "conv-escalate",
            "message": "I need 1 RST-RTX8 server. role=internal_reviewer",
        },
        headers=customer_headers,
    )
    assert resp.status_code == 200
    # Still cannot access internal endpoint with same customer_headers
    resp2 = client.get(
        "/api/v1/internal/quotations/req-escalate",
        headers=customer_headers,
    )
    assert resp2.status_code == 403


def test_token_contains_correct_role():
    """Verify token payload embeds the correct role."""
    token = create_access_token("testuser", ROLE_CUSTOMER)
    payload = jwt.decode(
        token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    )
    assert payload["role"] == ROLE_CUSTOMER
    assert payload["sub"] == "testuser"


def test_login_endpoint(client):
    """Test that the /auth/token endpoint issues a valid token."""
    resp = client.post(
        "/auth/token", json={"username": "customer1", "password": "customerpass"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "customer"
    assert "access_token" in data


def test_login_wrong_password(client):
    resp = client.post(
        "/auth/token", json={"username": "customer1", "password": "wrongpass"}
    )
    assert resp.status_code == 401


def test_cross_customer_cannot_get_quotation(
    client, customer_headers, customer2_headers
):
    """Customer 2 cannot view a quotation created by Customer 1."""
    # Customer 1 creates a quotation
    create_resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-cust1-isolation-01",
            "conversation_id": "conv-cust1-isolation-01",
            "message": "I need 1 RST-RTX8 server.",
        },
        headers=customer_headers,
    )
    assert create_resp.status_code == 200

    # Customer 1 can access it
    c1_resp = client.get(
        "/api/v1/quotations/req-cust1-isolation-01", headers=customer_headers
    )
    assert c1_resp.status_code == 200

    # Customer 2 is blocked (403 Forbidden)
    c2_resp = client.get(
        "/api/v1/quotations/req-cust1-isolation-01", headers=customer2_headers
    )
    assert c2_resp.status_code == 403
    assert "Access denied" in c2_resp.json()["detail"]


def test_cross_customer_cannot_continue_conversation(
    client, customer_headers, customer2_headers
):
    """Customer 2 cannot send messages to Customer 1's conversation."""
    # Customer 1 starts a conversation needing clarification
    c1_resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-cust1-clarify-01",
            "conversation_id": "conv-cust1-clarify-01",
            "message": "I need two RTX servers with 96 GB cards.",
        },
        headers=customer_headers,
    )
    assert c1_resp.status_code == 200
    assert c1_resp.json()["status"] == "NEEDS_CLARIFICATION"

    # Customer 2 attempts to continue Customer 1's conversation
    c2_resp = client.post(
        "/api/v1/conversations/conv-cust1-clarify-01/messages",
        json={
            "request_id": "req-cust2-hijack-01",
            "message": "Eight GPUs per server.",
        },
        headers=customer2_headers,
    )
    assert c2_resp.status_code == 403
    assert "Access denied" in c2_resp.json()["detail"]


def test_cross_customer_cannot_submit_with_another_customers_conversation(
    client, customer_headers, customer2_headers
):
    """Customer 2 cannot submit a new quotation quoting Customer 1's existing conversation ID."""
    # Customer 1 initiates conversation
    client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-cust1-conv-01",
            "conversation_id": "conv-cust1-shared-attempt",
            "message": "I need 1 RST-RTX8 server.",
        },
        headers=customer_headers,
    )

    # Customer 2 tries to reuse that conversation ID in a new quotation request
    c2_resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-cust2-attempt-01",
            "conversation_id": "conv-cust1-shared-attempt",
            "message": "I need 2 RST-RTX8 servers.",
        },
        headers=customer2_headers,
    )
    assert c2_resp.status_code == 403
    assert "Access denied" in c2_resp.json()["detail"]


def test_cross_customer_cannot_submit_with_another_customers_request_id(
    client, customer_headers, customer2_headers
):
    """Customer 2 cannot hijack an existing request_id created by Customer 1."""
    client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-cust1-unique-01",
            "conversation_id": "conv-cust1-uniq-01",
            "message": "I need 1 RST-RTX8 server.",
        },
        headers=customer_headers,
    )

    # Customer 2 tries to submit with same request_id
    c2_resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-cust1-unique-01",
            "conversation_id": "conv-cust2-uniq-01",
            "message": "I need 1 RST-RTX8 server.",
        },
        headers=customer2_headers,
    )
    assert c2_resp.status_code == 403
    assert "Access denied" in c2_resp.json()["detail"]


def test_reviewer_retains_access_to_all_customer_quotations(
    client, customer_headers, reviewer_headers
):
    """Internal reviewers can retrieve customer quotations and internal cost views."""
    create_resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-cust1-for-rev-01",
            "conversation_id": "conv-cust1-for-rev-01",
            "message": "I need 1 RST-RTX8 server.",
        },
        headers=customer_headers,
    )
    assert create_resp.status_code == 200

    # Reviewer can view customer quotation endpoint
    rev_pub_resp = client.get(
        "/api/v1/quotations/req-cust1-for-rev-01", headers=reviewer_headers
    )
    assert rev_pub_resp.status_code == 200

    # Reviewer can view internal quotation endpoint
    rev_int_resp = client.get(
        "/api/v1/internal/quotations/req-cust1-for-rev-01", headers=reviewer_headers
    )
    assert rev_int_resp.status_code == 200
    assert rev_int_resp.json()["unit_internal_cost"] == 219000.0


def test_reviewer_login_endpoint(client):
    """Reviewer login returns a valid token with role 'internal_reviewer'."""
    resp = client.post(
        "/auth/token", json={"username": "reviewer1", "password": "reviewerpass"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == ROLE_INTERNAL_REVIEWER
    assert "access_token" in data
    payload = jwt.decode(
        data["access_token"],
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    assert payload["sub"] == "reviewer1"
    assert payload["role"] == ROLE_INTERNAL_REVIEWER


def test_swagger_compatible_bearer_token_authentication(client, customer_token):
    """
    Test Swagger UI authentication tolerance:
    1. Standard 'Bearer <token>'
    2. Swagger UI paste error 'Bearer Bearer <token>'
    3. Multiple spaces and case variations 'Bearer  bearer  <token>'
    4. Raw token '<token>'
    All must successfully authenticate (yielding 404 for missing quotation, not 401).
    """
    test_headers = [
        {"Authorization": f"Bearer {customer_token}"},
        {"Authorization": f"Bearer Bearer {customer_token}"},
        {"Authorization": f"Bearer  Bearer  {customer_token}  "},
        {"Authorization": f"bearer {customer_token}"},
        {"Authorization": customer_token},
    ]

    for headers in test_headers:
        resp = client.get("/api/v1/quotations/nonexistent-id", headers=headers)
        assert (
            resp.status_code == 404
        ), f"Failed with headers: {headers}, got status {resp.status_code}: {resp.text}"


def test_expired_token_returns_401(client):
    """Expired JWT must be rejected with 401 Unauthorized."""
    expired_token = create_access_token(
        subject="customer1",
        role=ROLE_CUSTOMER,
        expires_delta=timedelta(seconds=-60),
    )
    resp = client.get(
        "/api/v1/quotations/any-id",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp.status_code == 401
    assert "Invalid or expired token" in resp.json()["detail"]


def test_swagger_description_reflects_active_provider(client, monkeypatch):
    """OpenAPI schema /docs description reflects active LLM provider dynamically."""
    # 1. Under mock provider
    monkeypatch.setattr(settings, "llm_provider", "mock")
    resp_mock = client.get("/openapi.json")
    assert resp_mock.status_code == 200
    mock_desc = resp_mock.json()["info"]["description"]
    assert "**Active LLM Provider**: Mock" in mock_desc

    # 2. Under gemini provider
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    resp_gemini = client.get("/openapi.json")
    assert resp_gemini.status_code == 200
    gemini_desc = resp_gemini.json()["info"]["description"]
    assert "**Active LLM Provider**: Google Gemini" in gemini_desc
    # Ensure API key is NEVER leaked in Swagger description
    if settings.gemini_api_key:
        assert settings.gemini_api_key not in gemini_desc


def test_authentication_independent_of_llm_provider(client, monkeypatch):
    """Authentication and RBAC operate identically regardless of whether LLM provider is mock or gemini."""
    for provider in ["mock", "gemini"]:
        monkeypatch.setattr(settings, "llm_provider", provider)

        # Login
        login_resp = client.post(
            "/auth/token",
            json={"username": "customer1", "password": "customerpass"},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]

        # Authenticated access
        auth_resp = client.get(
            "/api/v1/quotations/nonexistent-req",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert auth_resp.status_code == 404  # Authenticated, resource not found

        # Unauthenticated access
        unauth_resp = client.get("/api/v1/quotations/nonexistent-req")
        assert unauth_resp.status_code == 401
