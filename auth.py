"""Session-based auth helpers.

Admin: single account, credentials in env vars.
Annotator: row in the Annotators table.
Sessions: Flask's built-in secure cookies.
"""

import os
from functools import wraps

from flask import session, redirect, url_for, flash, request

from models import Annotator

ADMIN_EMAIL = os.environ.get("MT_EVAL_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("MT_EVAL_ADMIN_PASSWORD", "change-me")


def admin_login(email, password):
    """Return True if credentials match the admin env vars."""
    return email.strip().lower() == ADMIN_EMAIL.strip().lower() and password == ADMIN_PASSWORD


def current_admin():
    return session.get("admin_email") if session.get("is_admin") else None


def current_annotator():
    aid = session.get("annotator_id")
    if not aid:
        return None
    return Annotator.query.get(aid)


def require_admin(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Please sign in as admin.", "info")
            return redirect(url_for("admin_login_view", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def require_annotator(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("annotator_id"):
            campaign_id = kwargs.get("campaign_id") or request.view_args.get("campaign_id", "")
            flash("Please sign in to continue.", "info")
            return redirect(url_for("annotator_login_view", campaign_id=campaign_id))
        return view(*args, **kwargs)
    return wrapper
