import csv
import io
import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from flask import Flask, jsonify, request, render_template, Response
from edi import analyze, classify_request


def create_app(database=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1_100_000
    dbpath = database or os.getenv("DATABASE_PATH", "data/workbench.sqlite3")
    Path(dbpath).parent.mkdir(parents=True, exist_ok=True)

    def db():
        con = sqlite3.connect(dbpath)
        con.row_factory = sqlite3.Row
        return con

    def body_object():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError("JSON object required")
        return body

    with db() as con:
        con.executescript(
            "CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY, body TEXT NOT NULL); CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, analysis_id TEXT, finding_id TEXT, actor TEXT, decision TEXT, note TEXT, created TEXT);"
        )

    def result(identity):
        with db() as con:
            row = con.execute(
                "SELECT body FROM analyses WHERE id=?", (identity,)
            ).fetchone()
            if not row:
                return None
            data = json.loads(row["body"])
            data["reviews"] = [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM reviews WHERE analysis_id=? ORDER BY id", (identity,)
                )
            ]
            return data

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "healthcare-edi-workbench"}

    @app.get("/api/samples/<name>")
    def sample(name):
        if name not in {"valid", "invalid", "termination"}:
            return {"error": "Unknown sample"}, 404
        return {
            "edi": (Path(__file__).parent / "samples" / (name + ".edi")).read_text()
        }

    @app.post("/api/analyze")
    def analyze_api():
        body = body_object()
        try:
            report = analyze(body.get("edi", ""))
        except ValueError as exc:
            return {"error": str(exc)}, 400
        with db() as con:
            con.execute(
                "INSERT OR IGNORE INTO analyses VALUES (?,?)",
                (report["id"], json.dumps(report)),
            )
        report = result(report["id"])
        if body.get("request_text"):
            report["request_intent"] = classify_request(str(body["request_text"]))
        return jsonify(report)

    @app.get("/api/analyses/<identity>")
    def get_report(identity):
        data = result(identity)
        return jsonify(data) if data else ({"error": "Analysis not found"}, 404)

    @app.post("/api/analyses/<identity>/reviews")
    def review(identity):
        report = result(identity)
        if not report:
            return {"error": "Analysis not found"}, 404
        body = body_object()
        if body.get("finding_id") not in {f["id"] for f in report["findings"]}:
            return {"error": "Finding does not belong to this analysis"}, 400
        if body.get("decision") not in {
            "acknowledged",
            "needs_follow_up",
            "false_positive",
        }:
            return {"error": "Unsupported decision"}, 400
        if not all(
            isinstance(body.get(k), str) and 0 < len(body[k].strip()) <= 500
            for k in ("actor", "note")
        ):
            return {
                "error": "Reviewer and rationale are required (max 500 characters)"
            }, 400
        with db() as con:
            con.execute(
                "INSERT INTO reviews(analysis_id,finding_id,actor,decision,note,created) VALUES (?,?,?,?,?,?)",
                (
                    identity,
                    body["finding_id"],
                    body["actor"].strip(),
                    body["decision"],
                    body["note"].strip(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return jsonify(result(identity)), 201

    @app.get("/api/analyses/<identity>/export.csv")
    def export(identity):
        report = result(identity)
        if not report:
            return {"error": "Analysis not found"}, 404
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(
            ["code", "severity", "segment", "member", "message", "evidence"]
        )

        def safe(value):
            text = str(value if value is not None else "")
            return (
                "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
            )

        for f in report["findings"]:
            writer.writerow(
                [
                    safe(f[k])
                    for k in [
                        "code",
                        "severity",
                        "segment",
                        "member",
                        "message",
                        "evidence",
                    ]
                ]
            )
        return Response(
            out.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=findings.csv"},
        )

    @app.errorhandler(413)
    def large(_):
        return {"error": "File too large; limit 1 MB"}, 413

    @app.errorhandler(ValueError)
    def invalid(exc):
        return {"error": str(exc)}, 400

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.getenv("PORT", "8101")))
