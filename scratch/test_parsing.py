from dataclasses import dataclass
from typing import Optional

@dataclass
class ProviderResult:
    success: bool
    external_reference: str | None = None
    message: str | None = None
    meta: dict | None = None

_CLUBKONNECT_PENDING_CODES = {100, 201, 300, 600, 601, 602, 603, 604, 605, 606}
_CLUBKONNECT_SUCCESS_CODES = {100, 199, 200, 201, 300}
_CLUBKONNECT_FAILURE_CODES = {399, 400, 401} # truncated for test

def _status_code_and_text(data: dict):
    raw_code = data.get("statuscode") or data.get("status_code") or data.get("StatusCode")
    code_text = str(raw_code or "").strip()
    code = int(code_text) if code_text.isdigit() else None

    raw_status = data.get("orderstatus") or data.get("status") or data.get("Status")
    status_text = str(raw_status or "").strip().upper()

    if not status_text and code is not None:
        status_text = str(code)
    if code is None and status_text.isdigit():
        code = int(status_text)
    return code, status_text

def _extract_reference(data: dict):
    for key in ("OrderID", "orderid", "order_id", "RequestID", "requestid", "request_id", "reference"):
        val = str(data.get(key) or "").strip()
        if val:
            return val
    return None

def _parse_result(data: dict, action: str):
    code, status_text = _status_code_and_text(data)
    external_reference = _extract_reference(data)
    message = str(
        data.get("remark")
        or data.get("message")
        or data.get("orderstatus")
        or data.get("status")
        or ""
    ).strip()

    pending = (
        (code in _CLUBKONNECT_PENDING_CODES if code is not None else False)
        or status_text in {"ORDER_RECEIVED", "ORDER_PROCESSED", "ORDER_ONHOLD", "PENDING", "TXN_HISTORY", "PROCESSING", "SUBMITTED"}
    )
    success = (
        (code in _CLUBKONNECT_SUCCESS_CODES if code is not None else False)
        or status_text in {"ORDER_COMPLETED", "SUCCESS", "TRANSACTION_SUCCESSFUL"}
    )
    failed = (
        (code in _CLUBKONNECT_FAILURE_CODES if code is not None else False)
        or "INVALID" in status_text
        or "ERROR" in status_text
        or "FAILED" in status_text
        or "CANCEL" in status_text
    )

    meta = {
        "clubkonnect": {
            "status": "pending" if pending else ("success" if success and not failed else "failed"),
            "raw_status": str(data.get("status") or ""),
            "code": code,
            "action": action,
            "raw": data,
        }
    }

    if pending and not failed:
        return ProviderResult(False, external_reference=external_reference, message="Transaction pending", meta=meta)
    if success and not failed:
        return ProviderResult(True, external_reference=external_reference, message=message or "Successful", meta=meta)
    return ProviderResult(False, external_reference=external_reference, message=message or "Provider failed", meta=meta)

def test():
    # Simulate ClubKonnect response
    data = {"status": "TXN_HISTORY"}
    res = _parse_result(data, "electricity")
    print(f"Result for {data}:")
    print(f"  Success: {res.success}")
    print(f"  Message: {res.message}")
    print(f"  Status in Meta: {res.meta['clubkonnect']['status']}")

    data2 = {"status": "100"} # Code in status field
    res2 = _parse_result(data2, "electricity")
    print(f"\nResult for {data2}:")
    print(f"  Success: {res2.success}")
    print(f"  Message: {res2.message}")
    print(f"  Status in Meta: {res2.meta['clubkonnect']['status']}")

if __name__ == "__main__":
    test()
