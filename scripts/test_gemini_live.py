"""
CLI tool to test live Google Gemini extraction against the real Gemini API.

Usage:
  1. Add your real GEMINI_API_KEY to .env or environment variable:
     $env:GEMINI_API_KEY = "your_actual_api_key"
  2. Run:
     python scripts/test_gemini_live.py "I need two RTX servers with 8 GPUs and 96GB memory."
"""

import os
import sys

# Ensure project root is in Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.services.gemini_provider import (
    PLACEHOLDER_KEYS,
    GeminiAuthenticationError,
    GeminiExtractionProvider,
)


def main():
    test_message = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "I need two RST-RTX8 servers for high-performance deep learning."
    )

    api_key = (
        getattr(settings, "gemini_api_key", None)
        or os.environ.get("GEMINI_API_KEY", "")
        or ""
    ).strip()

    if not api_key or api_key.lower() in PLACEHOLDER_KEYS:
        print("\n" + "=" * 70)
        print(" [!] GEMINI API KEY REQUIRED FOR LIVE TEST")
        print("=" * 70)
        print("To run a live test with Google Gemini:")
        print("1. Obtain an API key from: https://aistudio.google.com/")
        print("2. Set the environment variable in PowerShell:")
        print('   $env:GEMINI_API_KEY = "AIzaSy..."')
        print("   OR update GEMINI_API_KEY in your .env file.")
        print('3. Run: python scripts/test_gemini_live.py "I need 2 RST-RTX8 servers."')
        print("=" * 70 + "\n")
        sys.exit(1)

    print("\n" + "=" * 70)
    print(" RST Quotation Agent — Live Gemini Extraction Test")
    print("=" * 70)
    print(f"Model: {settings.gemini_model}")
    print(f"Input Inquiry: {test_message}")
    print("Calling Google Gemini via google-genai SDK...")

    try:
        provider = GeminiExtractionProvider(
            api_key=api_key, model=settings.gemini_model
        )
        result = provider.extract(test_message)
        print("\n[+] SUCCESS! Gemini Extraction Result:")
        print(result.model_dump_json(indent=2))
        print("=" * 70 + "\n")
    except GeminiAuthenticationError as auth_err:
        print(f"\n[-] Authentication failed: {auth_err}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n[-] Live test failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
