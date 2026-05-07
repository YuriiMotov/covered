<p align="center">
<img src="https://raw.githubusercontent.com/YuriiMotov/covered/master/docs/img/covered.png" alt="covered" width="200"><br />
<b>Make it green.</b>
</p>

# covered

CLI for uploading HTML coverage reports to a self-hosted [covered](https://github.com/YuriiMotov/covered) backend, then setting a GitHub commit status with the coverage value.

## Install

```bash
pip install covered
```

Requires Python 3.13+.

## Usage

Upload an HTML coverage report directory:

```bash
covered upload ./htmlcov \
  --api-url https://covered.example.com \
  --api-key "$COVERED_API_KEY" \
  --repo-owner my-org \
  --repo-name my-repo \
  --commit-sha "$GITHUB_SHA" \
  --gh-token "$GITHUB_TOKEN" \
  --coverage-threshold 90 \
  --purge-cache
```

Every option also reads from a matching environment variable (`COVERED_API_URL`, `COVERED_API_KEY`, `COVERED_REPO_OWNER`, `COVERED_REPO_NAME`, `COVERED_COMMIT_SHA`, `COVERED_GH_TOKEN`, `COVERED_COVERAGE_THRESHOLD`, `COVERED_PURGE_CACHE`), which is the typical way to use it from CI.

`covered upload --help` lists all options.

## What it does

1. Requests a temporary upload session from the covered backend.
2. Uploads every file in the given directory to S3 in parallel (default concurrency: 50).
3. Reads the coverage percentage from `index.html` in the report.
4. Posts a `covered` commit status to GitHub (`success` if `>= --coverage-threshold`, otherwise `failure`).
5. On the default branch, invalidates the badge cache and purges GitHub's Camo cache for the README badge.

## License

MIT. See the project repository for the full license text.
