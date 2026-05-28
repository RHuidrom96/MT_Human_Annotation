# MT Evaluation Platform

A multi-campaign platform for human evaluation of machine translation. An admin
creates campaigns, uploads segment files, and shares per-campaign links.
Annotators register a global account once and can join any campaign. Each
annotation can be edited until the admin closes the campaign.

## Features

- **Admin role** (single account, configured via env vars) with a dashboard, campaign creation form, per-campaign progress view, master CSV download, and campaign close action.
- **Annotator role** with global account (one email + password works across all campaigns).
- **Three Likert criteria**: adequacy, fluency, meaning preservation.
- **Error-span marking** on the translation text — three overlapping error types, colored highlights, free-form cursor selection.
- **Persistent on-screen instructions** plus per-criterion guidance.
- **Optional reference translation** behind a disclosure.
- **Resumable** — annotators can come back, jump to any segment, and edit until the campaign is closed.
- **Storage**: SQLite locally (`data/app.db`) + optional Google Drive mirror via service account (master CSV + per-annotator JSON snapshot).

## Quick start (local development)

```bash
cd mt_eval_v2
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Required environment variables
export MT_EVAL_ADMIN_EMAIL=youremail@example.com
export MT_EVAL_ADMIN_PASSWORD=a-strong-password
export MT_EVAL_SECRET_KEY=some-long-random-string

# Optional: enable Drive mirror (see "Google Drive setup" below)
# Place data/gdrive_sa.json

python app.py            # serves on PORT or default 8000
```

Open <http://localhost:8000> and click **Admin sign in**.

## Production

Run with gunicorn behind a reverse proxy (nginx, Caddy, etc.) terminating TLS:

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

Make sure `data/` is on persistent storage so SQLite, segment files, and the
service-account JSON survive restarts.

## Google Drive setup (optional)

The platform writes a master CSV and per-annotator JSON to a Drive folder you
control. Setup once:

1. Go to <https://console.cloud.google.com> → create or select a project.
2. APIs & Services → Library → enable the **Google Drive API**.
3. IAM & Admin → Service Accounts → Create service account. Skip role grants.
4. On the service account row → Keys → Add key → Create new key → JSON. Download.
5. Save the downloaded JSON to `mt_eval_v2/data/gdrive_sa.json`.
6. Open the JSON and copy the `client_email` field (looks like `xxxxx@your-project.iam.gserviceaccount.com`).
7. In Google Drive, create a folder for each campaign (or one shared folder).
8. Share each folder with the `client_email` from step 6, granting **Editor** access.
9. Copy the folder ID from the Drive URL (the long string after `/folders/`).
10. When creating a campaign in the admin UI, paste the folder ID into the
    "Google Drive folder ID" field.

After this, every save by an annotator triggers a non-blocking upload of:
- `<campaign_name>_master.csv` — one row per (annotator, segment) rating
- `<campaign_name>__<annotator_email>.json` — that annotator's full snapshot

The local SQLite DB is always the source of truth; Drive is a mirror. If Drive
is briefly unreachable, ratings still save and you can re-trigger the master
CSV via the admin "Download master CSV" button.

## Admin workflow

1. **Sign in** at `/admin/login` with the credentials in your env vars.
2. **New campaign**: set name, source/target languages, optional script (Bengali / Meitei Mayek for Manipuri), upload segments JSON, and optionally paste a Drive folder ID.
3. **Copy the share link** from the campaign detail page and send it to your annotators (e.g. by email).
4. **Monitor progress**: the detail page shows each annotator with their completion percentage.
5. **Download CSV** at any time — completed ratings only.
6. **Close campaign** when done — irreversible. After this, annotators can't edit.

## Annotator workflow

1. Click the share link from the admin.
2. **Create account** (name, email, password, optional native language) — or sign in if they've worked on another campaign before.
3. Rate each segment on three criteria using a 1–5 scale.
4. Mark error spans in the translation by selecting words with the cursor.
5. Click **Save & next** to advance. Ratings save to the server (and to Drive in the background).
6. The "Jump to…" button opens a modal with all segments and their status, for review and editing.
7. Once finished, the thank-you screen offers a "Review my ratings" button — they can come back to this URL any time until the admin closes the campaign.

