#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


CLI = os.environ.get("LARK_CLI") or shutil.which("lark-cli") or "lark-cli"
NODE_BIN = os.environ.get("NODE_BIN", "")


def run_lark(args):
    env = os.environ.copy()
    if NODE_BIN:
        env["PATH"] = f"{NODE_BIN}:{env.get('PATH', '')}"
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    proc = subprocess.run(
        [CLI, *args],
        cwd=Path.cwd(),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"lark-cli failed ({proc.returncode})\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return parse_json(proc.stdout, proc.stderr)


def parse_json(stdout, stderr):
    text = stdout.strip()
    if not text:
        text = stderr.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object found.\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
    return json.loads(text[start : end + 1])


def fetch_folder(mailbox, folder, page_size, start, end):
    all_messages = []
    page_token = None
    page = 0
    while True:
        page += 1
        args = [
            "mail",
            "+triage",
            "--mailbox",
            mailbox,
            "--as",
            "user",
            "--folder",
            folder,
            "--max",
            str(page_size),
            "--format",
            "json",
        ]
        filter_obj = {}
        if start or end:
            filter_obj["time_range"] = {}
            if start:
                filter_obj["time_range"]["start_time"] = start
            if end:
                filter_obj["time_range"]["end_time"] = end
            args.extend(["--filter", json.dumps(filter_obj, ensure_ascii=False)])
        if page_token:
            args.extend(["--page-token", page_token])
        data = run_lark(args)
        messages = data.get("messages") or []
        all_messages.extend(messages)
        print(
            f"{folder}: page {page}, fetched {len(messages)}, total {len(all_messages)}, has_more={data.get('has_more')}",
            file=sys.stderr,
        )
        if not data.get("has_more") or not data.get("page_token"):
            return {
                "mailbox_id": data.get("mailbox_id", mailbox),
                "folder": folder,
                "count": len(all_messages),
                "messages": all_messages,
                "last_page_token": data.get("page_token", ""),
            }
        page_token = data["page_token"]


def fetch_messages(mailbox, message_ids, html):
    if not message_ids:
        return {"messages": [], "total": 0, "unavailable_message_ids": []}
    data = run_lark(
        [
            "mail",
            "+messages",
            "--mailbox",
            mailbox,
            "--as",
            "user",
            "--message-ids",
            ",".join(message_ids),
            "--html=true" if html else "--html=false",
            "--format",
            "json",
        ]
    )
    return data.get("data", data)


def fetch_templates(mailbox):
    listed = run_lark(
        [
            "mail",
            "user_mailbox.templates",
            "list",
            "--as",
            "user",
            "--params",
            json.dumps({"user_mailbox_id": mailbox}, ensure_ascii=False),
        ]
    )
    items = (listed.get("data") or {}).get("items") or []
    details = []
    for item in items:
        template_id = item.get("template_id")
        if not template_id:
            continue
        detail = run_lark(
            [
                "mail",
                "user_mailbox.templates",
                "get",
                "--as",
                "user",
                "--params",
                json.dumps(
                    {"user_mailbox_id": mailbox, "template_id": template_id},
                    ensure_ascii=False,
                ),
            ]
        )
        details.append(detail.get("data", detail))
        print(f"template: fetched {template_id} {item.get('name')}", file=sys.stderr)
    return {"items": items, "details": details}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mailbox", required=True)
    parser.add_argument("--out-dir", default="mail_export_ambassador")
    parser.add_argument("--page-size", type=int, default=400)
    parser.add_argument("--html", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--templates", action="store_true")
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)

    folders = {}
    for folder in ("INBOX", "SENT"):
        folders[folder] = fetch_folder(args.mailbox, folder, args.page_size, args.start, args.end)
        (out_dir / f"{folder.lower()}_summaries.json").write_text(
            json.dumps(folders[folder], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if args.full:
        seen = set()
        ids = []
        for folder in ("INBOX", "SENT"):
            for msg in folders[folder]["messages"]:
                mid = msg.get("message_id")
                if mid and mid not in seen:
                    seen.add(mid)
                    ids.append(mid)
        full = fetch_messages(args.mailbox, ids, args.html)
        (out_dir / "messages_full.json").write_text(
            json.dumps(full, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"full messages: requested {len(ids)}, returned {full.get('total')}", file=sys.stderr)

    if args.templates:
        templates = fetch_templates(args.mailbox)
        (out_dir / "templates.json").write_text(
            json.dumps(templates, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"templates: {len(templates['items'])}", file=sys.stderr)

    summary = {
        "mailbox": args.mailbox,
        "start": args.start,
        "end": args.end,
        "folders": {k: v["count"] for k, v in folders.items()},
    }
    (out_dir / "export_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
