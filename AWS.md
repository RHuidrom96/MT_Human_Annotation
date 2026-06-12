# AWS S3 Storage Setup

## Overview

This project uses Amazon S3 to store campaign exports.

Whenever an annotator submits a completed rating:

1. The rating is saved to the database.
2. A background sync job is triggered.
3. A master CSV is generated for the campaign.
4. An annotator JSON snapshot is generated.
5. Both files are uploaded to Amazon S3.

---

## Architecture

```text
Annotator
    ↓
Submit Rating
    ↓
Flask Application
    ↓
Database
    ↓
Background Sync
    ↓
Generate CSV + JSON
    ↓
Amazon S3 Bucket
```

Files stored in S3:

```text
campaign<campaign_id>/
│
├── master.csv
│
└── annotators/
    ├── user1atemail_com.json
    ├── user2atemail_com.json
    └── ...
```

Example:

```text
campaignd5705e68238444c1976045db6009abdb/
│
├── master.csv
│
└── annotators/
    └── test1atemail_com.json
```

---

## Prerequisites

* AWS Account
* Python 3.10+
* boto3
* python-dotenv

Install dependencies:

```bash
pip install boto3 python-dotenv
```

or

```bash
pip install -r requirements.txt
```

---

## Step 1: Create an AWS Account

1. Visit https://aws.amazon.com/
2. Create an AWS account.
3. Sign in to the AWS Management Console.

---

## Step 2: Create an S3 Bucket

1. Open the AWS Console.
2. Search for **S3**.
3. Click **Create bucket**.

Example:

```text
Bucket name:
wmt-human-annotation-storage
```

Select a region:

Example:

```text
us-east-1
```

Leave remaining settings at default values and click:

```text
Create bucket
```

---

## Step 3: Create an IAM User

Do not use the AWS root account for applications.

Create a dedicated IAM user.

1. Open AWS Console.
2. Search for **IAM**.
3. Navigate to: Users
4. Click:

```text
Create User
```

---

## Step 4: Grant S3 Permissions

Choose:

```text
Attach policies directly
```

For development purposes:

```text
AmazonS3FullAccess
```

For production environments, create a restricted custom policy.

Create the user once permissions have been assigned.

---

## Step 5: Generate Access Keys

1. Open the IAM user.
2. Navigate to:

```text
Security Credentials
```

3. Click:

```text
Create Access Key
```

4. Choose:

```text
Application running outside AWS
```

AWS will generate:

```text
Access Key ID
Secret Access Key
```

Example:

```text
Access Key ID:
AKIAxxxxxxxxxxxxxxxx

Secret Access Key:
xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Save the secret access key immediately.

AWS only displays it once.

---

## Step 6: Configure Environment Variables

Create a `.env` file in the project root.

Example:

```env
AWS_REGION=us-east-1

AWS_S3_BUCKET=wmt-human-annotation-storage

AWS_ACCESS_KEY_ID=AKIAxxxxxxxxxxxxxxxx

AWS_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

---

## Step 7: Load Environment Variables

Install python-dotenv if not already installed:

```bash
pip install python-dotenv
```

In `app.py`:

```python
from dotenv import load_dotenv

load_dotenv()
```

Important:

`load_dotenv()` must run before importing `drive_sync.py`.

Correct example:

```python
from dotenv import load_dotenv

load_dotenv()

import drive_sync
```

---

## Step 8: Configure S3 Client

Example S3 client configuration:

```python
import boto3
import os

AWS_REGION = os.environ.get("AWS_REGION")

_s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
)
```

---

## Step 9: Verify Environment Variables

Run:

```python
import os

print(os.environ.get("AWS_REGION"))
print(os.environ.get("AWS_S3_BUCKET"))
print(os.environ.get("AWS_ACCESS_KEY_ID"))
```

Expected output:

```text
us-east-1
wmt-human-annotation-storage
AKIAxxxxxxxxxxxxxxxx
```

If any value is:

```text
None
```

verify:

* `.env` location
* Variable names
* `load_dotenv()` execution
* Import order

---

## Step 10: Verify Uploads

After an annotator submits a rating:

Expected logs:

```text
BACKGROUND SYNC STARTED
```

and

```text
S3 upload OK:
s3://wmt-human-annotation-storage/campaign123/master.csv
```

Verify in AWS Console:

```text
S3
 └── Bucket
      └── campaign123/
            ├── master.csv
            └── annotators/
```

---

## Security Best Practices

Never commit AWS credentials to source control.

Add `.env` to `.gitignore`:

@# Summary

The application stores campaign exports in Amazon S3 using a background synchronization process.

Generated artifacts include:

* Campaign master CSV exports
* Annotator JSON snapshots

Amazon S3 acts as the centralized storage layer for all exported annotation data.
