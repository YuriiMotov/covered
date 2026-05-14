# AWS setup

This guide walks through creating the S3 bucket, IAM user, and IAM role the [Covered backend](README.md) needs. If you already have AWS infrastructure to reuse, you can adapt the policies described here.

## What you'll create

Three resources:

1. **An S3 bucket** — stores the uploaded HTML coverage reports.
2. **An IAM user** for the backend — provides the long-lived credentials the backend uses to read reports back from S3 and to assume the upload role.
3. **An IAM role** (the *upload role*) — assumed by the backend via STS to mint short-lived credentials. The backend hands those temporary credentials to the CLI, scoped via a session policy to a single report's directory, so the CLI can never write outside its own report.

This two-credential design (long-lived backend user + short-lived assumed role) is what isolates each CI upload.

## Placeholders used below

| Placeholder | Meaning | Example |
|---|---|---|
| `covered-reports` | The S3 bucket name | `acme-covered-reports` |
| `<region>` | AWS region | `us-east-1` |
| `<account-id>` | Your 12-digit AWS account ID | `123456789012` |

Replace them with your own values in the policy JSON.

## 1. Create the S3 bucket

In the [S3 Console](https://console.aws.amazon.com/s3/):

1. Click **Create bucket**.
2. **Bucket name**: `covered-reports` (or whatever you prefer).
3. **Region**: `<region>`. Pick one close to where the backend will run; the backend's `AWS_REGION` env var must match this.
4. Leave all other settings at their defaults. In particular, **keep "Block all public access" enabled** — the backend serves files itself; the bucket does not need to be public.
5. Click **Create bucket**.

## 2. Create the IAM user

We create the user before the role so the role's trust policy can reference the user's ARN.

In the [IAM Console](https://console.aws.amazon.com/iam/), under **Users**:

1. Click **Create user**.
2. **User name**: `covered-backend` (any name).
3. Do **not** check "Provide user access to the AWS Management Console" — the backend uses programmatic access only.
4. On the **Set permissions** step, choose **Attach policies directly** and don't select anything. Click **Next**, then **Create user**.

Open the created user and copy its **ARN** from the **Summary** panel — it looks like `arn:aws:iam::<account-id>:user/covered-backend`. You'll paste it into the role's trust policy in the next step.

## 3. Create the upload role

In the IAM Console, under **Roles**:

1. Click **Create role**.
2. **Trusted entity type**: **Custom trust policy**.
3. Paste this trust policy, substituting `<account-id>` and the user name if you changed it:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Principal": { "AWS": "arn:aws:iam::<account-id>:user/covered-backend" },
         "Action": "sts:AssumeRole"
       }
     ]
   }
   ```



4. Click **Next**. Skip attaching any managed policy on this screen — we'll add an inline policy after creation.
5. **Role name**: `covered-upload-role` (any name).
6. Click **Create role**.

Now open the role and add its permissions policy. On the **Permissions** tab, click **Add permissions → Create inline policy**, switch to the **JSON** editor, and paste:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::covered-reports/sites/*"
    }
  ]
}
```

This is the outer bound — when the backend assumes the role, it applies a session policy that narrows access to a single `sites/<site_id>/*` prefix for that specific upload.

Save the policy (name it `covered-upload-policy`).

Finally, copy the role's **ARN** from the role's **Summary** panel — it looks like `arn:aws:iam::<account-id>:role/covered-upload-role`. This is what you'll set as `AWS_UPLOAD_ROLE_ARN` on the backend.

## 4. Attach the user's permissions policy

Go back to the user from step 2. On the **Permissions** tab, click **Add permissions → Create inline policy**, switch to **JSON**, and paste:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3Access",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject"],
      "Resource": "arn:aws:s3:::covered-reports/sites/*"
    },
    {
      "Sid": "AssumeUploadRole",
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::<account-id>:role/covered-upload-role"
    }
  ]
}
```

Save the policy (name it `covered-backend-policy`).

The backend needs `s3:PutObject` (to create the per-upload `site_id` directory) and `s3:GetObject` (to serve report files back to readers), plus `sts:AssumeRole` to mint upload credentials for the CLI.

## 5. Create the access key

On the user's **Security credentials** tab:

1. Click **Create access key**.
2. **Use case**: **Other**, then click **Next**.
3. Click **Create access key**.
4. **Copy the access key ID and the secret access key now** — the secret is shown only once and can never be retrieved again. These become `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` on the backend.

## Backend env vars, recapped

After all five steps you have:

| Backend env var | Value |
|---|---|
| `AWS_REGION` | The region of the bucket (e.g. `us-east-1`) |
| `AWS_BUCKET` | `covered-reports` |
| `AWS_ACCESS_KEY_ID` | From step 5 |
| `AWS_SECRET_ACCESS_KEY` | From step 5 |
| `AWS_UPLOAD_ROLE_ARN` | From step 3 |

Set these on FastAPI Cloud following [Deploying to FastAPI Cloud](README.md#deploying-to-fastapi-cloud).
