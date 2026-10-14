"""Phase 0 guard: make sure we are talking to the right GCP project with the right key.

Offline checks on BQ_KEYFILE (an absolute path to a service-account key for GCP_PROJECT_ID,
outside the repo), then online checks with that key: the three datasets exist in BQ_LOCATION,
and the service account can create query jobs (a free dry run).
"""

import json
import os
import stat
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ["GCP_PROJECT_ID", "BQ_KEYFILE", "BQ_LOCATION", "BQ_DATASET_RAW", "BQ_DATASET_DEV", "BQ_DATASET_PROD"]


def fail(msg: str) -> int:
    print(f"FAIL: {msg}")
    return 1


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        return fail(f"missing in .env: {', '.join(missing)}")

    project = os.environ["GCP_PROJECT_ID"]
    location = os.environ["BQ_LOCATION"]
    keyfile_setting = os.environ["BQ_KEYFILE"]
    # dbt passes the path straight to the Google client, which expands neither ~ nor relative paths.
    if not Path(keyfile_setting).is_absolute():
        return fail(f"BQ_KEYFILE must be an absolute path (dbt can't expand ~ or relative paths): {keyfile_setting}")
    keyfile = Path(keyfile_setting).resolve()

    # --- Offline: the key file itself ---
    if not keyfile.is_file():
        return fail(f"BQ_KEYFILE not found: {keyfile}")
    if keyfile.is_relative_to(REPO_ROOT):
        return fail(f"key file is inside the repo ({keyfile}); move it outside so it can never be committed")
    if keyfile.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(f"WARN: {keyfile} is readable by other users; run: chmod 600 '{keyfile}'")

    key = json.loads(keyfile.read_text())
    if key.get("type") != "service_account":
        return fail(f"key type is {key.get('type')!r}, expected 'service_account'")
    if key.get("project_id") != project:
        return fail(f"key belongs to project {key.get('project_id')!r}, but GCP_PROJECT_ID is {project!r}")
    email = key.get("client_email", "")
    if not email.endswith(f"@{project}.iam.gserviceaccount.com"):
        return fail(f"service account {email!r} is not from project {project!r}")
    print(f"Key OK: {email} (project {project})")

    # --- Online: datasets and job permission, using only this key ---
    from google.api_core.exceptions import NotFound
    from google.cloud import bigquery
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(str(keyfile))
    client = bigquery.Client(project=project, credentials=creds, location=location)

    problems = 0
    for name in (os.environ["BQ_DATASET_RAW"], os.environ["BQ_DATASET_DEV"], os.environ["BQ_DATASET_PROD"]):
        try:
            ds = client.get_dataset(f"{project}.{name}")
        except NotFound:
            print(f"FAIL: dataset {name} not found (run make setup-gcp)")
            problems += 1
            continue
        except Exception as exc:  # Forbidden, auth errors, ...
            print(f"FAIL: dataset {name}: {type(exc).__name__}: {exc}")
            problems += 1
            continue
        status = "OK" if ds.location.lower() == location.lower() else f"WRONG REGION (want {location})"
        problems += status != "OK"
        print(f"Dataset {name:<10} location={ds.location:<12} {status}")

    job = client.query("select 1", job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
    print(f"Dry-run query OK (would scan {job.total_bytes_processed} bytes)")

    if problems:
        return fail(f"{problems} dataset problem(s)")
    print("GCP identity check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
