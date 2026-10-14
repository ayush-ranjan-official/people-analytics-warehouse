#!/usr/bin/env bash
# One-time GCP setup for this project. Safe to re-run: every step checks before it creates.
#
# The project runs in the BigQuery SANDBOX: it has no billing account, so it cannot be charged.
# That is the project's ₹0 guarantee, and step 2 refuses to continue if billing is ever enabled.
#
#   1. a gcloud configuration scoped to this repo (the global default is never touched)
#   2. the project, and a check that it has NO billing account
#   3. the APIs we need
#   4. a service account for dbt/scripts with BigQuery Data Editor + Job User, and its JSON key (outside the repo)
#   5. the three datasets: raw_hris (loaded by our batch loader), dbt_dev, analytics
#
# Run via `make setup-gcp`, which loads .env. Prerequisite (opens a browser, so you run it):
#   CLOUDSDK_ACTIVE_CONFIG_NAME=people-analytics gcloud auth login <your personal account>
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${GCP_ACCOUNT:?set GCP_ACCOUNT in .env}"
: "${GCP_PROJECT_ID:?set GCP_PROJECT_ID in .env}"
: "${BQ_LOCATION:?set BQ_LOCATION in .env}"
: "${BQ_KEYFILE:?set BQ_KEYFILE in .env}"
: "${BQ_DATASET_RAW:?}" "${BQ_DATASET_DEV:?}" "${BQ_DATASET_PROD:?}"
: "${CLOUDSDK_ACTIVE_CONFIG_NAME:?set CLOUDSDK_ACTIVE_CONFIG_NAME in .env}"
SA_NAME="${SA_NAME:-dbt-bigquery}"

P="$GCP_PROJECT_ID"
CFG="$CLOUDSDK_ACTIVE_CONFIG_NAME"
SA_EMAIL="${SA_NAME}@${P}.iam.gserviceaccount.com"

export CLOUDSDK_ACTIVE_CONFIG_NAME="$CFG"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1
# gcloud falls back to the macOS system Python 3.9 here; point it at 3.12 if available.
if [[ -z "${CLOUDSDK_PYTHON:-}" ]] && command -v python3.12 >/dev/null; then
  export CLOUDSDK_PYTHON="$(command -v python3.12)"
fi

