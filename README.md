# MT Human Annotation Platform

A web-based platform for human evaluation of machine translation (MT) outputs.

Administrators can create annotation campaigns, upload translation segments, monitor progress, and export evaluation results. Annotators create a single account and can participate in multiple campaigns through shareable links.

---

## Features

## Campaign Management

* Create multiple evaluation campaigns
* Upload translation segment datasets
* Configure custom evaluation criteria
* Configure annotation instructions
* Open and close campaigns
* Track annotator progress

## Annotation Interface

* Segment-by-segment evaluation
* Configurable Likert-scale criteria
* Error span annotation
* Optional reference translations
* Autosaved progress
* Resume annotation at any time

## Data Export

* Campaign master CSV export
* Annotator JSON snapshots
* Automatic synchronization to Amazon S3

## Authentication

* Admin authentication
* Annotator accounts
* Password hashing using bcrypt

---

## Architecture

```text
Annotator
    ↓
Flask Application
    ↓
PostgreSQL Database
    ↓
Background Sync
    ↓
Amazon S3 Storage
```

Generated exports:

```text
campaign<campaign_id>/
├── master.csv
└── annotators/
    ├── user1atemail_com.json
    ├── user2atemail_com.json
    └── ...
```

---

## Technology Stack

* Python 3.13+
* Flask
* SQLAlchemy
* PostgreSQL
* Amazon S3
* Gunicorn

---

## Installation

Clone the repository:

```bash
git clone <repository-url>
cd MT_Human_Annotation
```

Create a virtual environment:

```bash
python -m venv venv
```

Linux/macOS:

```bash
source venv/bin/activate
```

Windows:

```cmd
venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Environment Variables

Copy the example environment file:

Linux/macOS:

```bash
cp .env.example .env
```

Windows:

```cmd
copy .env.example .env
```

Then edit `.env` and replace the placeholder values with your own configuration:

* PostgreSQL connection string
* Admin email and password
* Flask secret key
* AWS S3 credentials
* S3 bucket name

The application will read these values automatically at startup.

---

## Running Locally

```bash
python app.py
```

The application automatically creates database tables on startup.

Default address:

```text
http://localhost:8000
```

---

## AWS S3 Setup

## Create an S3 Bucket

Create a bucket in AWS S3.

Example:

```text
wmt-human-annotation-storage
```

## Create an IAM User

Create an IAM user with permissions to:

```text
s3:PutObject
s3:GetObject
s3:DeleteObject
s3:ListBucket
```

Generate:

* Access Key ID
* Secret Access Key

## Configure Environment Variables

Add the credentials to your `.env` file:

```env
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=...
S3_BUCKET=...
```

---

## Deployment (Render)

## Build Command

```bash
pip install -r requirements.txt
```

## Start Command

```bash
gunicorn app:app
```

## Required Environment Variables

Configure all variables from the `.env` example inside Render's Environment settings.

Create a PostgreSQL instance and set:

```env
DATABASE_URL=<render-postgres-url>
```

---

## Admin Workflow

1. Sign in as administrator.
2. Create a campaign.
3. Upload segment JSON.
4. Share campaign link.
5. Monitor annotation progress.
6. Export results.
7. Close campaign when complete.

---

## Annotator Workflow

1. Open campaign link.
2. Register or sign in.
3. Evaluate segments.
4. Mark error spans.
5. Save ratings.
6. Resume later if needed.

---

## Segment File Format

Example:

```json
[
  {
    "id": "seg_001",
    "source": "Source sentence.",
    "target": "Machine translation output.",
    "reference": "Reference translation.",
    "system": "SystemA",
    "domain": "Medical"
  }
]
```

Required fields:

* id
* source
* target

Optional fields:

* reference
* system
* domain

---

## Project Structure

```text
MT_Human_Annotation/
├── app.py
├── auth.py
├── models.py
├── drive_sync.py
├── requirements.txt
├── templates/
├── static/
└── README.md
```

---

## Security Notes

* Never commit `.env`
* Never commit AWS credentials
* Use HTTPS in production
* Use a strong secret key
* Use a strong admin password
* Annotator passwords are securely hashed

---

## License
