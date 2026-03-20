#!/usr/bin/env python3
"""
app.py — Client-facing onboarding request portal.

Clients fill in App ID, App Name, Region, and Entitlement Groups.
Each submission is stored as a document in a dedicated ES datastream.

Run with:
    python app.py

Environment variables:
    LOG_LEVEL   DEBUG / INFO / WARNING / ERROR  (default: INFO)
    LOG_FILE    Path to log file  (default: none)
"""
import json
import logging
import os
import re
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import requests as http_client
import urllib3
from flask import Flask, g, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

SCRIPT_DIR  = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "config.json"


# ── Logging ────────────────────────────────────────────────────────────────────

def setup_logging(app: Flask) -> logging.Logger:
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        log_level = "INFO"

    formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  [portal]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("portal")
    logger.setLevel(getattr(logging, log_level))
    logger.handlers.clear()
    logger.propagate = False

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    log_file = os.environ.get("LOG_FILE", "").strip()
    if log_file:
        fh = TimedRotatingFileHandler(log_file, when="midnight", backupCount=30, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    return logger


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB
log = setup_logging(app)


# ── Request lifecycle ──────────────────────────────────────────────────────────

@app.before_request
def _before():
    g.start_time = time.monotonic()
    log.info("→ %s %s  [%s]", request.method, request.path, request.remote_addr or "-")


@app.after_request
def _after(response):
    elapsed_ms = (time.monotonic() - g.start_time) * 1000
    level = logging.WARNING if response.status_code >= 400 else logging.INFO
    log.log(level, "← %s %s  %d  %.0fms",
            request.method, request.path, response.status_code, elapsed_ms)
    return response


@app.errorhandler(Exception)
def _handle_exception(exc):
    if isinstance(exc, HTTPException):
        return exc
    log.error("Unhandled exception:\n%s", traceback.format_exc())
    return jsonify({"errors": [f"Server error: {exc}"]}), 500


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def es_index(doc: dict, config: dict) -> str:
    """Write a document to the configured ES datastream. Returns the ES _id."""
    ds       = config.get("datastream", {})
    base_url = ds.get("elk_url", "").rstrip("/")
    index    = ds.get("index", "cribl-onboarding-requests")
    skip_ssl = ds.get("skip_ssl", False)

    if not base_url:
        raise ValueError("datastream.elk_url is not configured in config.json")

    if skip_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = http_client.Session()
    session.verify = not skip_ssl

    headers = {"Content-Type": "application/json"}
    token    = ds.get("token",    "").strip()
    username = ds.get("username", "").strip()
    password = ds.get("password", "").strip()
    if token:
        headers["Authorization"] = f"ApiKey {token}"
    elif username:
        session.auth = (username, password)

    resp = session.post(
        f"{base_url}/{index}/_doc",
        json=doc,
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("_id", "unknown")


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
@app.route("/portal")
@app.route("/portal/")
def index():
    return render_template("request.html")


@app.route("/portal/api/submit", methods=["POST"])
@app.route("/api/submit", methods=["POST"])
def submit():
    data     = request.get_json(silent=True) or {}
    app_id   = (data.get("app_id")   or "").strip()
    app_name = (data.get("app_name") or "").strip()
    region   = (data.get("region")   or "").strip()
    groups   = [grp for grp in (data.get("groups") or []) if grp]

    errors = []
    if not app_id:                        errors.append("App ID is required.")
    if not app_name:                      errors.append("App Name is required.")
    elif not re.match(r"^\w+$", app_name):
                                          errors.append("App Name must be a single word using only letters, numbers, and underscores.")
    if region not in ("azn", "azs"):      errors.append("Region must be azn or azs.")
    if not groups:                        errors.append("Select at least one entitlement group.")
    if errors:
        return jsonify({"errors": errors}), 400

    try:
        config = load_config()
    except Exception as exc:
        return jsonify({"errors": [f"Could not load config.json: {exc}"]}), 500

    now        = datetime.now(timezone.utc)
    request_id = f"REQ-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

    doc = {
        "@timestamp":         now.isoformat(),
        "request_id":         request_id,
        "app_id":             app_id,
        "app_name":           app_name,
        "region":             region,
        "entitlement_groups": groups,
        "status":             "pending",
    }

    try:
        es_id = es_index(doc, config)
        log.info("submitted  request_id=%s  app_id=%s  region=%s  groups=%s  es_id=%s",
                 request_id, app_id, region, groups, es_id)
    except Exception as exc:
        log.error("ES index failed: %s", exc)
        return jsonify({"errors": [f"Failed to store request: {exc}"]}), 500

    return jsonify({"request_id": request_id})


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    log.info("Starting portal on 0.0.0.0:9229")
    app.run(host="0.0.0.0", port=9229, debug=False)