step() { printf '\n==> %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$CFG" != "default" ]] || die "CLOUDSDK_ACTIVE_CONFIG_NAME must not be 'default' (that is your global gcloud config)"
for v in CLOUDSDK_CORE_PROJECT CLOUDSDK_CORE_ACCOUNT; do
  [[ -z "${!v:-}" ]] || die "$v is set in your environment and would override the '$CFG' configuration; unset it"
done
[[ "$BQ_KEYFILE" == /* ]] || die "BQ_KEYFILE must be an absolute path (dbt can't expand ~ or relative paths): $BQ_KEYFILE"
case "$BQ_KEYFILE" in
  "$REPO_ROOT"/*) die "BQ_KEYFILE points inside the repo; keep keys outside it (e.g. ~/.config/people-analytics/)";;
esac

# --------------------------------------------------------------------------- 1
step "gcloud configuration '$CFG' (global default left untouched)"
if ! gcloud config configurations describe "$CFG" >/dev/null 2>&1; then
  gcloud config configurations create "$CFG" --no-activate
fi
gcloud config set account "$GCP_ACCOUNT" --quiet
gcloud config set project "$P" --quiet 2>/dev/null || true   # warns until the project exists; still sets it

if [[ -z "$(gcloud auth list --filter="account:$GCP_ACCOUNT" --format='value(account)')" ]]; then
  die "not logged in as $GCP_ACCOUNT. Run this in your terminal, then re-run make setup-gcp:
    CLOUDSDK_ACTIVE_CONFIG_NAME=$CFG gcloud auth login $GCP_ACCOUNT"
fi
active="$(gcloud config get account 2>/dev/null)"
[[ "$active" == "$GCP_ACCOUNT" ]] || die "configuration '$CFG' is using '$active', expected '$GCP_ACCOUNT'"
echo "Using account $active in configuration $CFG"

# --------------------------------------------------------------------------- 2
step "project $P"
if gcloud projects describe "$P" >/dev/null 2>&1; then
  echo "exists"
else
  if ! out="$(gcloud projects create "$P" --name="People Analytics Demo" 2>&1)"; then
    echo "$out" >&2
    grep -q "already in use" <<<"$out" \
      && die "project ID '$P' is taken globally (IDs are never reused). Set a unique GCP_PROJECT_ID in .env and re-run."
    die "could not create project '$P'"
  fi
fi
gcloud config set project "$P" --quiet

step "no-billing guard (sandbox = cannot be charged)"
billing="$(gcloud billing projects describe "$P" --format='value(billingEnabled)')"
if [[ "$billing" != "False" ]]; then
  die "billing is ENABLED on $P (billingEnabled=$billing). This project must stay in the free sandbox.
    Unlink it: gcloud billing projects unlink $P"
fi
echo "billing disabled: BigQuery sandbox, nothing can be charged"

# --------------------------------------------------------------------------- 3
step "enable APIs"
gcloud services enable bigquery.googleapis.com iam.googleapis.com --project="$P"

# --------------------------------------------------------------------------- 4
step "service account $SA_EMAIL"
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$P" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --project="$P" \
    --display-name="dbt + scripts (BigQuery)"
fi
for role in roles/bigquery.dataEditor roles/bigquery.jobUser; do
  # A new service account can take up to a minute to propagate; retry the binding.
  for attempt in 1 2 3 4 5 6 7 8; do
    if err="$(gcloud projects add-iam-policy-binding "$P" --member="serviceAccount:$SA_EMAIL" \
         --role="$role" --condition=None --format=none 2>&1)"; then
      echo "granted $role"; break
    fi
    [[ $attempt -lt 8 ]] || die "could not grant $role to $SA_EMAIL:
$err"
    sleep 10
  done
done

step "service-account key -> $BQ_KEYFILE"
if [[ -f "$BQ_KEYFILE" ]] && grep -q "\"client_email\": \"$SA_EMAIL\"" "$BQ_KEYFILE"; then
  key_id="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["private_key_id"])' "$BQ_KEYFILE")"
  if gcloud iam service-accounts keys list --iam-account="$SA_EMAIL" --project="$P" --managed-by=user \
       --format='value(name.basename())' | grep -qx "$key_id"; then
    echo "key already present and still active on the service account ($key_id)"
  else
    die "local key $key_id no longer exists on $SA_EMAIL. Move $BQ_KEYFILE aside and re-run to create a new one."
  fi
else
  mkdir -p "$(dirname "$BQ_KEYFILE")" && chmod 700 "$(dirname "$BQ_KEYFILE")"
  (umask 077; gcloud iam service-accounts keys create "$BQ_KEYFILE" --iam-account="$SA_EMAIL" --project="$P")
fi
echo "user-managed keys on $SA_EMAIL (the one in use is $(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["private_key_id"])' "$BQ_KEYFILE")):"
gcloud iam service-accounts keys list --iam-account="$SA_EMAIL" --project="$P" --managed-by=user \
  --format='table(name.basename(),validAfterTime)'

# --------------------------------------------------------------------------- 5
step "BigQuery datasets in $BQ_LOCATION"
for ds in "$BQ_DATASET_RAW" "$BQ_DATASET_DEV" "$BQ_DATASET_PROD"; do
  if bq --project_id="$P" show --format=none "$P:$ds" >/dev/null 2>&1; then
    echo "exists: $ds"
  else
    bq --project_id="$P" --location="$BQ_LOCATION" mk --dataset "$P:$ds"
  fi
done

step "done"
echo "Next: make check"
