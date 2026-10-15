"""Text-to-SQL evaluation: how many HR questions Claude answers correctly from the warehouse docs.

    python eval/run_eval.py --run bare            # context: eval/schema_bare.md (names and types only)
    python eval/run_eval.py --run documented      # eval/schema_context_original.md (the docs as first written)
    python eval/run_eval.py --run improved        # eval/schema_context.md (after fixing the docs behind misses)
    python eval/run_eval.py --run documented --only q01,q02 --jobs 4
    python eval/run_eval.py --run improved --questions eval/questions_holdout.yaml
    python eval/run_eval.py --run improved --context path/to/other_context.md
    python eval/run_eval.py --rescore eval/results/documented-<stamp>.jsonl
    python eval/run_eval.py --canary              # check that the model can't see the repo

Model calls (docs/decisions.md, D8). Each question goes to Claude through the Claude Code CLI on the
claude.ai subscription: one `claude -p` call per question with a pinned model, run from a fresh empty
temp directory outside the repo, with no tools, MCP servers, settings files, plugins, hooks or
CLAUDE.md, and a fixed system prompt. The prompt, sent on stdin, is eval/prompt.md, then the context
file, then the question. The CLI adds a short note of its own to every call (an Agent SDK identity
line, the current date, the temp directory, the platform, shell and OS version, the model name and the
account e-mail); it can't be turned off without API-key auth. That date is the machine's, so a "today"
question comes out right only if the model takes today from analytics.int_reporting_date, as the docs
say. The script refuses to run if ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN is set (the CLI would bill
the API per call) or if `claude auth status` isn't a claude.ai login. The CLI's environment is an
allowlist (home, user, path, shell, locale, temp, terminal, CLAUDE_CONFIG_DIR and proxy and TLS
settings), so the .env secrets and any API credentials never reach it.

Canary. Every run starts by asking the model, the same way, for the contents of a file holding a random
token in its working directory and for the first line of eval/questions.yaml. It passes if the model
used no tools (one turn) and the reply contains neither (the line is checked only if it's 20 characters
or more, too long to guess). A failed canary stops the run. The reply is saved with the repo's path
replaced by <repo>.

Execution. The reply's first ```sql block is dry-run on BigQuery and run only if BigQuery reports a
SELECT, with maximum_bytes_billed of 1 GiB. The question's gold_sql is run the same way at the same
time.

Comparison. G is the gold result, keeping only compare.columns (default: every gold column); R is the
generated result with all of its columns. The answer passes when each kept gold column can be given
its own column of R (column names and R's extra columns don't matter) such that the rows pair up one
to one, every gold row with a row of R whose assigned columns match its values:
- R has as many rows as G (more are allowed with compare.allow_extra_rows: true);
- row order is ignored unless compare.order_matters: then gold row i pairs with row i of R;
- NULL matches only NULL (a NaN counts as NULL);
- numbers (INT64, FLOAT64, NUMERIC) match numbers within compare.tolerance, an absolute difference
  (default 0, plus 1e-9 of float noise); with compare.rate a value may also be a percentage or a
  fraction of the gold one: r is checked against g, g * 100 and g / 100, the tolerance scaled alike;
- booleans match equal booleans; a number never matches a string or a boolean;
- strings match after trimming, ignoring case;
- dates and timestamps compare as ISO text (a timestamp at midnight UTC as its date), so a DATE
  matches the same date in a STRING column.
tolerance may also be a {column: tolerance} map, and rate a list of the columns it applies to.
Because extra columns are ignored, an answer that hedges (one reading in its answer column, another
in an extra one) can pass. The prompt asks for one answer, and the summary lists every pass with
extra columns so a reader can check which column was matched.

Statuses: pass; fail (it ran, the result is wrong); sql_error (not a SELECT, or the dry run or run
failed); not_answered (the reply has no SQL); harness_error (the CLI failed or timed out, BigQuery
couldn't be reached, or the gold query failed or returned no rows: not the model's doing, listed
apart, and the run exits 1). All but pass count as misses.

Review. eval/results/reviews.yaml records verdicts from reading the results by hand where they
differ from the harness's (e.g. a hedge that passed), keyed by results file name; the summary shows
the score as judged and after review.

Output: eval/results/<run>-<YYYYMMDD-HHMM>.jsonl (prefixed with the questions file's name when it
isn't eval/questions.yaml, e.g. holdout-improved-...), one row per question (the SQL, the raw reply,
the status, the first rows of both results, bytes billed, latency), and <same name>.summary.md with
the score overall and by difficulty and every miss. --rescore re-judges a results file's saved SQL
against the current questions file and today's data, without calling the model, and rewrites both
files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from datetime import time as clock_time
from decimal import Decimal
from itertools import product
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.auth.exceptions import TransportError
from google.cloud import bigquery

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from ingestion.load_to_bigquery import bigquery_client  # noqa: E402

EVAL = REPO / "eval"
RESULTS = EVAL / "results"
REVIEWS = RESULTS / "reviews.yaml"
QUESTIONS = EVAL / "questions.yaml"
PROMPT = EVAL / "prompt.md"
# documented is a frozen copy of schema_context.md as Phase 4 wrote it, so Run B stays reproducible
# after the docs are fixed; `make docs` regenerates schema_context.md and schema_bare.md.
CONTEXTS = {"bare": EVAL / "schema_bare.md",
            "documented": EVAL / "schema_context_original.md",
            "improved": EVAL / "schema_context.md"}
# The CLI's environment: enough to find its login and reach the API, nothing from .env.
KEEP_ENV = ("HOME", "USER", "LOGNAME", "PATH", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TERM",
            "CLAUDE_CONFIG_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy",
            "no_proxy", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE")

MODEL = "claude-sonnet-5-5"
EFFORT = "medium"
SYSTEM_PROMPT = "You write BigQuery SQL for an HR analytics team."
CALL_TIMEOUT = 180            # seconds per model call
CALL_ATTEMPTS = 3             # CLI errors are retried (transient API failures); timeouts are not
MAX_BYTES = 1024**3           # maximum_bytes_billed for every query
QUERY_TIMEOUT = 120           # seconds per query
MAX_ROWS = 10_000             # rows fetched per result
KEEP_ROWS = 10                # rows saved per result in the results file
MAX_MAPPINGS = 5_000          # column assignments tried before giving up
DIFFICULTIES = ("easy", "medium", "hard")
STATUSES = ("pass", "fail", "sql_error", "not_answered", "harness_error")


# ------------------------------------------------------------------ the model, through the CLI

def claude_env() -> dict[str, str]:
    """The CLI's environment: an allowlist, so no API credentials, .env secrets or parent session."""
    return {k: os.environ[k] for k in KEEP_ENV if k in os.environ}


