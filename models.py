# models.py
"""Database models for the MT evaluation platform.

There is one global admin (credentials in env vars, no DB row).
Annotators are global (one account works across campaigns).
Each campaign has its own segments and ratings.
"""

import json
import uuid
from datetime import datetime

import bcrypt
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def _uuid():
    return uuid.uuid4().hex


class Annotator(db.Model):
    """Global annotator account. Same login works for any campaign they join."""
    __tablename__ = "annotators"

    id = db.Column(db.String(32), primary_key=True, default=_uuid)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(200), nullable=False)
    native_language = db.Column(db.String(100), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    ratings = db.relationship("Rating", backref="annotator", lazy="dynamic")

    def set_password(self, raw):
        self.password_hash = bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def check_password(self, raw):
        try:
            return bcrypt.checkpw(raw.encode("utf-8"), self.password_hash.encode("utf-8"))
        except (ValueError, AttributeError):
            return False


class Campaign(db.Model):
    """One evaluation campaign created by the admin."""
    __tablename__ = "campaigns"

    id = db.Column(db.String(32), primary_key=True, default=_uuid)
    name = db.Column(db.String(200), nullable=False)
    source_language = db.Column(db.String(100), nullable=False)
    target_language = db.Column(db.String(100), nullable=False)
    # For Manipuri: "bengali" or "meetei" -- otherwise None.
    script = db.Column(db.String(50), default=None)
    # Segments as a JSON-encoded list of dicts {id, source, target, reference?, system?, domain?}
    segments_json = db.Column(db.Text, nullable=False)
    # Criteria as a JSON-encoded list of dicts {id, name, color, desc, guide}
    # If not set, the app falls back to the built-in defaults.
    criteria_json = db.Column(db.Text, default="")
    # Free-text annotation instructions shown to annotators (Markdown-ish, rendered as text).
    instructions = db.Column(db.Text, default="")
    # Where span annotation is allowed: "target" (default) or "both"
    span_scope = db.Column(db.String(20), default="target")
    # Whether span annotation is enabled at all
    enable_spans = db.Column(db.Boolean, default=True)
    # Google Drive folder ID where data is mirrored
    drive_folder_id = db.Column(db.String(200), default="")
    # Most recent Drive sync status, for display in admin UI
    drive_last_status = db.Column(db.String(20), default="")     # "ok", "error", or ""
    drive_last_error = db.Column(db.Text, default="")
    drive_last_sync_at = db.Column(db.DateTime, default=None)
    # Set when admin closes the campaign -- editing disabled afterward
    closed_at = db.Column(db.DateTime, default=None)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    ratings = db.relationship("Rating", backref="campaign", lazy="dynamic", cascade="all,delete-orphan")

    @property
    def is_closed(self):
        return self.closed_at is not None

    @property
    def segments(self):
        try:
            return json.loads(self.segments_json or "[]")
        except json.JSONDecodeError:
            return []

    @property
    def criteria(self):
        """Return campaign-specific criteria if set, else None (caller should default)."""
        if not self.criteria_json:
            return None
        try:
            return json.loads(self.criteria_json)
        except json.JSONDecodeError:
            return None

    def segment_by_id(self, seg_id):
        for s in self.segments:
            if s.get("id") == seg_id:
                return s
        return None

    @property
    def num_segments(self):
        return len(self.segments)


class Rating(db.Model):
    """One annotator's rating of one segment in one campaign. Editable until close."""
    __tablename__ = "ratings"

    id = db.Column(db.String(32), primary_key=True, default=_uuid)
    campaign_id = db.Column(db.String(32), db.ForeignKey("campaigns.id"), nullable=False, index=True)
    annotator_id = db.Column(db.String(32), db.ForeignKey("annotators.id"), nullable=False, index=True)
    segment_id = db.Column(db.String(200), nullable=False, index=True)

    # JSON: {criterion_id: int(1-5)}
    scores_json = db.Column(db.Text, default="{}")
    # JSON: {criterion_id: [[start, end], ...]}
    spans_json = db.Column(db.Text, default="{}")

    comments = db.Column(db.Text, default="")
    time_spent_seconds = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("campaign_id", "annotator_id", "segment_id",
                            name="uq_campaign_annotator_segment"),
    )

    def scores_dict(self):
        try:
            return json.loads(self.scores_json or "{}")
        except json.JSONDecodeError:
            return {}

    def spans_dict(self):
        try:
            return json.loads(self.spans_json or "{}")
        except json.JSONDecodeError:
            return {}

    def is_complete_for(self, criterion_ids):
        """Return True iff every criterion has a non-null integer score."""
        scores = self.scores_dict()
        return all(isinstance(scores.get(cid), int) and 1 <= scores[cid] <= 5 for cid in criterion_ids)
