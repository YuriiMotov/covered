<p align="center">
<img src="../docs/img/covered.png" alt="covered" width="200"><br />
<b>Make it green.</b>
</p>

# Covered backend

FastAPI app that stores HTML coverage reports, serves them over HTTP, and exposes an SVG badge endpoint reflecting each repository's latest coverage on its default branch.

This document is the operator manual — provisioning the external services, configuring the backend, deploying to [FastAPI Cloud](https://fastapicloud.com/), and verifying the install. For the project overview and the CI side of the picture, see the [top-level README](../README.md).

## Prerequisites

Before deploying the backend, you will need:

- **AWS infrastructure** — an S3 bucket, an IAM user, and an IAM role. See [aws.md](aws.md) for step-by-step setup with the exact policy JSON. The short version of what you'll end up with:
  - An **S3 bucket** for HTML reports. The backend writes objects under the `sites/<site_id>/...` prefix and reads them back when serving reports. No public access or static website hosting — the backend serves files itself.
  - An **IAM user** for the backend, with a long-lived access key. It needs `s3:PutObject` + `s3:GetObject` on `<bucket>/sites/*`, and `sts:AssumeRole` on the upload role.
  - An **IAM role for uploads**, with a permissions policy granting `s3:PutObject` on `<bucket>/sites/*` and a trust policy allowing the IAM user to assume it. The backend assumes this role via STS to mint short-lived credentials for the CLI, further narrowed per upload by a session policy scoped to a single `site_id` — so the CLI never receives credentials that can write outside its own report directory.

- **A Redis instance** (optional) reachable from the backend. It is used as a short-lived cache for rendered badge SVGs.

- **A GitHub token** with read access to commit statuses on every repository whose coverage you want to display. A fine-grained PAT with `Commit statuses: Read-only` is sufficient; a classic PAT with `repo` (or `public_repo` for public repositories only) also works.

## Environment variables

The backend is configured via environment variables, loaded by `app.config.Settings` (pydantic-settings).

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | yes | — | Token the CLI uses to authenticate with `/coverage/create-site/` and `/coverage/invalidate-cache/*`. Treat as a secret. |
| `AWS_REGION` | no | `us-east-1` | Region of the S3 bucket. |
| `AWS_BUCKET` | no | `covered` | Name of the S3 bucket reports are stored in. |
| `AWS_ACCESS_KEY_ID` | yes | — | Access key of the backend's IAM user (the one with `s3:GetObject`/`PutObject` on `<bucket>/sites/*` and `sts:AssumeRole` on the upload role). |
| `AWS_SECRET_ACCESS_KEY` | yes | — | Secret access key for the above IAM user. |
| `AWS_UPLOAD_ROLE_ARN` | yes | — | ARN of the IAM role the backend assumes via STS to mint short-lived upload credentials for the CLI. |
| `REDIS_URL` | no | — | Connection URL for Redis. Used to cache rendered badge SVGs (60 s TTL). |
| `GITHUB_TOKEN` | yes | — | GitHub token used to read commit statuses. See [Prerequisites](#prerequisites) for the required scopes. |

## Deploying to FastAPI Cloud

Covered is designed to run on [FastAPI Cloud](https://fastapicloud.com/). The deploy flow:

1. **Sign up at [fastapicloud.com](https://fastapicloud.com/).** Access is waitlist-only at the moment — getting in typically takes a couple of days.

2. **Create an app** in the FastAPI Cloud dashboard.

3. **Configure the environment variables** on the app (the [table above](#environment-variables) lists all of them).

4. **Clone (or fork) this repository.** Fork if you plan to customize the backend.

5. **Install dependencies and deploy.** From the repo root:

   ```bash
   uv sync --project backend
   cd backend
   fastapi deploy
   ```

6. **Authorize the CLI.** On the first deploy, the CLI opens your browser to authorize itself against your FastAPI Cloud account — grant the requested permissions.

7. **Pick your team and app** when prompted. Typically your personal team and the app you created in step 2; leave the rest at their defaults.

8. **Confirm and wait** for the deploy to report success.

9. **Verify** by triggering the [`Coverage upload` workflow](../README.md#workflow-setup) in a repository configured to use this backend. If anything fails, the FastAPI Cloud dashboard logs are the first place to look.