def redact(text: str) -> str:
    """Text with the repo's absolute path replaced, so results don't carry the local directory."""
    return (text or "").replace(str(REPO), "<repo>")


def preflight() -> dict:
    """Refuse unless the CLI bills a claude.ai subscription; return the CLI, its env and version."""
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if var in os.environ:
            sys.exit(f"{var} is set: the CLI would bill the API per call. Unset it; "
                     "the eval runs on the claude.ai subscription.")
    cli = shutil.which("claude")
    if not cli:
        sys.exit("claude (the Claude Code CLI) isn't on PATH.")
    env = claude_env()
    out = subprocess.run([cli, "auth", "status"], env=env, capture_output=True, text=True, timeout=60).stdout
    try:
        auth = json.loads(out)
    except json.JSONDecodeError:
        sys.exit(f"`claude auth status` didn't print JSON: {out.strip()[:200]}")
    if not (auth.get("loggedIn") and auth.get("authMethod") == "claude.ai"
            and auth.get("apiProvider") == "firstParty"):
        sys.exit("Claude Code must be logged in with a claude.ai subscription (`claude auth login`); "
                 f"auth status: loggedIn={auth.get('loggedIn')}, authMethod={auth.get('authMethod')}, "
                 f"apiProvider={auth.get('apiProvider')}.")
    version = subprocess.run([cli, "--version"], env=env, capture_output=True, text=True, timeout=60)
    return {"cli": cli, "env": env, "cli_version": (version.stdout.split() or ["unknown"])[0],
            "auth": f"claude.ai ({auth.get('subscriptionType') or 'subscription'})"}


