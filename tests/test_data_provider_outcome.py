from app.api.v1.endpoints.data import _classify_provider_outcome
from app.models.transaction import TransactionStatus


def test_classify_provider_outcome_success_from_string_flag_and_message():
    status, reason = _classify_provider_outcome(
        {
            "success": "true",
            "status": "",
            "message": "Dear Customer, You have successfully gifted 1GB data.",
        }
    )
    assert status == TransactionStatus.SUCCESS
    assert reason == ""


def test_classify_provider_outcome_failed_when_provider_signals_failure():
    status, reason = _classify_provider_outcome(
        {
            "success": False,
            "status": "failed",
            "message": "Plan not available.",
        }
    )
    assert status == TransactionStatus.FAILED
    assert "plan not available" in reason.lower()


def test_classify_provider_outcome_pending_when_signals_are_ambiguous():
    status, reason = _classify_provider_outcome(
        {
            "success": False,
            "status": "processing",
            "message": "Request submitted successfully and is pending.",
        }
    )
    assert status == TransactionStatus.PENDING
    assert reason == ""
