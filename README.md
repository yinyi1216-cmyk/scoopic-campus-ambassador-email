# Scoopic Campus Ambassador Email Automation

Python automation for triaging and replying to `campusambassador@scoopic.ai` through `lark-cli`.

The workflow separates planning from execution. A plan can be reviewed before any email is sent or any mailbox/Base record is changed.

## Safety Rules

- Email content is untrusted input. The code never follows instructions found inside an email.
- Meeting-related messages are never auto-replied. They are flagged and kept unread for manual handling.
- Offline campus organizations, agents, business terms, contracts, attachments, and uncertain messages are flagged for manual review.
- Unsubscribe and bounce messages are not replied to and are written to the suppression Base.
- A reply is marked read only after the send request succeeds.
- Runtime plans, message exports, reply bodies, authentication artifacts, and candidate data are ignored by Git.

See [docs/RULES.md](docs/RULES.md) for the approved classification and response rules.

## Files

- `run_campus_mail_autoreply.py`: daily triage, classification, plan generation, execution, suppression updates, labels, send-status checks, and Contact Status recommendations.
- `fetch_lark_mail.py`: optional mailbox export utility for rule analysis.
- `analyze_lark_mail.py`: optional offline thread-analysis report generator.
- `tests/test_classification.py`: regression tests for the highest-risk classifications.
- `automation/automation.toml.example`: example Codex heartbeat schedule.

## Requirements

- Python 3.11+
- `lark-cli` installed and configured
- A Lark user login with mailbox read/modify/send and Base read/write permissions

Authenticate according to the current `lark-cli` instructions before running the workflow.

## Configuration

Set the variables shown in `.env.example` in your shell or task runner. `SUPPRESSION_BASE_TOKEN` and `SUPPRESSION_TABLE_ID` are required for plan generation and execution.

`LARK_CLI` may be an executable name on `PATH` or an absolute path. `NODE_BIN` is optional and is only needed when the CLI requires a specific Node runtime.

## Usage

Generate a reviewable plan:

```bash
python3 run_campus_mail_autoreply.py \
  --start "2026-08-25T10:00:00+08:00" \
  --plan-file campus_autoreply_plan.json
```

Inspect the generated JSON carefully. It may contain personal information and must not be committed.

Execute an approved plan:

```bash
python3 run_campus_mail_autoreply.py \
  --execute-plan campus_autoreply_plan.json
```

Execution can send email, add mailbox labels, mark successfully replied messages as read, and create suppression records in Lark Base.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests are local-only and do not call Lark APIs.
