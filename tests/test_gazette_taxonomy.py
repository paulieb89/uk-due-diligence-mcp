"""Regression tests for authoritative Gazette corporate-insolvency notice labels."""

from gazette import NOTICE_LABELS, SEVERITY, _extract_notices


def _entry(code: str, notice_id: str) -> dict:
    return {
        "f:notice-code": code,
        "id": f"https://www.thegazette.co.uk/id/notice/{notice_id}",
        "published": "2024-04-26T01:05:01Z",
        "title": "MEL PRECISION LIMITED",
        "content": "<p>Example notice</p>",
    }


def test_mel_notice_codes_match_authoritative_gazette_taxonomy():
    notices = _extract_notices(
        [
            _entry("2443", "4611709"),
            _entry("2441", "4611652"),
            _entry("2450", "4605680"),
            _entry("2442", "4582848"),
        ]
    )
    by_code = {n["notice_code"]: n for n in notices}

    assert by_code["2443"]["notice_type"] == "Appointment of liquidators (creditors' voluntary)"
    assert by_code["2441"]["notice_type"] == "Resolution for winding up (creditors' voluntary)"
    assert by_code["2450"]["notice_type"] == "Petitions to wind up (companies)"
    assert by_code["2442"]["notice_type"] == "Meetings of creditors (creditors' voluntary)"


def test_winding_up_order_is_2452_and_highest_severity():
    assert NOTICE_LABELS["2452"] == "Winding up order (companies)"
    assert SEVERITY["2452"] == 10
    assert SEVERITY["2450"] < SEVERITY["2452"]


def test_members_voluntary_winding_up_is_not_treated_as_high_distress():
    assert NOTICE_LABELS["2431"] == "Resolution for winding up (members' voluntary)"
    assert SEVERITY["2431"] <= 1
