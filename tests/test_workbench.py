import json
from pathlib import Path
import pytest
from edi import analyze, classify_request
from app import create_app

ROOT = Path(__file__).parents[1]


@pytest.fixture
def valid():
    return (ROOT / "samples/valid.edi").read_text()


def codes(text):
    return {f["code"] for f in analyze(text)["findings"]}


def test_valid(valid):
    r = analyze(valid)
    assert r["status"] == "checks_passed"
    assert r["members"][0]["member_id"] == "SYN001"


@pytest.mark.parametrize(
    "before,after,expected",
    [
        ("ST*834", "ST*835", "TRANSACTION_TYPE"),
        ("SE*8*0001", "SE*7*0001", "SEGMENT_COUNT"),
        ("SE*8*0001", "SE*8*9999", "CONTROL_NUMBER"),
        ("REF*0F*SYN001", "REF*0F*", "MISSING_MEMBER_ID"),
        ("20261101", "20260230", "INVALID_DATE"),
        ("DTP*348", "DTP*349", "MISSING_BEGIN"),
        ("HD*021", "HD*024", "MISSING_END"),
        ("INS*Y*18*021", "INS*Y*18*999", "UNSUPPORTED_ACTION"),
        ("HD*021**HLT*DEMO-PPO", "HD*021**HLT*", "COVERAGE_HEADER"),
    ],
)
def test_seeded_failure(valid, before, after, expected):
    assert expected in codes(valid.replace(before, after))


def test_duplicate(valid):
    duplicate = valid.replace(
        "SE*8*0001", valid.split("INS", 1)[1].split("SE")[0].join(["INS", "SE*13*0001"])
    )
    assert "DUPLICATE_MEMBER" in codes(duplicate)


def test_scope_and_orphan(valid):
    assert "SCOPE" in codes("ISA*DEMO~" + valid)
    assert "ORPHAN_SEGMENT" in codes("HD*021**HLT*P~" + valid)


def test_date_order(valid):
    assert "DATE_ORDER" in codes(valid.replace("SE*8", "DTP*349*D8*20251001~SE*9"))


def test_termination():
    assert analyze((ROOT / "samples/termination.edi").read_text())["errors"] == 0


@pytest.mark.parametrize("value", [None, "", 42, " " * 10])
def test_invalid_input(value):
    with pytest.raises(ValueError):
        analyze(value)


@pytest.mark.parametrize(
    "case", json.loads((ROOT / "samples/intents.json").read_text())
)
def test_intents(case):
    assert classify_request(case["text"])["intent"] == case["expected"]


def test_review_persistence(tmp_path):
    path = str(tmp_path / "test.db")
    client = create_app(path).test_client()
    content = (ROOT / "samples/invalid.edi").read_text()
    r = client.post("/api/analyze", json={"edi": content}).json
    identity = r["id"]
    fid = r["findings"][0]["id"]
    assert (
        client.post(
            "/api/analyses/" + identity + "/reviews",
            json={
                "finding_id": "foreign",
                "decision": "acknowledged",
                "actor": "Analyst",
                "note": "Reviewed",
            },
        ).status_code
        == 400
    )
    body = {
        "finding_id": fid,
        "decision": "needs_follow_up",
        "actor": "Analyst",
        "note": "Request corrected enrollment record",
    }
    assert (
        client.post("/api/analyses/" + identity + "/reviews", json=body).status_code
        == 201
    )
    other = create_app(path).test_client()
    r = other.get("/api/analyses/" + identity).json
    assert len(r["reviews"]) == 1 and len(r["findings"]) > 0
    assert other.post("/api/analyze", json={"edi": content}).json["id"] == identity
    csv = other.get("/api/analyses/" + identity + "/export.csv")
    assert csv.status_code == 200 and b"INVALID_DATE" in csv.data
    assert other.get("/").status_code == 200
