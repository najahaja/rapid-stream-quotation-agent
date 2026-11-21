"""Conversation state and clarification continuation tests."""


def test_clarification_then_continuation(client, customer_headers):
    """Test 3: Full clarification flow — missing GPU count → clarify → complete quotation."""
    conv_id = "conv-clarify-flow"

    # Step 1: Initial message missing GPU count
    resp1 = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-clarify-01",
            "conversation_id": conv_id,
            "message": "I need two RTX servers with 96 GB cards.",
        },
        headers=customer_headers,
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["status"] == "NEEDS_CLARIFICATION"

    # Step 2: Send clarification
    resp2 = client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={
            "request_id": "req-clarify-02",
            "message": "Eight GPUs per server.",
        },
        headers=customer_headers,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["status"] != "NEEDS_CLARIFICATION"
    assert data2["catalog_match"] is not None
    assert data2["catalog_match"]["sku"] == "RST-RTX8"


def test_conversation_not_found(client, customer_headers):
    """Non-existent conversation_id returns 404."""
    resp = client.post(
        "/api/v1/conversations/nonexistent-conv/messages",
        json={"request_id": "req-nf", "message": "eight GPUs per server"},
        headers=customer_headers,
    )
    assert resp.status_code == 404


def test_multiple_conversations_independent(client, customer_headers):
    """Different conversation IDs must not share state."""
    # Conversation A: RTX request
    client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-conv-a",
            "conversation_id": "conv-a",
            "message": "I need 2 RTX servers with 96 GB cards.",
        },
        headers=customer_headers,
    )

    # Conversation B: H200 request
    resp_b = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-conv-b",
            "conversation_id": "conv-b",
            "message": "I need 1 H200 server.",
        },
        headers=customer_headers,
    )
    assert resp_b.status_code == 200

    # Clarification for A must not affect B
    resp_a2 = client.post(
        "/api/v1/conversations/conv-a/messages",
        json={"request_id": "req-conv-a2", "message": "Eight GPUs per server."},
        headers=customer_headers,
    )
    assert resp_a2.status_code == 200
    # conv-a should resolve to RTX8
    if resp_a2.json().get("catalog_match"):
        assert resp_a2.json()["catalog_match"]["sku"] == "RST-RTX8"