def ask_claude(cli: str, env: dict, prompt: str, files: dict[str, str] | None = None) -> dict:
    """One fresh, tool-less `claude -p` session in a new temp directory; the reply and its stats."""
    cmd = [cli, "-p", "--model", MODEL, "--effort", EFFORT, "--output-format", "json",
           "--no-session-persistence", "--strict-mcp-config", "--tools", "", "--setting-sources", "",
           "--safe-mode", "--disable-slash-commands", "--system-prompt", SYSTEM_PROMPT]
    reply = {"answered": False, "text": "", "error": "", "attempts": 0, "latency_s": None,
             "num_turns": None, "tool_denials": 0, "input_tokens": None, "output_tokens": None}
    for attempt in range(1, CALL_ATTEMPTS + 1):
        reply["attempts"] = attempt
        with tempfile.TemporaryDirectory(prefix="text-to-sql-") as cwd:
            if REPO in Path(cwd).resolve().parents:
                sys.exit(f"The temp directory {cwd} is inside the repo; set TMPDIR elsewhere.")
            for name, content in (files or {}).items():
                (Path(cwd) / name).write_text(content)
            start = time.monotonic()
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, text=True, start_new_session=True,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                out, err = proc.communicate(prompt, timeout=CALL_TIMEOUT)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
                reply.update(latency_s=CALL_TIMEOUT, error=f"no reply within {CALL_TIMEOUT} s")
                return reply
            reply["latency_s"] = round(time.monotonic() - start, 1)
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            reply["error"] = f"CLI exited {proc.returncode}: {(err or out).strip()[-300:]}"
        else:
            usage = data.get("usage") or {}
            reply.update(text=data.get("result") or "", num_turns=data.get("num_turns"),
                         tool_denials=len(data.get("permission_denials") or []),
                         input_tokens=sum(usage.get(k) or 0 for k in
                                          ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
                         output_tokens=usage.get("output_tokens"))
            models = sorted(data.get("modelUsage") or {})
            if data.get("is_error"):
                reply["error"] = (f"CLI error ({data.get('subtype')}, API status {data.get('api_error_status')}): "
                                  f"{reply['text'].strip()[:300]}")
            elif MODEL not in models:
                reply["error"] = f"the reply came from {models}, not {MODEL}"
                return reply
            else:
                reply.update(answered=True, error="")
                return reply
        if attempt < CALL_ATTEMPTS:
            time.sleep(15 * attempt)
    return reply


def canary(claude: dict) -> dict:
    """Ask for things only a session with file access could know; pass if it can't."""
    token = f"canary-{secrets.token_hex(8)}"
    reply = ask_claude(claude["cli"], claude["env"],
                       "Reply with the exact contents of the file canary.txt in your current working directory, "
                       f"and the first line of the file {QUESTIONS}. If you can't read them, say so; don't guess.",
                       files={"canary.txt": token + "\n"})
    hidden = [token]
    lines = QUESTIONS.read_text().strip().splitlines() if QUESTIONS.exists() else []
    if lines and len(lines[0].strip()) >= 20:          # a shorter line could be guessed
        hidden.append(lines[0].strip())
    leaked = [s for s in hidden if s in reply["text"]]
    passed = reply["answered"] and reply["num_turns"] == 1 and not reply["tool_denials"] and not leaked
    why = ("" if passed else reply["error"] or
           f"turns={reply['num_turns']}, tool denials={reply['tool_denials']}, leaked={len(leaked)} secret(s)")
    return {"passed": passed, "answer": redact(reply["text"].strip()), "error": redact(why)}


def extract_sql(text: str) -> str:
    """The first ```sql block of a reply (or an untagged block, or a reply that is only SQL)."""
    blocks, body, lang = [], None, ""
    for line in text.splitlines():
        fence = re.match(r"\s*```\s*(\w*)", line)
        if fence and body is None:
            body, lang = [], fence.group(1).lower()
        elif fence:
            blocks.append((lang, "\n".join(body)))
            body = None
        elif body is not None:
            body.append(line)
    if body is not None:                                  # an unclosed last block
        blocks.append((lang, "\n".join(body)))
    sql = ([b for lang, b in blocks if lang in ("sql", "googlesql", "bigquery")]
           or [b for lang, b in blocks if not lang] or [""])[0]
    if not blocks and re.match(r"\s*(select|with)\b", text, re.I):
        sql = text
    return sql.strip().rstrip(";").strip()


# ------------------------------------------------------------------ BigQuery

def with_retries(call, attempts: int = 3):
    """Retry a BigQuery call on DNS and connection errors."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except (requests.exceptions.ConnectionError, TransportError):
            if attempt == attempts:
                raise
            time.sleep(5 * attempt)


def run_query(client: bigquery.Client, sql: str) -> dict:
    """Dry-run, then run only a SELECT, capped at MAX_BYTES. Columns, rows, row count and bytes, or error."""
    try:
        dry = with_retries(lambda: client.query(sql, job_config=bigquery.QueryJobConfig(
            dry_run=True, use_query_cache=False)))
        if dry.statement_type != "SELECT":
            return {"error": f"not run: BigQuery reads it as {dry.statement_type}, and only a SELECT is run"}
        config = bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES, job_timeout_ms=QUERY_TIMEOUT * 1000)
        job = with_retries(lambda: client.query(sql, job_config=config))
        result = with_retries(lambda: job.result(timeout=QUERY_TIMEOUT + 30, max_results=MAX_ROWS))
        rows = [tuple(row.values()) for row in result]
        return {"columns": [field.name for field in result.schema], "rows": rows,
                "row_count": result.total_rows, "bytes_billed": job.total_bytes_billed or 0}
    except GoogleAPICallError as e:
        errors = getattr(e, "errors", None) or [{}]
        return {"error": errors[0].get("message") or e.message}
    except TimeoutError:
        return {"error": f"query ran longer than {QUERY_TIMEOUT} s"}
    except RetryError as e:        # the client's own retries ran out: a network outage, not the SQL
        return {"error": f"BigQuery unreachable: {e}", "unreachable": True}


def reporting_date(client: bigquery.Client) -> str:
    result = run_query(client, "select reporting_date from analytics.int_reporting_date")
    return result["rows"][0][0].isoformat() if result.get("rows") else f"unknown ({result.get('error')})"


# ------------------------------------------------------------------ comparison (rule in the docstring)

def normalize(value):
    """A comparable form of a BigQuery value: None, a bool, a float or trimmed lower-case text."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        return None if math.isnan(number) else number
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        value = value.date().isoformat() if value.time() == clock_time(0) else value.isoformat()
    elif isinstance(value, date):
        value = value.isoformat()
    return str(value).strip().casefold()


def scales(rate: bool) -> tuple[float, ...]:
    return (1.0, 100.0, 0.01) if rate else (1.0,)


def same(gold, got, tolerance: float, rate: bool) -> bool:
    """Whether two normalized values match."""
    if gold is None or got is None:
        return gold is None and got is None
    if isinstance(gold, bool) or isinstance(got, bool):
        return isinstance(gold, bool) and isinstance(got, bool) and gold == got
    if isinstance(gold, float) or isinstance(got, float):
        return isinstance(gold, float) and isinstance(got, float) and any(
            abs(got - gold * s) <= tolerance * s + 1e-9 * max(1.0, abs(gold * s)) for s in scales(rate))
    return gold == got


def kind(value) -> tuple:
    """A hashable key that keeps True apart from 1.0."""
    return (type(value).__name__, value)


def column_holds(gold_values: list, column: list, tolerance: float, rate: bool) -> bool:
    """Whether every gold value matches some value of one generated column."""
    exact = {kind(v) for v in column if not isinstance(v, float)}
    numbers = sorted(v for v in column if isinstance(v, float))
    for g in gold_values:
        if not isinstance(g, float):
            if kind(g) not in exact:
                return False
            continue
        for s in scales(rate):
            target, margin = g * s, tolerance * s + 1e-9 * max(1.0, abs(g * s))
            i = bisect_left(numbers, target - margin)
            if i < len(numbers) and numbers[i] <= target + margin:
                break
        else:
            return False
    return True


def pair_rows(gold: list, got: list, row_ok) -> bool:
    """Whether every gold row can be paired with a generated row of its own (bipartite matching)."""
    options = [[i for i, r in enumerate(got) if row_ok(g, r)] for g in gold]
    if not all(options):
        return False
    owner: dict[int, int] = {}

    def assign(gi: int, seen: set) -> bool:
        for ri in options[gi]:
            if ri not in seen:
                seen.add(ri)
                if ri not in owner or assign(owner[ri], seen):
                    owner[ri] = gi
                    return True
        return False

    return all(assign(gi, set()) for gi in range(len(gold)))


def compare(gold_columns: list[str], gold_rows: list, got_rows: list, spec: dict) -> tuple[bool, str]:
    """Judge a generated result against the gold one; (passed, why not)."""
    columns = spec.get("columns") or list(gold_columns)
    unknown = [c for c in columns if c not in gold_columns]
    if unknown:
        raise ValueError(f"compare.columns {unknown} aren't in the gold result {gold_columns}")
    tolerance, rate = spec.get("tolerance") or 0, spec.get("rate") or False
    checks = [(float(tolerance.get(c, 0) if isinstance(tolerance, dict) else tolerance),
               rate is True or (isinstance(rate, list) and c in rate)) for c in columns]
    keep = [gold_columns.index(c) for c in columns]
    raw = [[row[i] for i in keep] for row in gold_rows]        # for messages: 574, not 574.0
    gold = [[normalize(v) for v in row] for row in raw]
    got = [[normalize(v) for v in row] for row in got_rows]

    if len(got) < len(gold) or (len(got) > len(gold) and not spec.get("allow_extra_rows")):
        return False, f"expected {len(gold)} row(s), got {len(got)}"
    if not gold:
        return True, ""
    candidates = []
    for j, name in enumerate(columns):
        fits = [k for k in range(len(got[0]))
                if column_holds([g[j] for g in gold], [r[k] for r in got], *checks[j])]
        if not fits:
            example = json.dumps(raw[0][j], default=plain)
            return False, f"no generated column holds the values of {name} (e.g. {example})"
        candidates.append(fits)

    wrong_order, tried = False, 0
    for mapping in product(*candidates):
        if len(set(mapping)) < len(mapping):
            continue
        tried += 1
        if tried > MAX_MAPPINGS:
            return False, "too many ways to line up the columns; narrow compare.columns"

        def row_ok(g, r, mapping=mapping):
            return all(same(g[j], r[k], *checks[j]) for j, k in enumerate(mapping))

        if spec.get("order_matters"):
            if all(row_ok(g, r) for g, r in zip(gold, got)):
                return True, ""
            wrong_order = wrong_order or pair_rows(gold, got, row_ok)
        elif pair_rows(gold, got, row_ok):
            return True, ""
    if wrong_order:
        return False, "the right rows in the wrong order"
    return False, "each gold column has matching values, but the rows don't pair up"


# ------------------------------------------------------------------ one question

def plain(value):
    """JSON form of a BigQuery value."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime, clock_time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def judge(client: bigquery.Client, question: dict, sql: str) -> dict:
    """Run the gold and the generated SQL and compare them; the result fields of a row."""
    gold = run_query(client, question["gold_sql"])
    out = {"gold_columns": gold.get("columns"), "gold_rows": gold.get("rows", [])[:KEEP_ROWS],
           "gold_row_count": gold.get("row_count"), "gold_bytes_billed": gold.get("bytes_billed")}
    if "error" in gold:
        return {**out, "status": "harness_error", "error": f"gold SQL: {gold['error']}"}
    if not gold["rows"]:           # an empty gold result would pass any empty answer
        return {**out, "status": "harness_error", "error": "gold SQL returned no rows"}
    got = run_query(client, sql)
    out.update(generated_columns=got.get("columns"), generated_rows=got.get("rows", [])[:KEEP_ROWS],
               generated_row_count=got.get("row_count"), bytes_billed=got.get("bytes_billed"))
    if got.get("unreachable"):
        return {**out, "status": "harness_error", "error": f"generated SQL: {got['error']}"}
    if "error" in got:
        return {**out, "status": "sql_error", "error": got["error"]}
    try:
        passed, why = compare(gold["columns"], gold["rows"], got["rows"], question.get("compare") or {})
    except ValueError as e:
        return {**out, "status": "harness_error", "error": str(e)}
    return {**out, "status": "pass" if passed else "fail", "error": why}


def build_prompt(instructions: str, context: str, question: str) -> str:
    return (f"{instructions.strip()}\n\n<schema>\n{context.strip()}\n</schema>\n\n"
            f"<question>\n{question.strip()}\n</question>\n")


def answer(claude: dict, client: bigquery.Client, question: dict, run: dict) -> dict:
    """Ask the model one question and judge its SQL; one results row."""
    reply = ask_claude(claude["cli"], claude["env"],
                       build_prompt(run["instructions"], run["context"], question["question"]))
    sql = extract_sql(reply["text"]) if reply["answered"] else ""
    row = {"id": question["id"], "difficulty": question.get("difficulty"), "tests": question.get("tests"),
           "run": run["name"], "model": MODEL, "effort": EFFORT, "cli_version": claude["cli_version"],
           "auth": claude["auth"], "context_file": run["context_file"],
           "context_sha256": run["context_sha256"], "prompt_sha256": run["prompt_sha256"],
           "questions_sha256": run["questions_sha256"], "data_as_of": run["data_as_of"],
           "question": question["question"], "gold_sql": question["gold_sql"],
           "compare": question.get("compare") or {}, "generated_sql": sql, "raw_response": reply["text"],
           "latency_s": reply["latency_s"], "attempts": reply["attempts"], "num_turns": reply["num_turns"],
           "input_tokens": reply["input_tokens"], "output_tokens": reply["output_tokens"],
           "canary_passed": run["canary"]["passed"], "canary_answer": run["canary"]["answer"],
           "answered_at": datetime.now(UTC).isoformat(timespec="seconds")}
    if not reply["answered"]:      # a CLI error or timeout (e.g. a usage limit): ask again with --only
        return {**row, "status": "harness_error", "error": f"CLI: {reply['error']}"}
    if not sql:
        return {**row, "status": "not_answered", "error": "no SQL in the reply"}
    return {**row, **judge(client, question, sql)}


# ------------------------------------------------------------------ files and summary

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def load_questions(path: Path, only: str | None) -> list[dict]:
    if not path.exists():
        sys.exit(f"{path} not found")
    data = yaml.safe_load(path.read_text())
    questions = data["questions"] if isinstance(data, dict) else data
    for q in questions:
        missing = [k for k in ("id", "question", "gold_sql") if not q.get(k)]
        if missing:
            sys.exit(f"{path}: question {q.get('id', '?')} has no {', '.join(missing)}")
    ids = [q["id"] for q in questions]
    if len(set(ids)) < len(ids):
        sys.exit(f"{path}: duplicate question ids")
    if only:
        wanted = [i.strip() for i in only.split(",") if i.strip()]
        unknown = set(wanted) - set(ids)
        if unknown:
            sys.exit(f"--only: no such question(s): {', '.join(sorted(unknown))}")
        questions = [q for q in questions if q["id"] in wanted]
    if not questions:
        sys.exit(f"{path}: no questions")
    return questions


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, default=plain) + "\n" for row in rows))


def short(rows, n: int = 3) -> str:
    return json.dumps((rows or [])[:n], default=plain)


def clip(text: str, n: int) -> str:
    """Text on one line, cut at a word boundary with a marker if longer than n characters."""
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " ..."


def load_reviews(name: str) -> dict:
    """Verdicts from reading this results file by hand: {question id: {status, reason}}."""
    if not REVIEWS.exists():
        return {}
    return (yaml.safe_load(REVIEWS.read_text()) or {}).get(name) or {}


def extra_columns(row: dict) -> bool:
    """Whether a generated result has more columns than the comparison looks at."""
    compared = (row.get("compare") or {}).get("columns") or row.get("gold_columns") or []
    return len(row.get("generated_columns") or []) > len(compared)


def summary(name: str, rows: list[dict], reviews: dict | None = None) -> str:
    """The run's score overall and by difficulty, every miss, and the passes to check, as Markdown."""
    reviews = reviews or {}
    first = rows[0]
    passed = sum(r["status"] == "pass" for r in rows)
    reviewed = sum(reviews.get(r["id"], {}).get("status", r["status"]) == "pass" for r in rows)
    broken = [r["id"] for r in rows if r["status"] == "harness_error"]
    score = f"**Score: {passed}/{len(rows)} ({passed / len(rows):.0%})"
    if reviews:
        score += f" as judged; {reviewed}/{len(rows)} ({reviewed / len(rows):.0%}) after review"
    score += "**" + (f" Not a valid score: {len(broken)} harness error(s) ({', '.join(broken)}); "
                     "answer them again with --only." if broken else "")
    lines = [f"# Text-to-SQL eval: {name}", "",
             f"- Model: {first['model']} (effort {first['effort']}) through Claude Code {first['cli_version']}, "
             f"{first['auth']}",
             f"- Context: {first['context_file']} (sha256 {first['context_sha256'][:12]}); prompt sha256 "
             f"{first['prompt_sha256'][:12]}; questions sha256 {first['questions_sha256'][:12]}",
             f"- Data as of {first['data_as_of']}",
             f"- Isolation canary: {'passed' if first['canary_passed'] else 'FAILED'}. Its reply: "
             f"\"{clip(redact(first['canary_answer']), 300)}\"",
             "", score, "",
             "| difficulty | questions | " + " | ".join(STATUSES) + " |",
             "| --- | ---: | " + " | ".join("---:" for _ in STATUSES) + " |"]
    levels = list(DIFFICULTIES) + sorted({r["difficulty"] for r in rows} - set(DIFFICULTIES), key=str)
    for level in levels + ["all"]:
        group = [r for r in rows if level in ("all", r["difficulty"])]
        if group:
            counts = " | ".join(str(sum(r["status"] == s for r in group)) for s in STATUSES)
            label = "**all**" if level == "all" else level
            lines.append(f"| {label} | {len(group)} | {counts} |")
    if reviews:
        lines += ["", "## Changed by review", ""]
        lines += [f"- {qid}: {verdict.get('status')}. {clip(verdict.get('reason'), 400)}"
                  for qid, verdict in reviews.items()]
    misses = [r for r in rows if r["status"] != "pass"]
    lines += ["", "## Misses", ""] + ([] if misses else ["None."])
    for r in misses:
        lines += [f"### {r['id']} ({r['difficulty']}): {r['status']}", "", r["question"], "",
                  f"- Why: {clip(r['error'], 400)}"]
        if r.get("gold_columns"):
            lines.append(f"- Expected {r['gold_row_count']} row(s) {r['gold_columns']}: {short(r['gold_rows'])}")
        if r.get("generated_columns"):
            lines.append(f"- Got {r['generated_row_count']} row(s) {r['generated_columns']}: "
                         f"{short(r['generated_rows'])}")
        if r["generated_sql"]:
            lines += ["", "```sql", r["generated_sql"], "```"]
        lines.append("")
    extra = [r for r in rows if r["status"] == "pass" and extra_columns(r)]
    if extra:
        lines += ([""] if lines[-1] else []) + ["## Passes with extra columns", "",
                  "Extra columns are ignored, so check that the matched column is the answer and not an "
                  "alternative reading.", ""]
        lines += [f"- {r['id']}: expected {r['gold_columns']} {short(r['gold_rows'], 1)}; got "
                  f"{r['generated_columns']} {short(r['generated_rows'], 1)}" for r in extra]
    return "\n".join(lines).rstrip() + "\n"


def shown(path: Path) -> str:
    return str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)


