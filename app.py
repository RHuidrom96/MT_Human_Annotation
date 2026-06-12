# app.py
"""MT Evaluation Platform -- Flask app.

Admin: env vars MT_EVAL_ADMIN_EMAIL / MT_EVAL_ADMIN_PASSWORD.
Annotators: registered via /campaign/<id> after admin shares the link.
Storage: PostgreSQL for metadata and AWS S3 for annotation exports.

Run:
    PORT=8000 python app.py

Production:
    gunicorn -w 4 -b 0.0.0.0:8000 app:app
"""

import json
import logging
import os
import re

from dotenv import load_dotenv

load_dotenv()

from datetime import datetime
from pathlib import Path

from flask import (Flask, render_template, request, jsonify, redirect, url_for,
                   session, flash, abort)

import drive_sync
from auth import (admin_login, current_admin, current_annotator,
                  require_admin, require_annotator, ADMIN_EMAIL)
from models import db, Annotator, Campaign, Rating

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

APP_ROOT = Path(__file__).parent


# -- Evaluation rubric (shared with the rating template + JS) -----------------
CRITERIA_DEFAULTS = [
    {"id": "adequacy", "name": "Adequacy",
     "color": "#d4537e",
     "desc": "Source information preserved in translation",
     "guide": "Measures how much information from the source sentence is correctly preserved in the translation. A high adequacy score means the translated sentence conveys the same content and intent as the source. Penalize omissions, additions, and mistranslations."},
    {"id": "fluency", "name": "Fluency",
     "color": "#378add",
     "desc": "Natural and grammatical target text",
     "guide": "Measures how natural, grammatically correct, and readable the translated sentence is in the target language. Judge this independently of the source -- ignore meaning for a moment and ask whether a native speaker would find the sentence well-formed."},
    {"id": "meaning_preservation", "name": "Meaning preservation",
     "color": "#ba7517",
     "desc": "Overall meaning intact, no hallucination",
     "guide": "Measures whether the overall meaning and context of the source sentence are maintained without distortion or misunderstanding in the translation, and whether any hallucinated content (information not present in the source) has been introduced. Even a fluent and superficially adequate translation should score low here if it subtly changes the meaning or invents details."},
]

# Pool of colors auto-assigned to admin-defined criteria
CRITERION_COLOR_POOL = ["#d4537e", "#378add", "#ba7517", "#1d9e75", "#6b51b8", "#c84a4a"]