## Segment file format

A JSON array of objects:

```json
[
  {
    "id": "seg_001",
    "source": "Source sentence text.",
    "target": "Machine translation text.",
    "reference": "Optional human reference translation.",
    "system": "SystemA",
    "domain": "Medical"
  },
  ...
]
```

Required fields: `id` (unique within the file), `source`, `target`.
Optional: `reference`, `system`, `domain`.

## CSV output columns

| Column | Description |
|---|---|
| `response_id` | UUID for this rating |
| `timestamp_utc` | Last update time |
| `annotator_name`, `annotator_email`, `annotator_native_lang` | Annotator info |
| `campaign_id`, `campaign_name` | Campaign info |
| `source_language`, `target_language`, `script` | Languages and optional script |
| `segment_id`, `system`, `domain` | Segment metadata |
| `source`, `target`, `reference` | The texts |
| `adequacy`, `fluency`, `meaning_preservation` | Integer 1–5 |
| `adequacy_spans`, `fluency_spans`, `meaning_preservation_spans` | JSON arrays of `[start, end]` character offsets into `target` |
| `comments` | Free-text comments |
| `time_spent_seconds` | Cumulative time spent on this segment |
| `updated_at_utc` | Last update timestamp |

The weighted score is no longer displayed in the UI but you can compute it from
the three Likert columns after the fact:
`0.35*adequacy + 0.30*fluency + 0.35*meaning_preservation` (adjust weights as desired).

## Files

```
mt_eval_v2/
├── app.py              Flask app + routes
├── auth.py             Session helpers, admin/annotator decorators
├── models.py           SQLAlchemy: Annotator, Campaign, Rating
├── drive_sync.py       Google Drive service-account uploads
├── requirements.txt
├── README.md
├── templates/
│   ├── base.html
│   ├── landing.html
│   ├── admin_login.html
│   ├── admin_dashboard.html
│   ├── admin_campaign_new.html
│   ├── admin_campaign_detail.html
│   ├── annotator_login.html
│   ├── campaign_closed.html
│   └── rate.html
├── static/
│   ├── style.css
│   └── rate.js
└── data/
    ├── app.db              (auto-created)
    └── gdrive_sa.json      (place here to enable Drive)
```

## What the admin configures per campaign (v2.1+)

When creating a campaign, the admin now controls:

- **Evaluation criteria**: add/remove criteria, each with a name and a definition. These appear on the rating screen and become the CSV score columns. Defaults to Adequacy / Fluency / Meaning preservation, which you can edit or replace entirely.
- **Span scope**: choose whether annotators mark error spans in the **target only** or in **both source and target**. In "both" mode each stored span carries a pane tag (`"source"` or `"target"`).
- **Annotation instructions**: free text shown in the collapsible instructions panel on every rating page.

## Deleting a campaign

On a campaign's detail page there's a **Delete campaign** card. To prevent accidents, the admin must type the exact campaign name to confirm. Deleting removes the campaign, all its ratings (local DB), and best-effort removes the campaign's files from the linked Drive folder.

## CSV columns are dynamic

Because criteria are now per-campaign, the CSV columns adapt: one column per criterion id (the score), plus one `<criterion_id>_spans` column each. Span cells are JSON arrays; in target-only mode items are `[start, end]`, in both-mode they are `[start, end, "target"|"source"]`.

## Drive sync status

The campaign detail page shows the outcome of the most recent Drive sync (success / failure with the error message / not-yet-run). If a sync fails, the data is still safe in the local DB and downloadable via the CSV button; the error text tells you what to fix (most commonly: share the folder with the service account as Editor).

## Security notes

- Set `MT_EVAL_SECRET_KEY` to a long random string in production. Without it, sessions are not secure.
- Use HTTPS in production. Flask cookie sessions are signed but not encrypted.
- Annotator passwords are bcrypt-hashed.
- The admin password is read from `MT_EVAL_ADMIN_PASSWORD` — store it via your platform's secrets manager, not in source control.
- Set a sensible `MAX_CONTENT_LENGTH` (default 16 MB) for the segment file upload.
- The `service-account JSON` grants Drive access; keep `data/gdrive_sa.json` out of source control.