def finish(path: Path, rows: list[dict]) -> int:
    write_rows(path, rows)
    text = summary(path.stem, rows, load_reviews(path.stem))
    path.with_suffix(".summary.md").write_text(text)
    print("\n" + text)
    print(f"Wrote {shown(path)} and {shown(path.with_suffix('.summary.md'))}")
    broken = [r["id"] for r in rows if r["status"] == "harness_error"]
    if broken:
        print(f"Harness errors on {', '.join(broken)}: fix the cause, then answer them again with "
              f"--only {','.join(broken)}.")
    return 1 if broken else 0


def in_parallel(work, items: list, jobs: int, label) -> list:
    """Apply work to items on `jobs` threads, printing each result as it lands; results in input order."""
    results = [None] * len(items)
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(work, item): i for i, item in enumerate(items)}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
            print(label(results[futures[future]]), flush=True)
    return results


def progress(row: dict) -> str:
    latency = f"{row['latency_s']:6.1f} s" if row.get("latency_s") is not None else ""
    why = " ".join((row.get("error") or "").split())[:90]
    return f"  {row['id']:<6} {str(row['difficulty']):<7} {row['status']:<13} {latency}  {why}"


# ------------------------------------------------------------------ commands

def run_eval(args) -> int:
    claude = preflight()
    questions_path = Path(args.questions).resolve()
    questions = load_questions(questions_path, args.only)
    context_path = Path(args.context).resolve() if args.context else CONTEXTS[args.run]
    if not context_path.exists():
        sys.exit(f"{context_path} not found")
    instructions, context = PROMPT.read_text(), context_path.read_text()
    client, _ = bigquery_client()

    print(f"Canary: can a {MODEL} session (Claude Code {claude['cli_version']}) see this machine's files?")
    check = canary(claude)
    print(f"  {'passed' if check['passed'] else 'FAILED'}: {clip(check['answer'], 200)}")
    if not check["passed"]:
        print(f"  {check['error']}\nStopping: the model session isn't isolated.")
        return 1

    run = {"name": args.run, "instructions": instructions, "context": context,
           "context_file": shown(context_path), "context_sha256": sha256(context),
           "prompt_sha256": sha256(SYSTEM_PROMPT + "\n\n" + instructions),
           "questions_sha256": sha256(questions_path.read_text()),
           "data_as_of": reporting_date(client), "canary": check}
    RESULTS.mkdir(exist_ok=True)
    # A results file from another questions file is named after it: questions_holdout.yaml -> holdout-
    prefix = "" if questions_path == QUESTIONS else questions_path.stem.removeprefix("questions_") + "-"
    path = RESULTS / f"{prefix}{args.run}-{datetime.now():%Y%m%d-%H%M}.jsonl"
    if path.exists():
        path = RESULTS / f"{prefix}{args.run}-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    print(f"Run {args.run}: {len(questions)} question(s), context {run['context_file']}, "
          f"data as of {run['data_as_of']}, {args.jobs} at a time")

    lock = threading.Lock()

    def work(question: dict) -> dict:
        row = answer(claude, client, question, run)
        with lock, path.open("a") as f:        # saved as it lands; rewritten in order at the end
            f.write(json.dumps(row, default=plain) + "\n")
        return row

    rows = in_parallel(work, questions, args.jobs, progress)
    return finish(path, rows)


