"""
Live verification script for Swagger UI authentication and dynamic OpenAPI description.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)


def verify_authentication():
    print("=" * 60)
    print(" 1. CUSTOMER LOGIN VIA /auth/token")
    print("=" * 60)
    login_res = client.post(
        "/auth/token",
        json={"username": "customer1", "password": "customerpass"},
    )
    assert login_res.status_code == 200, f"Login failed: {login_res.text}"
    cust_token = login_res.json()["access_token"]
    print(
        f"Customer Token obtained: {cust_token[:30]}... (role: {login_res.json()['role']})"
    )

    print("\n" + "=" * 60)
    print(" 2. SUBMIT QUOTATION WITH SWAGGER-STYLE 'Bearer Bearer [token]'")
    print("=" * 60)
    swagger_headers = {"Authorization": f"Bearer Bearer {cust_token}"}
    quote_res = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-swagger-test-01",
            "conversation_id": "conv-swagger-test-01",
            "message": "I need two RST-RTX8 servers.",
        },
        headers=swagger_headers,
    )
    assert (
        quote_res.status_code == 200
    ), f"Quotation submission failed: {quote_res.status_code} {quote_res.text}"
    data = quote_res.json()
    print("HTTP Status Code:", quote_res.status_code)
    print("Quotation Status:", data["status"])
    print("Catalog Match SKU:", data["catalog_match"]["sku"])
    print(f"Total Selling Price: ${data['total_price']:,.2f}")

    print("\n" + "=" * 60)
    print(" 3. GET QUOTATION WITH RAW TOKEN 'Authorization: [token]'")
    print("=" * 60)
    raw_headers = {"Authorization": cust_token}
    get_res = client.get("/api/v1/quotations/req-swagger-test-01", headers=raw_headers)
    assert (
        get_res.status_code == 200
    ), f"Get quotation failed: {get_res.status_code} {get_res.text}"
    print("HTTP Status Code:", get_res.status_code)
    print("Quotation Message:", get_res.json()["message"])

    print("\n" + "=" * 60)
    print(" 4. REVIEWER LOGIN & INTERNAL COST RETRIEVAL")
    print("=" * 60)
    rev_login = client.post(
        "/auth/token",
        json={"username": "reviewer1", "password": "reviewerpass"},
    )
    assert rev_login.status_code == 200
    rev_token = rev_login.json()["access_token"]
    rev_headers = {"Authorization": f"Bearer Bearer {rev_token}"}
    int_res = client.get(
        "/api/v1/internal/quotations/req-swagger-test-01", headers=rev_headers
    )
    assert int_res.status_code == 200
    print("Reviewer accessed internal quotation successfully!")
    print(f"Unit Internal Cost: ${int_res.json()['unit_internal_cost']:,.2f}")
    print(f"Gross Margin:        ${int_res.json()['gross_margin']:,.2f}")

    print("\n" + "=" * 60)
    print(" 5. DYNAMIC OPENAPI DESCRIPTION VERIFICATION")
    print("=" * 60)
    # Check current provider
    openapi_res = client.get("/openapi.json")
    desc = openapi_res.json()["info"]["description"]
    print(f"Active settings.llm_provider: {settings.llm_provider}")
    print("OpenAPI Description excerpt:")
    for line in desc.splitlines()[:10]:
        print("  ", line)

    print("\n[+] ALL LIVE AUTHENTICATED API REQUESTS VERIFIED SUCCESSFULLY!")


if __name__ == "__main__":
    verify_authentication()
