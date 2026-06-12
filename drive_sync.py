import csv
import io
import json
import logging
import os
import threading

import boto3

logger = logging.getLogger(__name__)

AWS_REGION = os.environ.get("AWS_REGION")
AWS_BUCKET = os.environ.get("S3_BUCKET")

_s3 = boto3.client(
"s3",
region_name=AWS_REGION,
aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
)

def service_account_email():
    return None

def campaign_prefix(campaign):
    return f"campaign{campaign.id}"

def _upload_bytes(key, data, mime_type):
    # Debug
    print("Uploading to S3...")
    print("Bucket:", AWS_BUCKET)
    print("Key:", key)
    # Till Here
    try:
        _s3.put_object(
            Bucket=AWS_BUCKET,
            Key=key,
            Body=data,
            ContentType=mime_type,
        )

        logger.info(
            "S3 upload OK: s3://%s/%s (%d bytes)",
            AWS_BUCKET,
            key,
            len(data),
        )

        return True, ""

    except Exception as e: 
        print("S3 ERROR:", repr(e))
        logger.exception("S3 upload failed: %s", key)
        return False, str(e)

# CSV helpers

def _csv_header(criteria):
    header = [
    "response_id",
    "timestamp_utc",
    "annotator_name",
    "annotator_email",
    "annotator_native_lang",
    "campaign_id",
    "campaign_name",
    "source_language",
    "target_language",
    "script",
    "segment_id",
    "system",
    "domain",
    "source",
    "target",
    "reference",
    ]

    for c in criteria:
        header.append(c["id"])

    for c in criteria:
        header.append(c["id"] + "_spans")

    header += [
        "comments",
        "time_spent_seconds",
        "updated_at_utc",
    ]

    return header

def _safe_filename(name):
    keep = []

    for ch in name:
        if ch.isalnum() or ch in "-_ ":
            keep.append(ch)
        else:
            keep.append("_")

    return "".join(keep).strip() or "campaign"

def _build_master_csv(campaign, ratings, criteria):
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow(_csv_header(criteria))

    for r in ratings:
        seg = campaign.segment_by_id(r.segment_id) or {}
        ann = r.annotator

        scores = r.scores_dict()
        spans = r.spans_dict()

        row = [
            r.id,
            r.updated_at.isoformat(timespec="seconds") + "Z",
            ann.name,
            ann.email,
            ann.native_language or "",
            campaign.id,
            campaign.name,
            campaign.source_language,
            campaign.target_language,
            campaign.script or "",
            r.segment_id,
            seg.get("system", ""),
            seg.get("domain", ""),
            seg.get("source", ""),
            seg.get("target", ""),
            seg.get("reference", ""),
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

def _build_annotator_snapshot(
    campaign,
    annotator,
    ratings,
    criteria,
    ):
    payload = {
    "campaign": {
    "id": campaign.id,
    "name": campaign.name,
    "source_language": campaign.source_language,
    "target_language": campaign.target_language,
    "script": campaign.script,
    "num_segments": campaign.num_segments,
    "criteria": [
    {
    "id": c["id"],
    "name": c["name"],
    }
    for c in criteria
    ],
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

    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


# Sync helpers

def sync_campaign_master_csv(
    campaign,
    all_ratings,
    criteria,
    ):
    key = f"{campaign_prefix(campaign)}/master.csv"

    data = _build_master_csv(
        campaign,
        all_ratings,
        criteria,
    )

    return _upload_bytes(
        key,
        data,
        "text/csv",
    )

def sync_annotator_snapshot(
    campaign,
    annotator,
    annotator_ratings,
    criteria,
    ):
    safe_email = (
    annotator.email
    .replace("@", "at")
    .replace(".", "_")
    )

    key = (
        f"{campaign_prefix(campaign)}/"
        f"annotators/{safe_email}.json"
    )

    data = _build_annotator_snapshot(
        campaign,
        annotator,
        annotator_ratings,
        criteria,
    )

    return _upload_bytes(
        key,
        data,
        "application/json",
    )

def delete_campaign_files(campaign):
    prefix = campaign_prefix(campaign)

    try:
        paginator = _s3.get_paginator("list_objects_v2")

        for page in paginator.paginate(
            Bucket=AWS_BUCKET,
            Prefix=prefix,
        ):
            for obj in page.get("Contents", []):
                try:
                    _s3.delete_object(
                        Bucket=AWS_BUCKET,
                        Key=obj["Key"],
                    )

                    logger.info(
                        "Deleted S3 object: %s",
                        obj["Key"],
                    )

                except Exception:
                    logger.exception(
                        "Failed deleting S3 object: %s",
                        obj["Key"],
                    )

    except Exception:
        logger.exception(
            "Failed deleting campaign files for %s",
            campaign.id,
        )

# Background sync

def sync_all_in_background(
    app,
    campaign_id,
    annotator_id,
    criteria,
    ):
    
    def _run():
        # Debug
        print("=" * 50)
        print("BACKGROUND SYNC STARTED")
        print("campaign_id =", campaign_id)
        print("annotator_id =", annotator_id)
        print("=" * 50)
        # Till here
        with app.app_context():
            from sqlalchemy.orm import scoped_session, sessionmaker
            from models import (
                Campaign,
                Rating,
                Annotator,
                db as _db,
                )

            from datetime import datetime as _dt

            session = scoped_session(
                sessionmaker(bind=_db.engine)
            )

            campaign = session.query(Campaign).get(
                campaign_id
            )

            annotator = session.query(Annotator).get(
                annotator_id
            )

            if not campaign or not annotator:
                session.remove()
                return

            all_ok = True
            last_err = ""

            try:
                all_ratings = (
                    session.query(Rating)
                    .filter_by(
                        campaign_id=campaign_id
                    )
                    .all()
                )

                criterion_ids = [
                    c["id"]
                    for c in criteria
                ]

                completed = [
                    r
                    for r in all_ratings
                    if r.is_complete_for(
                        criterion_ids
                    )
                ]

                ok, err = sync_campaign_master_csv(
                    campaign,
                    completed,
                    criteria,
                )

                if not ok:
                    all_ok = False
                    last_err = err

            except Exception as e:
                logger.exception(
                    "Master CSV sync failed"
                )
                all_ok = False
                last_err = str(e)

            try:
                annotator_ratings = (
                    session.query(Rating)
                    .filter_by(
                        campaign_id=campaign_id,
                        annotator_id=annotator_id,
                    )
                    .all()
                )

                ok, err = sync_annotator_snapshot(
                    campaign,
                    annotator,
                    annotator_ratings,
                    criteria,
                )

                if not ok:
                    all_ok = False
                    last_err = err

            except Exception as e:
                logger.exception(
                    "Annotator snapshot sync failed"
                )
                all_ok = False
                last_err = str(e)

            campaign.drive_last_status = (
                "ok"
                if all_ok
                else "error"
            )

            campaign.drive_last_error = (
                ""
                if all_ok
                else last_err[:1000]
            )

            campaign.drive_last_sync_at = (
                _dt.utcnow()
            )

            session.commit()
            session.remove()

    t = threading.Thread(
    target=_run,
    daemon=True,
    )

    t.start()