def rescore(args) -> int:
    path = Path(args.rescore).resolve()
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    current = {q["id"]: q for q in load_questions(Path(args.questions), None)} if Path(args.questions).exists() else {}
    client, _ = bigquery_client()
    data_as_of = reporting_date(client)
    print(f"Rescoring {len(rows)} saved answer(s) in {path.name} against {args.questions}")

    def work(row: dict) -> dict:
        question = current.get(row["id"]) or {"gold_sql": row["gold_sql"], "compare": row.get("compare")}
        if row["id"] in current and question["question"].strip() != row["question"].strip():
            print(f"  {row['id']}: the question text has changed since this answer; judging it anyway")
        row.update(gold_sql=question["gold_sql"], compare=question.get("compare") or {},
                   data_as_of=data_as_of, rescored_at=datetime.now(UTC).isoformat(timespec="seconds"))
        if row["generated_sql"]:
            row.update(judge(client, question, row["generated_sql"]))
        return row

    rows = in_parallel(work, rows, args.jobs, progress)
    return finish(path, rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", choices=sorted(CONTEXTS), help="ask every question with this run's context")
    mode.add_argument("--rescore", metavar="JSONL", help="re-judge a results file's saved SQL (no model calls)")
    mode.add_argument("--canary", action="store_true", help="only check that the model session is isolated")
    ap.add_argument("--questions", default=str(QUESTIONS), help="questions file (default: eval/questions.yaml)")
    ap.add_argument("--context", help="context file to send instead of the run's own (recorded in each row)")
    ap.add_argument("--only", help="comma-separated question ids, e.g. q01,q02")
    ap.add_argument("--jobs", type=int, default=4, help="questions in flight at once (default 4)")
    args = ap.parse_args()
    load_dotenv(REPO / ".env")

    if args.canary:
        check = canary(preflight())
        print(f"Canary {'passed' if check['passed'] else 'FAILED'}. The model's reply:\n{check['answer']}")
        if check["error"]:
            print(check["error"])
        return 0 if check["passed"] else 1
    if args.rescore:
        return rescore(args)
    return run_eval(args)


if __name__ == "__main__":
    sys.exit(main())
