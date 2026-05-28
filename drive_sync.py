"""Google Drive upload helper using a service-account JSON key.

The admin places the JSON at the path given by MT_EVAL_GDRIVE_SA_PATH (default
data/gdrive_sa.json) and shares the destination folder with the service account's
email (visible inside the JSON as `client_email`).

This module is intentionally fault-tolerant: if Drive is unreachable or
credentials are missing, sync calls log an error but never raise to the
caller. The local SQLite DB is always the source of truth; Drive is a mirror.
"""

import csv
import io
import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

SA_PATH = Path(os.environ.get("MT_EVAL_GDRIVE_SA_PATH", "data/gdrive_sa.json"))
SCOPES = ["https://www.googleapis.com/auth/drive"]

_service = None
_service_lock = threading.Lock()
_file_cache = {}  # (folder_id, filename) -> drive file id


def _get_service():
    """Lazily build the Drive service. Returns None if credentials missing."""
    global _service
    if _service is not None:
        return _service
    with _service_lock:
        if _service is not None:
            return _service
        if not SA_PATH.exists():
            logger.warning("Service account JSON not found at %s; Drive sync disabled.", SA_PATH)
            return None
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds = service_account.Credentials.from_service_account_file(str(SA_PATH), scopes=SCOPES)
            _service = build("drive", "v3", credentials=creds, cache_discovery=False)
            logger.info("Google Drive service initialized for %s", creds.service_account_email)
        except Exception:
            logger.exception("Could not initialize Drive service")
            _service = None
        return _service


def service_account_email():
    """Return the service account email for admin to share folders with, or None."""
    if not SA_PATH.exists():
        return None
    try:
        with open(SA_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("client_email")
    except Exception:
        return None


def _find_file(service, folder_id, name):
    """Return the Drive file id for `name` in `folder_id`, or None."""
    cache_key = (folder_id, name)
    if cache_key in _file_cache:
        return _file_cache[cache_key]
    safe_name = name.replace("'", "\\'")
    q = f"'{folder_id}' in parents and name = '{safe_name}' and trashed = false"
    try:
        resp = service.files().list(
            q=q, fields="files(id, name)", pageSize=10,
            supportsAllDrives=True, includeItemsFromAllDrives=True
        ).execute()
        files = resp.get("files", [])
        if files:
            _file_cache[cache_key] = files[0]["id"]
            return files[0]["id"]
    except Exception:
        logger.exception("Drive list failed for %s/%s", folder_id, name)
    return None


def _upload_bytes(folder_id, name, data, mime_type):
    """Upload (or replace) a file in the given Drive folder.
    Returns (ok, err_msg)."""
    service = _get_service()
    if not service:
        return False, "Drive service unavailable (no service-account JSON at data/gdrive_sa.json)"
    if not folder_id:
        return False, "no drive folder configured"
    try:
        from googleapiclient.http import MediaIoBaseUpload
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)
        existing_id = _find_file(service, folder_id, name)
        if existing_id:
            service.files().update(
                fileId=existing_id, media_body=media, supportsAllDrives=True
            ).execute()
        else:
            meta = {"name": name, "parents": [folder_id]}
            created = service.files().create(
                body=meta, media_body=media, fields="id",
                supportsAllDrives=True
            ).execute()
            _file_cache[(folder_id, name)] = created["id"]
        logger.info("Drive upload OK: %s/%s (%d bytes)", folder_id, name, len(data))
        return True, ""
    except Exception as e:
        msg = str(e)
        # google-api-python-client puts the useful bit in the .content attr
        if hasattr(e, "content"):
            try:
                msg = e.content.decode("utf-8", errors="replace")
            except Exception:
                pass
        logger.exception("Drive upload failed: %s/%s: %s", folder_id, name, msg)
        return False, msg[:500]


# ---- High-level helpers used by app.py ---------------------------------------

def _csv_header(criteria):
    """Build a CSV header based on the campaign's criteria list."""
    header = [
        "response_id", "timestamp_utc",
        "annotator_name", "annotator_email", "annotator_native_lang",
        "campaign_id", "campaign_name",
        "source_language", "target_language", "script",
        "segment_id", "system", "domain",
        "source", "target", "reference",
    ]
    for c in criteria:
        header.append(c["id"])
    for c in criteria:
        header.append(c["id"] + "_spans")
    header += ["comments", "time_spent_seconds", "updated_at_utc"]
    return header


def _safe_filename(name):
    """Make a string safe for use as a Drive filename."""
    keep = []
    for ch in name:
        if ch.isalnum() or ch in "-_ ":
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep).strip() or "campaign"


