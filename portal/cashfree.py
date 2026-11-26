import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request

from django.conf import settings


class CashfreeError(Exception):
    """Raised when a Cashfree API call fails or credentials are missing."""


def is_configured() -> bool:
    return bool(settings.CASHFREE_APP_ID and settings.CASHFREE_SECRET_KEY)


def get_base_url() -> str:
    if getattr(settings, "CASHFREE_ENV", "sandbox").lower() == "production":
        return "https://api.cashfree.com/pg"
    return "https://sandbox.cashfree.com/pg"


def _headers() -> dict:
    if not is_configured():
        raise CashfreeError(
            "Cashfree credentials are not configured. Set CASHFREE_APP_ID and CASHFREE_SECRET_KEY in .env."
        )
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-api-version": getattr(settings, "CASHFREE_API_VERSION", "2025-01-01"),
        "x-client-id": settings.CASHFREE_APP_ID,
        "x-client-secret": settings.CASHFREE_SECRET_KEY,
    }


def create_order(
    *,
    order_id: str,
    amount: int,
    customer_id: str,
    customer_name: str,
    customer_phone: str,
    return_url: str,
    notify_url: str | None = None,
) -> dict:
    """Create a Cashfree Payment Gateway order and return the API response."""
    payload = {
        "order_id": order_id,
        "order_amount": float(amount),
        "order_currency": "INR",
        "customer_details": {
            "customer_id": customer_id,
            "customer_name": customer_name,
            "customer_phone": customer_phone,
        },
        "order_meta": {
            "return_url": return_url,
        },
    }
    if notify_url:
        payload["order_meta"]["notify_url"] = notify_url

    req = urllib.request.Request(
        f"{get_base_url()}/orders",
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )
    return _send_request(req)


def fetch_order(order_id: str) -> dict:
    """Fetch an order's current status from Cashfree."""
    req = urllib.request.Request(
        f"{get_base_url()}/orders/{order_id}",
        headers=_headers(),
        method="GET",
    )
    return _send_request(req)


def _send_request(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise CashfreeError(f"Cashfree API error ({exc.code}): {body}") from exc
    except urllib.error.URLError as exc:
        raise CashfreeError(f"Cashfree network error: {exc.reason}") from exc


def verify_webhook_signature(
    webhook_signature: str | None,
    timestamp: str | None,
    raw_body: bytes,
) -> bool:
    """Verify Cashfree HMAC-SHA256 webhook signature against raw request body."""
    secret = getattr(settings, "CASHFREE_SECRET_KEY", "")
    if not secret or not webhook_signature or not timestamp or not raw_body:
        return False

    signed_payload = timestamp.encode("utf-8") + raw_body
    digest = hmac.new(
        secret.encode("utf-8"), signed_payload, hashlib.sha256
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, webhook_signature)