def _slugify(s, fallback):
    """Lowercase, ascii, underscores. For deriving criterion IDs from names."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s or fallback


def get_criteria_for(campaign):
    """Return the effective criteria list for a campaign."""
    return campaign.criteria or CRITERIA_DEFAULTS

SCALE_LABELS = {1: "Very poor", 2: "Poor", 3: "Acceptable", 4: "Good", 5: "Excellent"}

SCRIPT_OPTIONS = [
    {"id": "bengali", "name": "Manipuri (Bengali script)", "short": "Bengali script"},
    {"id": "meetei",  "name": "Manipuri (Meitei Mayek script)", "short": "Meitei Mayek script"},
]

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
# DRIVE_ID_RE = re.compile(r"[A-Za-z0-9_-]{20,}")



# -- App setup ----------------------------------------------------------------
app = Flask(__name__)
def _load_or_create_secret_key():
    """Use the env var if set; otherwise persist a generated key to data/secret_key
    so sessions survive restarts and all workers share the same key.

    Creation is atomic (O_CREAT|O_EXCL) so that when several gunicorn workers
    boot at once on a fresh install they converge on a single key instead of
    each generating its own -- otherwise cookies signed by one worker are
    rejected by another, which logs admins/annotators out at random."""
    env_key = os.environ.get("MT_EVAL_SECRET_KEY")
    if env_key:
        return env_key
    key_file = DATA_DIR / "secret_key"
    import secrets
    new_key = secrets.token_hex(32)
    try:
        fd = os.open(str(key_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, new_key.encode())
        finally:
            os.close(fd)
        logger.warning("MT_EVAL_SECRET_KEY not set; generated and saved one to %s. "
                       "For production, set MT_EVAL_SECRET_KEY explicitly.", key_file)
        return new_key
    except FileExistsError:
        # Created by a previous run or a sibling worker -- reuse it.
        return key_file.read_text().strip()
    except Exception:
        logger.exception("Could not persist generated secret key; using in-memory key")
        return new_key

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = _load_or_create_secret_key()

db.init_app(app)

with app.app_context():
    db.create_all()

@app.context_processor
def inject_globals():
    return {"current_annotator": current_annotator()}


# ============================================================================
# Public landing
# ============================================================================

@app.route("/")
def landing():
    return render_template("landing.html",
                           is_admin=bool(current_admin()),
                           annotator=current_annotator())


# ============================================================================
# Admin auth
# ============================================================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login_view():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        if admin_login(email, password):
            session.clear()
            session.permanent = True
            session["is_admin"] = True
            session["admin_email"] = email
            return redirect(request.args.get("next") or url_for("admin_dashboard"))
        flash("Invalid admin credentials.", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("is_admin", None)
    session.pop("admin_email", None)
    flash("Signed out.", "info")
    return redirect(url_for("landing"))


# ============================================================================
# Admin dashboard + campaign CRUD
# ============================================================================

@app.route("/admin/dashboard")
@require_admin
def admin_dashboard():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    # Annotator counts per campaign
    stats = {}
    for c in campaigns:
        crit_ids = [cr["id"] for cr in get_criteria_for(c)]
        annotator_ids = {r.annotator_id for r in c.ratings}
        stats[c.id] = {
            "annotator_count": len(annotator_ids),
            "total_ratings": sum(1 for r in c.ratings if r.is_complete_for(crit_ids)),
            "max_possible": c.num_segments * max(1, len(annotator_ids)),
        }
    return render_template("admin_dashboard.html",
                           campaigns=campaigns, stats=stats,
                           sa_email=drive_sync.service_account_email())


@app.route("/admin/campaign/new", methods=["GET", "POST"])
@require_admin
def admin_campaign_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        source_language = request.form.get("source_language", "").strip()
        target_language = request.form.get("target_language", "").strip()
        script = request.form.get("script", "").strip() or None
        instructions = request.form.get("instructions", "").strip()
        span_scope = request.form.get("span_scope", "").strip()
        span_instructions = request.form.get("span_instructions", "").strip()
        enable_spans = request.form.get("enable_spans") == "on"
        if enable_spans:
            if span_scope not in ("target", "both"):
                span_scope = ""  # will fail validation below
        else:
            span_scope = "target"  # irrelevant but keep a safe default

        # Criteria: parallel arrays crit_name[] / crit_desc[]
        crit_names = request.form.getlist("crit_name")
        crit_descs = request.form.getlist("crit_desc")
        criteria = []
        criteria_missing_desc = []   # names of criteria saved with blank definitions
        used_ids = set()
        for i, nm in enumerate(crit_names):
            nm = (nm or "").strip()
            if not nm:
                continue
            desc = (crit_descs[i] if i < len(crit_descs) else "").strip()
            if not desc:
                criteria_missing_desc.append(nm)
            base_id = _slugify(nm, f"criterion_{i+1}")
            cid = base_id
            n = 2
            while cid in used_ids:
                cid = f"{base_id}_{n}"; n += 1
            used_ids.add(cid)
            color = CRITERION_COLOR_POOL[len(criteria) % len(CRITERION_COLOR_POOL)]
            criteria.append({"id": cid, "name": nm, "color": color,
                             "desc": "", "guide": desc})

        # Input JSON: either file upload or pasted text
        segments_raw = ""
        upload = request.files.get("segments_file")
        if upload and upload.filename:
            try:
                segments_raw = upload.read().decode("utf-8")
            except UnicodeDecodeError:
                flash("Could not read the uploaded file as UTF-8.", "error")
                return _render_new_campaign_form()
        else:
            segments_raw = request.form.get("segments_paste", "").strip()

        # Validate
        errors = []
        if not name:
            errors.append("Campaign name is required.")
        if not source_language:
            errors.append("Source language is required.")
        if not target_language:
            errors.append("Target language is required.")
        if script and script not in {s["id"] for s in SCRIPT_OPTIONS}:
            errors.append("Invalid script option.")
        if len(criteria) == 0:
            errors.append("Please define at least one evaluation criterion.")
        if criteria_missing_desc:
            names = ", ".join(criteria_missing_desc)
            errors.append(
                "Each criterion needs a definition shown to annotators. "
                f"Missing definition for: {names}."
            )
        if enable_spans and span_scope not in ("target", "both"):
            errors.append("Please select whether span annotation applies to the target only or to both source and target.")
        if not segments_raw:
            errors.append("Please upload or paste the segments JSON file.")
        if not instructions:
            errors.append("Annotation instructions are required.")
        if enable_spans and span_scope in ("target", "both") and not span_instructions:
            errors.append("Span annotation instructions are required when span annotation is enabled.")
        if segments_raw:
            try:
                segments = json.loads(segments_raw)
                if not isinstance(segments, list) or len(segments) == 0:
                    errors.append("Segments JSON must be a non-empty array.")
                else:
                    seen_ids = set()
                    for i, s in enumerate(segments):
                        if not isinstance(s, dict):
                            errors.append(f"Segment {i+1}: must be an object.")
                            continue
                        if "id" not in s or "source" not in s or "target" not in s:
                            errors.append(f"Segment {i+1}: must have at least 'id', 'source', 'target'.")
                            continue
                        if s["id"] in seen_ids:
                            errors.append(f"Duplicate segment id: {s['id']}")
                        seen_ids.add(s["id"])
            except json.JSONDecodeError as e:
                errors.append(f"Segments JSON is not valid: {e}")

        if errors:
            for e in errors:
                flash(e, "error")
            return _render_new_campaign_form(
                form_data={"name": name, "source_language": source_language,
                           "target_language": target_language, "script": script or "",
                           "instructions": instructions,
                           "enable_spans": enable_spans,
                           "span_scope": span_scope,
                           "span_instructions": span_instructions,
                           "criteria": criteria,
                           "segments_paste": segments_raw})

        c = Campaign(
            name=name,
            source_language=source_language,
            target_language=target_language,
            script=script,
            segments_json=json.dumps(segments, ensure_ascii=False),
            criteria_json=json.dumps(criteria, ensure_ascii=False),
            instructions=instructions,
            enable_spans=enable_spans,
            span_scope=span_scope,
            span_instructions=span_instructions if enable_spans else "",
        )
        db.session.add(c)
        db.session.commit()
        flash(f"Campaign '{name}' created.", "success")
        return redirect(url_for("admin_campaign_detail", campaign_id=c.id))

    return _render_new_campaign_form()


def _render_new_campaign_form(form_data=None):
    fd = form_data or {}
    # Provide default criteria for a fresh form
    if "criteria" not in fd:
        fd["criteria"] = [{"name": c["name"], "desc": c["guide"]} for c in CRITERIA_DEFAULTS]
    return render_template("admin_campaign_new.html",
                           scripts=SCRIPT_OPTIONS,
                           sa_email=drive_sync.service_account_email(),
                           form_data=fd)


@app.route("/admin/campaign/<campaign_id>")
@require_admin
def admin_campaign_detail(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    rows = _campaign_progress_rows(c)

    share_url = url_for("annotator_login_view", campaign_id=c.id, _external=True)
    return render_template("admin_campaign_detail.html",
                           campaign=c, rows=rows, share_url=share_url,
                           criteria=get_criteria_for(c),
                           sa_email=drive_sync.service_account_email())


def _campaign_progress_rows(c):
    """Per-annotator completion rows for a campaign, sorted by progress.

    Shared by the detail page and the live-polling JSON endpoint so both
    always show identical numbers.
    """
    crit_ids = [cr["id"] for cr in get_criteria_for(c)]
    all_ratings = list(c.ratings)
    rows = []
    annotator_ids = sorted({r.annotator_id for r in all_ratings})
    for aid in annotator_ids:
        ann = Annotator.query.get(aid)
        if not ann:
            continue
        rs = [r for r in all_ratings if r.annotator_id == aid]
        completed = sum(1 for r in rs if r.is_complete_for(crit_ids))
        last_update = max((r.updated_at for r in rs), default=None)
        rows.append({
            "annotator": ann,
            "completed": completed,
            "total": c.num_segments,
            "last_update": last_update,
        })
    rows.sort(key=lambda r: (-r["completed"], r["annotator"].email))
    return rows


@app.route("/admin/campaign/<campaign_id>/progress.json")
def admin_campaign_progress_json(campaign_id):
    """Live progress snapshot polled by the admin detail page (no refresh).

    Deliberately does NOT use @require_admin: that decorator redirects to the
    login page (302 -> 200 HTML), which a fetch() follows silently, leaving the
    table frozen with no signal. Instead we return a clean 401 JSON so the
    client can detect the lost session and reload to re-authenticate.
    """
    if not session.get("is_admin"):
        return jsonify({"ok": False, "error": "auth"}), 401
    c = Campaign.query.get_or_404(campaign_id)
    rows = _campaign_progress_rows(c)
    resp = jsonify({
        "ok": True,
        "is_closed": c.is_closed,
        "num_segments": c.num_segments,
        "rows": [
            {
                "name": r["annotator"].name,
                "email": r["annotator"].email,
                "completed": r["completed"],
                "total": r["total"],
                "pct": int(r["completed"] / r["total"] * 100) if r["total"] else 0,
                "last_update": (r["last_update"].strftime("%Y-%m-%d %H:%M UTC")
                                if r["last_update"] else None),
            }
            for r in rows
        ],
    })
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@app.route("/admin/campaign/<campaign_id>/close", methods=["POST"])
@require_admin
def admin_campaign_close(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        flash("Campaign is already closed.", "info")
    else:
        c.closed_at = datetime.utcnow()
        db.session.commit()
        # Final flush of master CSV (each known annotator already has their snapshot)
        try:
            crit = get_criteria_for(c)
            crit_ids = [cr["id"] for cr in crit]
            all_ratings = Rating.query.filter_by(campaign_id=c.id).all()
            completed = [r for r in all_ratings if r.is_complete_for(crit_ids)]
            drive_sync.sync_campaign_master_csv(c, completed, crit)
        except Exception:
            logger.exception("Final Drive flush failed for %s", c.id)
        flash("Campaign closed. Annotators can no longer edit.", "success")
    return redirect(url_for("admin_campaign_detail", campaign_id=c.id))


@app.route("/admin/campaign/<campaign_id>/delete", methods=["POST"])
@require_admin
def admin_campaign_delete(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    # Require the admin to type the campaign name to confirm
    confirm = request.form.get("confirm_name", "").strip()
    if confirm != c.name:
        flash("Deletion cancelled: the name you typed didn't match.", "error")
        return redirect(url_for("admin_campaign_detail", campaign_id=c.id))
    name = c.name
    # Best-effort: remove the campaign's files from Drive too
    try:
        drive_sync.delete_campaign_files(c)
    except Exception:
        logger.exception("Drive delete during campaign delete failed for %s", c.id)
    # Cascade deletes ratings (relationship cascade) -- delete the campaign row
    db.session.delete(c)
    db.session.commit()
    flash(f"Campaign '{name}' and all its ratings were deleted.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/campaign/<campaign_id>/download_csv")
@require_admin
def admin_campaign_download_csv(campaign_id):
    """Download the master CSV directly from the server (alongside Drive)."""
    from flask import Response
    c = Campaign.query.get_or_404(campaign_id)
    crit = get_criteria_for(c)
    crit_ids = [cr["id"] for cr in crit]
    all_ratings = Rating.query.filter_by(campaign_id=c.id).all()
    completed = [r for r in all_ratings if r.is_complete_for(crit_ids)]
    data = drive_sync._build_master_csv(c, completed, crit)
    filename = drive_sync._safe_filename(c.name) + "_master.csv"
    return Response(
        data, mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================================================
# Annotator auth (per-campaign landing, global account)
# ============================================================================

@app.route("/annotator/update-password", methods=["GET", "POST"])
@require_annotator
def annotator_update_password():
    ann = current_annotator()
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")
        errors = []
        if not ann.check_password(current_pw):
            errors.append("Current password is incorrect.")
        if len(new_pw) < 6:
            errors.append("New password must be at least 6 characters.")
        if new_pw != confirm_pw:
            errors.append("New passwords don't match.")
        if errors:
            for e in errors:
                flash(e, "error")
            return redirect(url_for("annotator_update_password"))
        ann.set_password(new_pw)
        db.session.commit()
        flash("Password updated successfully.", "success")
        return redirect(url_for("annotator_dashboard"))
    return render_template("annotator_update_password.html", annotator=ann)


@app.route("/campaign/<campaign_id>")
def annotator_login_view(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)
    if current_annotator():
        return redirect(url_for("annotator_dashboard"))
    return render_template("annotator_login.html",
                           campaign=c, scripts=SCRIPT_OPTIONS,
                           criteria=get_criteria_for(c))


@app.route("/campaign/<campaign_id>/login", methods=["POST"])
def annotator_login_post(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if not email or not password:
        flash("Email and password are required.", "error")
        return redirect(url_for("annotator_login_view", campaign_id=campaign_id))
    ann = Annotator.query.filter_by(email=email).first()
    if not ann or not ann.check_password(password):
        flash("Incorrect email or password.", "error")
        return redirect(url_for("annotator_login_view", campaign_id=campaign_id))
    session.clear()
    session["annotator_id"] = ann.id
    session["last_campaign_id"] = campaign_id
    return redirect(url_for("annotator_dashboard"))


@app.route("/campaign/<campaign_id>/register", methods=["POST"])
def annotator_register(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    native_lang = request.form.get("native_language", "").strip()
    confirm = request.form.get("password_confirm", "")

    errors = []
    if not name: errors.append("Name is required.")
    if not email or not EMAIL_RE.match(email): errors.append("A valid email is required.")
    if len(password) < 6: errors.append("Password must be at least 6 characters.")
    if password != confirm: errors.append("Passwords don't match.")
    if not request.form.get("consent"): errors.append("Please confirm the consent checkbox.")

    if not errors and Annotator.query.filter_by(email=email).first():
        errors.append("An account with that email already exists. Sign in instead.")

    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("annotator_login_view", campaign_id=campaign_id))

    ann = Annotator(name=name, email=email, native_language=native_lang)
    ann.set_password(password)
    db.session.add(ann)
    db.session.commit()
    session.clear()
    session["annotator_id"] = ann.id
    session["last_campaign_id"] = campaign_id
    flash("Account created. Welcome!", "success")
    return redirect(url_for("rate_view", campaign_id=campaign_id))


@app.route("/annotator/logout", methods=["POST"])
def annotator_logout():
    last_cid = session.get("last_campaign_id")
    session.pop("annotator_id", None)
    flash("Signed out.", "info")
    # Bring them back to the login page for the campaign they were working on.
    if last_cid and Campaign.query.get(last_cid):
        return redirect(url_for("annotator_login_view", campaign_id=last_cid))
    return redirect(url_for("landing"))


@app.route("/annotator/dashboard")
@require_annotator
def annotator_dashboard():
    ann = current_annotator()
    # Find all campaigns this annotator has ratings for
    campaign_ids = {r.campaign_id for r in
                    Rating.query.filter_by(annotator_id=ann.id).all()}
    # Also include the campaign they most recently signed in from, even with no
    # ratings yet, so they have a way into it from the dashboard.
    last_cid = session.get("last_campaign_id")
    if last_cid:
        campaign_ids.add(last_cid)
    rows = []
    for cid in campaign_ids:
        c = Campaign.query.get(cid)
        if not c:
            continue
        crit_ids = [cr["id"] for cr in get_criteria_for(c)]
        ratings = Rating.query.filter_by(campaign_id=cid, annotator_id=ann.id).all()
        completed = sum(1 for r in ratings if r.is_complete_for(crit_ids))
        last_update = max((r.updated_at for r in ratings), default=None)
        rows.append({
            "campaign": c,
            "completed": completed,
            "total": c.num_segments,
            "last_update": last_update,
            "pct": int(completed / c.num_segments * 100) if c.num_segments else 0,
        })
    rows.sort(key=lambda r: (r["campaign"].is_closed, -r["pct"], r["campaign"].name))
    return render_template("annotator_dashboard.html", annotator=ann, rows=rows)


# ============================================================================
# Rating UI
# ============================================================================

@app.route("/campaign/<campaign_id>/rate")
@require_annotator
def rate_view(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        return render_template("campaign_closed.html", campaign=c)
    session["last_campaign_id"] = campaign_id
    ann = current_annotator()
    criteria = get_criteria_for(c)
    crit_ids = [cr["id"] for cr in criteria]
    # Load existing ratings for this (campaign, annotator)
    existing = {r.segment_id: r for r in
                Rating.query.filter_by(campaign_id=c.id, annotator_id=ann.id).all()}
    existing_payload = {
        seg_id: {"scores": r.scores_dict(), "spans": r.spans_dict(),
                 "comments": r.comments or ""}
        for seg_id, r in existing.items()
    }
    completed_count = sum(1 for r in existing.values() if r.is_complete_for(crit_ids))
    return render_template(
        "rate.html",
        campaign=c, annotator=ann,
        segments=c.segments, criteria=criteria, scale_labels=SCALE_LABELS,
        existing=existing_payload, completed_count=completed_count,
        span_scope=c.span_scope or "target",
        enable_spans=c.enable_spans if c.enable_spans is not None else True,
        instructions=c.instructions or "",
        span_instructions=c.span_instructions or "",
    )


@app.route("/campaign/<campaign_id>/api/submit", methods=["POST"])
@require_annotator
def api_submit(campaign_id):
    c = Campaign.query.get_or_404(campaign_id)
    if c.is_closed:
        return jsonify({"ok": False, "error": "Campaign is closed."}), 403
    ann = current_annotator()
    criteria = get_criteria_for(c)
    data = request.get_json(silent=True) or {}

    segment_id = (data.get("segment_id") or "").strip()
    scores = data.get("scores") or {}
    spans = data.get("spans") or {}
    comments = (data.get("comments") or "").strip()
    time_spent = data.get("time_spent_seconds", 0)

    seg = c.segment_by_id(segment_id)
    if not seg:
        return jsonify({"ok": False, "error": "Unknown segment_id."}), 400

    # Validate Likert scores against this campaign's criteria
    clean_scores = {}
    for crit in criteria:
        v = scores.get(crit["id"])
        if v is None or not isinstance(v, int) or v < 1 or v > 5:
            return jsonify({"ok": False, "error": f"Missing or invalid rating for {crit['name']}."}), 400
        clean_scores[crit["id"]] = v

    # Validate spans. Spans may be on target text and (if span_scope=="both") source text.
    # We store offsets as-is; we just bounds-check against the relevant text length.
    # A span item is [start, end] (target) OR [start, end, "source"/"target"] when both are allowed.
    target_len = len(seg.get("target", ""))
    source_len = len(seg.get("source", ""))
    span_scope = c.span_scope or "target"
    cleaned = {}
    for crit in criteria:
        raw = spans.get(crit["id"], [])
        clean = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, list) or len(item) < 2:
                    continue
                s, e = item[0], item[1]
                if not (isinstance(s, int) and isinstance(e, int)):
                    continue
                which = item[2] if (len(item) >= 3 and span_scope == "both") else "target"
                if which == "source" and span_scope == "both":
                    if 0 <= s < e <= source_len:
                        clean.append([s, e, "source"])
                else:
                    if 0 <= s < e <= target_len:
                        if span_scope == "both":
                            clean.append([s, e, "target"])
                        else:
                            clean.append([s, e])
        cleaned[crit["id"]] = clean

    # Upsert
    rating = Rating.query.filter_by(
        campaign_id=c.id, annotator_id=ann.id, segment_id=segment_id
    ).first()
    if not rating:
        rating = Rating(campaign_id=c.id, annotator_id=ann.id, segment_id=segment_id)
        db.session.add(rating)
    rating.scores_json = json.dumps(clean_scores)
    rating.spans_json = json.dumps(cleaned, ensure_ascii=False)
    rating.comments = comments
    if isinstance(time_spent, (int, float)) and time_spent > 0:
        rating.time_spent_seconds = (rating.time_spent_seconds or 0) + int(time_spent)
    rating.updated_at = datetime.utcnow()
    db.session.commit()

    # Mirror to Drive (background)
    drive_sync.sync_all_in_background(app, c.id, ann.id, criteria)

    return jsonify({"ok": True})


# ============================================================================
# Healthcheck
# ============================================================================

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "admin_configured": ADMIN_EMAIL != "admin@example.com"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