def _build_master_csv(campaign, ratings, criteria):
    """Build the per-campaign master CSV from a list of completed ratings."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_csv_header(criteria))
    for r in ratings:
        seg = campaign.segment_by_id(r.segment_id) or {}
        ann = r.annotator
        scores = r.scores_dict()
        spans = r.spans_dict()
        row = [
            r.id, r.updated_at.isoformat(timespec="seconds") + "Z",
            ann.name, ann.email, ann.native_language or "",
            campaign.id, campaign.name,
            campaign.source_language, campaign.target_language, campaign.script or "",
            r.segment_id, seg.get("system", ""), seg.get("domain", ""),
            seg.get("source", ""), seg.get("target", ""), seg.get("reference", ""),
        ]
        for c in criteria:
            row.append(scores.get(c["id"], ""))
        for c in criteria:
            row.append(json.dumps(spans.get(c["id"], [])))
        row += [
            r.comments or "",
            r.time_spent_seconds or 0,
            r.updated_at.isoformat(timespec="seconds") + "Z",
        ]
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def _build_annotator_snapshot(campaign, annotator, ratings, criteria):
    """Build a JSON snapshot of one annotator's progress on one campaign."""
    payload = {
        "campaign": {
            "id": campaign.id,
            "name": campaign.name,
            "source_language": campaign.source_language,
            "target_language": campaign.target_language,
            "script": campaign.script,
            "num_segments": campaign.num_segments,
            "criteria": [{"id": c["id"], "name": c["name"]} for c in criteria],
        },
        "annotator": {
            "id": annotator.id,
            "name": annotator.name,
            "email": annotator.email,
            "native_language": annotator.native_language or "",
        },
        "ratings": [
            {
                "segment_id": r.segment_id,
                "scores": r.scores_dict(),
                "spans": r.spans_dict(),
                "comments": r.comments or "",
                "time_spent_seconds": r.time_spent_seconds or 0,
                "updated_at_utc": r.updated_at.isoformat(timespec="seconds") + "Z",
            }
            for r in ratings
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def sync_campaign_master_csv(campaign, all_ratings, criteria):
    """Upload the master CSV for a campaign. Returns (ok, err_msg)."""
    if not campaign.drive_folder_id:
        return False, "no drive folder configured"
    fname = _safe_filename(campaign.name) + "_master.csv"
    data = _build_master_csv(campaign, all_ratings, criteria)
    return _upload_bytes(campaign.drive_folder_id, fname, data, "text/csv")


def sync_annotator_snapshot(campaign, annotator, annotator_ratings, criteria):
    """Upload one annotator's JSON snapshot for a campaign."""
    if not campaign.drive_folder_id:
        return False, "no drive folder configured"
    safe_email = annotator.email.replace("@", "_at_").replace(".", "_")
    fname = _safe_filename(campaign.name) + "__" + safe_email + ".json"
    data = _build_annotator_snapshot(campaign, annotator, annotator_ratings, criteria)
    return _upload_bytes(campaign.drive_folder_id, fname, data, "application/json")


def delete_campaign_files(campaign):
    """Best-effort delete of all files for a campaign in its Drive folder."""
    service = _get_service()
    if not service or not campaign.drive_folder_id:
        return
    safe = _safe_filename(campaign.name)
    # Match `<safe>_master.csv` and `<safe>__*.json`
    try:
        q = (f"'{campaign.drive_folder_id}' in parents and "
             f"(name = '{safe}_master.csv' or name contains '{safe}__') and trashed = false")
        resp = service.files().list(
            q=q, fields="files(id, name)", pageSize=100,
            supportsAllDrives=True, includeItemsFromAllDrives=True
        ).execute()
        for f in resp.get("files", []):
            try:
                service.files().delete(fileId=f["id"], supportsAllDrives=True).execute()
                logger.info("Drive delete OK: %s", f["name"])
            except Exception:
                logger.exception("Drive delete failed: %s", f["name"])
    except Exception:
        logger.exception("Drive list-for-delete failed for %s", campaign.id)


def sync_all_in_background(app, campaign_id, annotator_id, criteria):
    """Schedule a non-blocking sync. Updates campaign.drive_last_status on completion.

    Runs in a daemon thread so the annotator's HTTP response isn't delayed by Drive.
    """
    def _run():
        with app.app_context():
            from models import Campaign, Rating, Annotator, db as _db
            from datetime import datetime as _dt
            campaign = Campaign.query.get(campaign_id)
            annotator = Annotator.query.get(annotator_id)
            if not campaign or not annotator:
                return
            all_ok = True
            last_err = ""
            try:
                all_ratings = Rating.query.filter_by(campaign_id=campaign_id).all()
                criterion_ids = [c["id"] for c in criteria]
                completed = [r for r in all_ratings if r.is_complete_for(criterion_ids)]
                ok, err = sync_campaign_master_csv(campaign, completed, criteria)
                if not ok:
                    all_ok = False
                    last_err = err
            except Exception as e:
                logger.exception("Background master CSV sync failed for %s", campaign_id)
                all_ok = False
                last_err = str(e)
            try:
                annotator_ratings = Rating.query.filter_by(
                    campaign_id=campaign_id, annotator_id=annotator_id
                ).all()
                ok, err = sync_annotator_snapshot(campaign, annotator, annotator_ratings, criteria)
                if not ok:
                    all_ok = False
                    last_err = err
            except Exception as e:
                logger.exception("Background annotator snapshot sync failed: %s/%s",
                                 campaign_id, annotator_id)
                all_ok = False
                last_err = str(e)

            # Update sync status on the campaign so admin UI can show it
            campaign.drive_last_status = "ok" if all_ok else "error"
            campaign.drive_last_error = "" if all_ok else last_err[:1000]
            campaign.drive_last_sync_at = _dt.utcnow()
            _db.session.commit()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
