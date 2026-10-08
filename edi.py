"""A deliberately bounded 834 transaction workbench, using synthetic data."""

from datetime import datetime
import hashlib
import re

RULESET = "synthetic-enrollment-v1"
MAINTENANCE = {
    "001": "change",
    "021": "add",
    "024": "terminate",
    "025": "reinstate",
    "030": "audit",
}


def classify_request(text):
    """Transparent baseline; ambiguous or negated requests go to human review."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    intents = {
        "add": {"add", "enroll", "enrol", "register"},
        "terminate": {"terminate", "cancel", "remove", "disenroll"},
        "change": {"change", "update", "correct", "modify"},
        "reinstate": {"reinstate", "restore", "reactivate"},
    }
    matches = [key for key, vocabulary in intents.items() if words & vocabulary]
    negated = bool(words & {"not", "never", "dont", "don", "without"})
    accepted = len(matches) == 1 and not negated
    return {
        "intent": matches[0] if accepted else "review",
        "method": "keyword-baseline",
        "matched_intents": matches,
        "needs_review": not accepted,
        "reason": (
            "One supported action matched."
            if accepted
            else "Ambiguous, negated or unsupported request."
        ),
    }


def analyze(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Provide a non-empty EDI transaction.")
    if len(text.encode()) > 1_000_000:
        raise ValueError("The demo accepts at most 1 MB.")
    raw = [s.strip() for s in text.strip().split("~") if s.strip()]
    segments = [
        {"index": i + 1, "tag": s.split("*")[0], "elements": s.split("*")[1:], "raw": s}
        for i, s in enumerate(raw)
    ]
    findings, members = [], []
    current = None

    def issue(code, severity, message, seg=None, member=None):
        index = seg["index"] if seg else 0
        fid = hashlib.sha256(f"{code}:{index}:{member}:{message}".encode()).hexdigest()[
            :16
        ]
        findings.append(
            {
                "id": fid,
                "code": code,
                "severity": severity,
                "message": message,
                "segment": index,
                "member": member,
                "evidence": seg["raw"] if seg else "",
            }
        )

    starts = [s for s in segments if s["tag"] == "ST"]
    ends = [s for s in segments if s["tag"] == "SE"]
    if len(starts) != 1 or len(ends) != 1:
        issue("ENVELOPE", "error", "Exactly one ST/SE transaction is supported.")
    else:
        st, se = starts[0], ends[0]
        if len(st["elements"]) < 2 or st["elements"][0] != "834":
            issue("TRANSACTION_TYPE", "error", "Expected an ST*834 transaction.", st)
        if (
            len(se["elements"]) < 2
            or len(st["elements"]) < 2
            or st["elements"][1] != se["elements"][1]
        ):
            issue("CONTROL_NUMBER", "error", "ST02 and SE02 must agree.", se)
        count = se["index"] - st["index"] + 1
        if (
            not se["elements"]
            or not se["elements"][0].isdigit()
            or int(se["elements"][0]) != count
            or count < 2
        ):
            issue(
                "SEGMENT_COUNT",
                "error",
                "SE01 must equal the inclusive ST-to-SE segment count.",
                se,
            )
        if st["index"] != 1 or se["index"] != len(segments):
            issue(
                "SCOPE",
                "error",
                "Submit an ST-to-SE extract; ISA/GS envelopes and trailing segments are outside this demo.",
            )

    for seg in segments:
        tag, e = seg["tag"], seg["elements"]
        if tag == "INS":
            current = {
                "ordinal": len(members) + 1,
                "member_id": "",
                "name": "",
                "maintenance": e[2] if len(e) > 2 else "",
                "coverage": [],
                "segments": [seg["index"]],
                "source": seg,
            }
            members.append(current)
            if len(e) < 3 or e[0] not in {"Y", "N"}:
                issue(
                    "MEMBER_HEADER",
                    "error",
                    "INS needs a subscriber indicator and maintenance code.",
                    seg,
                    current["ordinal"],
                )
            if current["maintenance"] not in MAINTENANCE:
                issue(
                    "UNSUPPORTED_ACTION",
                    "error",
                    "Maintenance code is outside the supported subset.",
                    seg,
                    current["ordinal"],
                )
        elif tag in {"REF", "NM1", "HD", "DTP"} and current:
            current["segments"].append(seg["index"])
            if tag == "REF" and e and e[0] == "0F":
                if current["member_id"]:
                    issue(
                        "DUPLICATE_IDENTIFIER",
                        "error",
                        "More than one REF*0F identifier for the member.",
                        seg,
                        current["ordinal"],
                    )
                current["member_id"] = e[1].strip() if len(e) > 1 else ""
            elif tag == "NM1" and e and e[0] == "IL":
                current["name"] = " ".join(e[2:4]).strip()
            elif tag == "HD":
                coverage = {
                    "action": e[0] if e else "",
                    "plan": e[3] if len(e) > 3 else "",
                    "begin": None,
                    "end": None,
                    "segment": seg["index"],
                }
                current["coverage"].append(coverage)
                if coverage["action"] not in MAINTENANCE or not coverage["plan"]:
                    issue(
                        "COVERAGE_HEADER",
                        "error",
                        "Coverage needs a supported action and a plan identifier.",
                        seg,
                        current["ordinal"],
                    )
            elif tag == "DTP" and e and e[0] in {"348", "349"}:
                if not current["coverage"]:
                    issue(
                        "ORPHAN_DATE",
                        "error",
                        "Coverage dates must follow an HD segment.",
                        seg,
                        current["ordinal"],
                    )
                    continue
                key = "begin" if e[0] == "348" else "end"
                try:
                    if len(e) != 3 or e[1] != "D8" or not re.fullmatch(r"\d{8}", e[2]):
                        raise ValueError()
                    datetime.strptime(e[2], "%Y%m%d")
                    if current["coverage"][-1][key] is not None:
                        issue(
                            "DUPLICATE_DATE",
                            "error",
                            "Coverage has a duplicate date qualifier.",
                            seg,
                            current["ordinal"],
                        )
                    current["coverage"][-1][key] = e[2]
                except ValueError:
                    issue(
                        "INVALID_DATE",
                        "error",
                        "Coverage date must be a valid D8 calendar date.",
                        seg,
                        current["ordinal"],
                    )
        elif tag in {"HD", "DTP", "NM1"} and not current:
            issue(
                "ORPHAN_SEGMENT",
                "error",
                "Member data appeared before an INS segment.",
                seg,
            )
        elif tag not in {"ST", "SE", "BGN", "REF", "N1", "INS", "NM1", "HD", "DTP"}:
            issue(
                "UNSUPPORTED_SEGMENT",
                "warning",
                "This segment is retained as evidence but not interpreted.",
                seg,
            )

    if not members:
        issue("NO_MEMBERS", "error", "The transaction contains no INS member records.")
    seen = set()
    for member in members:
        identity, ordinal, source = (
            member["member_id"],
            member["ordinal"],
            member["source"],
        )
        if not identity:
            issue(
                "MISSING_MEMBER_ID",
                "error",
                "Member needs a REF*0F identifier.",
                source,
                ordinal,
            )
        elif identity in seen:
            issue(
                "DUPLICATE_MEMBER",
                "error",
                "Member identifier appears more than once in this demo transaction.",
                source,
                ordinal,
            )
        seen.add(identity)
        if not member["coverage"]:
            issue(
                "MISSING_COVERAGE",
                "error",
                "Demo enrollment record needs at least one HD coverage.",
                source,
                ordinal,
            )
        for c in member["coverage"]:
            seg = segments[c["segment"] - 1]
            if c["action"] in {"021", "025"} and not c["begin"]:
                issue(
                    "MISSING_BEGIN",
                    "error",
                    "Adding or reinstating coverage needs a begin date in this demo.",
                    seg,
                    ordinal,
                )
            if c["action"] == "024" and not c["end"]:
                issue(
                    "MISSING_END",
                    "error",
                    "Terminating coverage needs an end date in this demo.",
                    seg,
                    ordinal,
                )
            if c["begin"] and c["end"] and c["begin"] > c["end"]:
                issue(
                    "DATE_ORDER",
                    "error",
                    "Coverage end precedes coverage begin.",
                    seg,
                    ordinal,
                )
        del member["source"]
        member["intent"] = MAINTENANCE.get(member["maintenance"], "review")
    errors = sum(f["severity"] == "error" for f in findings)
    return {
        "id": hashlib.sha256((RULESET + text).encode()).hexdigest()[:24],
        "ruleset": RULESET,
        "status": (
            "issues_found"
            if errors
            else "review_required" if findings else "checks_passed"
        ),
        "errors": errors,
        "warnings": len(findings) - errors,
        "members": members,
        "findings": findings,
        "segments": segments,
        "scope": "Synthetic ST-to-SE 834 subset; not a complete X12 or HIPAA validator.",
    }
