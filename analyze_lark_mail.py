#!/usr/bin/env python3
import json
import re
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path


DEFAULT_MAILBOX = "campusambassador@scoopic.ai"
DEFAULT_BASE = Path("mail_export_campus")


def load_messages(base):
    return json.loads((base / "messages_full.json").read_text(encoding="utf-8"))["messages"]


def clean_body(text):
    text = text or ""
    markers = [
        "On ",
        "From:",
        "发件人:",
        "-----Original Message-----",
    ]
    cut = len(text)
    for marker in markers:
        idx = text.find(marker)
        if idx > 80:
            cut = min(cut, idx)
    text = text[:cut]
    text = re.sub(r"\s+", " ", text).strip()
    return text


def direction(msg):
    return "sent" if msg.get("message_state_text") == "sent" else "received"


def epoch_to_local(ts):
    if not ts:
        return ""
    n = int(ts)
    if n > 10_000_000_000:
        n = n // 1000
    dt = datetime.fromtimestamp(n, tz=timezone(timedelta(hours=8)))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mailbox", default=DEFAULT_MAILBOX)
    parser.add_argument("--base-dir", default=str(DEFAULT_BASE))
    parser.add_argument("--title", default="Ambassador Mail Thread Analysis")
    args = parser.parse_args()

    base = Path(args.base_dir)
    messages = load_messages(base)
    threads = defaultdict(list)
    for m in messages:
        threads[m["thread_id"]].append(m)
    for items in threads.values():
        items.sort(key=lambda x: int(x.get("internal_date") or 0))

    lines = []
    lines.append(f"# {args.title}\n")
    lines.append(f"- mailbox: {args.mailbox}")
    lines.append(f"- messages: {len(messages)}")
    lines.append(f"- threads: {len(threads)}")
    lines.append("")

    subject_counter = Counter(m.get("subject", "") for m in messages)
    lines.append("## Subjects")
    for subject, count in subject_counter.most_common():
        lines.append(f"- {count} | {subject}")
    lines.append("")

    lines.append("## Threads")
    for tid, items in sorted(
        threads.items(), key=lambda kv: int(kv[1][-1].get("internal_date") or 0), reverse=True
    ):
        dirs = Counter(direction(m) for m in items)
        last = items[-1]
        lines.append(f"### {last.get('date_formatted')} | {last.get('subject')}")
        lines.append(f"- thread_id: {tid}")
        lines.append(f"- counts: sent={dirs['sent']}, received={dirs['received']}")
        lines.append(f"- labels(last): {','.join(last.get('label_ids') or [])}")
        for m in items:
            who = m.get("head_from", {}).get("mail_address", "")
            date = m.get("date_formatted", "")
            body = clean_body(m.get("body_plain_text", ""))
            lines.append(f"- {date} [{direction(m)}] {who} | {m.get('subject')}")
            lines.append(f"  {body[:700]}")
        lines.append("")

    templates_path = base / "templates.json"
    if templates_path.exists():
        templates = json.loads(templates_path.read_text(encoding="utf-8"))
        lines.append("## Templates")
        for raw in templates["details"]:
            tpl = raw.get("template", raw)
            content = re.sub(r"<[^>]+>", " ", tpl.get("template_content") or "")
            content = re.sub(r"\s+", " ", content).strip()
            lines.append(
                f"### {tpl.get('template_id')} | {tpl.get('name')} | created {epoch_to_local(tpl.get('create_time'))}"
            )
            lines.append(f"- subject: {tpl.get('subject')}")
            lines.append(f"- content: {content[:1000]}")
            lines.append("")

    output_path = base / "thread_analysis.md"
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
