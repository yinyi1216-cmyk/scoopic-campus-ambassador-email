#!/usr/bin/env python3
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path


CLI = os.environ.get("LARK_CLI") or shutil.which("lark-cli") or "lark-cli"
NODE_BIN = os.environ.get("NODE_BIN", "")
MAILBOX = os.environ.get("CAMPUS_MAILBOX", "campusambassador@scoopic.ai")
BASE_TOKEN = os.environ.get("SUPPRESSION_BASE_TOKEN", "")
TABLE_ID = os.environ.get("SUPPRESSION_TABLE_ID", "")
TZ = timezone(timedelta(hours=8))
MANUAL_MEETING_TEMPLATES = {"meeting_confirm", "schedule_call", "reschedule"}


def run_lark(args, *, check=True):
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
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"lark-cli failed ({proc.returncode})\nARGS: {' '.join(args)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc


def parse_json(stdout, stderr=""):
    text = (stdout or "").strip() or (stderr or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object found.\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
    return json.loads(text[start : end + 1])


def lark_json(args):
    proc = run_lark(args)
    return parse_json(proc.stdout, proc.stderr)


def fetch_folder(folder, start, page_size=400):
    messages = []
    page_token = None
    page = 0
    while True:
        page += 1
        args = [
            "mail",
            "+triage",
            "--as",
            "user",
            "--mailbox",
            MAILBOX,
            "--folder",
            folder,
            "--max",
            str(page_size),
            "--labels",
            "--format",
            "json",
        ]
        if start:
            args.extend(
                [
                    "--filter",
                    json.dumps(
                        {"time_range": {"start_time": start}},
                        ensure_ascii=False,
                    ),
                ]
            )
        if page_token:
            args.extend(["--page-token", page_token])
        data = lark_json(args)
        batch = data.get("messages") or []
        messages.extend(batch)
        print(
            f"{folder}: page {page}, fetched {len(batch)}, total {len(messages)}, has_more={data.get('has_more')}",
            file=sys.stderr,
        )
        if not data.get("has_more") or not data.get("page_token"):
            return messages
        page_token = data["page_token"]


def fetch_messages(message_ids, batch_size=50):
    if not message_ids:
        return []
    messages = []
    for start in range(0, len(message_ids), batch_size):
        batch_ids = message_ids[start : start + batch_size]
        data = lark_json(
            [
                "mail",
                "+messages",
                "--as",
                "user",
                "--mailbox",
                MAILBOX,
                "--message-ids",
                ",".join(batch_ids),
                "--html=false",
                "--format",
                "json",
            ]
        )
        payload = data.get("data", data)
        messages.extend(payload.get("messages") or [])
        print(
            f"MESSAGES: fetched {len(messages)} of {len(message_ids)}",
            file=sys.stderr,
        )
    return messages


def fetch_suppression_emails():
    require_suppression_config()
    emails = set()
    offset = 0
    while True:
        data = lark_json(
            [
                "base",
                "+record-list",
                "--as",
                "user",
                "--base-token",
                BASE_TOKEN,
                "--table-id",
                TABLE_ID,
                "--field-id",
                "Email",
                "--limit",
                "200",
                "--offset",
                str(offset),
                "--format",
                "json",
            ]
        )
        payload = data.get("data", {})
        fields = payload.get("fields") or []
        rows = payload.get("data") or []
        try:
            email_index = fields.index("Email")
        except ValueError:
            email_index = 0
        for row in rows:
            if len(row) > email_index and row[email_index]:
                emails.add(str(row[email_index]).strip().lower())
        if not payload.get("has_more") or not rows:
            return emails
        offset += len(rows)


def ts(msg):
    raw = int(msg.get("internal_date") or 0)
    if raw:
        return raw // 1000 if raw > 10_000_000_000 else raw
    date_text = msg.get("date")
    if date_text:
        try:
            return datetime.fromisoformat(date_text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return 0


def local_dt(msg):
    if msg.get("date_formatted"):
        text = msg["date_formatted"]
        if len(text) == 16:
            return text + ":00"
        return text
    stamp = ts(msg)
    if not stamp:
        return ""
    return datetime.fromtimestamp(stamp, TZ).strftime("%Y-%m-%d %H:%M:%S")


def direction(msg):
    return "sent" if msg.get("message_state_text") == "sent" else "received"


def top_body(text):
    text = (text or "").replace("\r", "\n")
    patterns = [
        r"\bOn\s+(Mon|Tue|Wed|Thu|Fri|Sat|Sun),.*?wrote:",
        r"\bOn\s+[A-Z][a-z]{2,9}\s+\d{1,2},\s+\d{4},.*?wrote:",
        r"(?m)^\s*On\s+.+?campusambassador@scoopic\.ai\s+wrote:",
        r"(?m)^\s*On\s+.+?<campusambassador@scoopic\.ai>\s*wrote:",
        r"(?m)^\s*On\s+.+?campusambassador\s*<campusambassador@scoopic\.ai>",
        r"\bSent from myMail for iOS\s+.*?\bfrom\s+campusambassador@scoopic\.ai\b",
        r"(?m)^\s*campusambassador\s*<campusambassador@scoopic\.ai>",
        r"(?m)^\s*<campusambassador@scoopic\.ai>",
        r"\bGet Outlook for iOS\s+From:",
        r"\bFrom:\s+campusambassador@scoopic\.ai\b",
        r"\bFrom:\s+campusambassador\s*<campusambassador@scoopic\.ai>",
        r"\bFrom:\s+(?:scoopic|campusambassador)\s*<campusambassador@scoopic\.ai>",
        r"(?m)^\s*发件人:",
        r"(?m)^\s*From:",
        r"(?m)^\s*-----Original Message-----",
    ]
    cut = len(text)
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.S)
        if match:
            cut = min(cut, match.start())
    return re.sub(r"\s+", " ", text[:cut]).strip()


def full_clean(text):
    return re.sub(r"\s+", " ", (text or "").replace("\r", "\n")).strip()


def sender_email(msg):
    return ((msg.get("head_from") or {}).get("mail_address") or "").strip().lower()


def sender_name(msg):
    return ((msg.get("head_from") or {}).get("name") or "").strip()


def first_name(msg):
    name = sender_name(msg)
    if name:
        token = re.split(r"\s+", name.strip())[0]
        token = re.sub(r"[^A-Za-z'\-]", "", token)
        if token and token.lower() not in {"team", "support", "info", "collabs", "hello"}:
            return token[:1].upper() + token[1:]
    return "there"


def extract_bounce_email(body):
    match = re.search(
        r"收件人\s+([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})",
        body,
        re.I,
    )
    if match:
        return match.group(1).lower()
    for pattern in [
        r"Final-Recipient:.*?;\s*([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})",
        r"Original-Recipient:.*?;\s*([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})",
    ]:
        match = re.search(pattern, body, re.I)
        if match:
            return match.group(1).lower()
    found = [
        item.lower()
        for item in re.findall(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", body, re.I)
    ]
    found = [item for item in found if item != MAILBOX and "mailer-daemon" not in item]
    return found[0] if found else ""


def bounce_reason(body):
    lower = body.lower()
    if "收件地址不存在" in body or "user does not exist" in lower or "account that you tried to reach does not exist" in lower:
        return "bounce_address_invalid"
    if "收件方域名解析异常" in body or ("domain" in lower and "does not exist" in lower):
        return "bounce_domain_error"
    if "邮箱空间已满" in body or "over quota" in lower or "mailbox full" in lower:
        return "bounce_mailbox_full"
    if "发信频率超过限制" in body or "rate limited" in lower:
        return "bounce_rate_limited"
    if "邮件被拒收" in body or "message rejected" in lower or "rejected" in lower:
        return "bounce_rejected"
    return "bounce_rejected"


def is_offline_campus_inquiry(subject, sender, top):
    subject_text = (subject or "").lower()
    identity_text = f"{sender} {top}".lower()

    outreach_subject = bool(
        re.search(r"\bopportunities for\b", subject_text)
        and re.search(
            r"\b(university|college|school|organization|chapter|fraternity|sorority|panhellenic)\b",
            subject_text,
        )
    )
    campus_identity = bool(
        re.search(r"\b(university|college|campus|class of ['’]?\d{2})\b", identity_text)
        and re.search(
            r"\b(chapter president|chapter member|fraternity|sorority|panhellenic|interfraternity|greek life|recruitment chair|vice president of recruitment|social chair|philanthropy chair|executive board)\b",
            identity_text,
        )
    )
    return outreach_subject or campus_identity


def is_meeting_related(text):
    patterns = [
        r"\b(interview|calendly|reschedul(?:e|ed|ing)|appointment|time ?zone)\b",
        r"\b(schedule|book|set up|hop on|join|missed|confirm(?:ed|ing)?)\b.{0,60}\b(call|meeting|interview|time|slot|appointment)\b",
        r"\b(call|meeting|interview|appointment)\b.{0,80}\b(schedule|book|join|confirm|time|slot|availability|available)\b",
        r"\b(any|open|today.?s?)\s+(?:available\s+)?slots?\b|\bwhat times? (?:work|are available)\b",
        r"\b(confirm(?:ed|ing)?|booked|scheduled)\b",
        r"\bsee you (?:then|tomorrow|soon)\b|\btalk to you then\b|\b(?:speak|talk) soon\b|looking forward to (?:speaking|talking|meeting)",
        r"when do you want to.{0,40}(call|meet|discuss)|would love to (chat|connect|talk)|let me know.{0,40}(times?|availability)",
        r"no one else showed|no one joined",
    ]
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def classify(msg):
    subject = msg.get("subject") or ""
    sender = sender_email(msg)
    top = top_body(msg.get("body_plain_text") or "")
    full = full_clean(msg.get("body_plain_text") or "")
    text = f"{subject} {sender} {top}".lower()
    full_text = f"{subject} {sender} {full}".lower()
    # Only inspect the sender's newest text for business/legal terms. Quoted
    # outreach and prior replies contain words like rate, budget, and agreement.
    business_scan = text
    attachments = msg.get("attachments") or []

    if (
        "邮件退信" in subject
        or "mailer-daemon" in sender
        or sender.startswith("postmaster@")
        or "delivery status notification" in subject.lower()
        or subject.lower().startswith("undeliverable:")
    ):
        email = extract_bounce_email(full)
        return {
            "category": "退信/投递失败",
            "action": "suppress",
            "reason": bounce_reason(full),
            "suppression_email": email,
            "notes": full[:500],
        }

    if sender.endswith("@scoopic.ai"):
        return {"category": "内部邮件", "action": "skip", "reason": "internal_sender"}

    if "brevo" in full_text or "api key expired" in full_text:
        return {"category": "系统告警", "action": "flag", "reason": "system_alert"}

    if "+canned.response@" in sender or re.search(r"out of office|automatic response|automated response|closed for (the )?summer|inquiry received|support team|overwhelming volume.{0,160}(can not|cannot|can.?t) respond|thanks so much for your message.{0,160}will get back to you soon|will get back to you soon about potential collaborations?", text, re.I):
        action = "flag" if re.search(r"please reach out|while i.?m away|contact\s+[A-Z0-9._%+\-]+@", text, re.I) else "no_reply"
        return {"category": "自动回复/OOO", "action": action, "reason": "auto_response"}

    if re.search(r"thank you,?\s+looking forward to it|thanks,?\s+looking forward to it", text, re.I):
        return {"category": "无需回复", "action": "no_reply", "reason": "simple_thank_you"}

    if re.search(r"\bbest of luck\b", text, re.I):
        return {"category": "无需回复", "action": "no_reply", "reason": "polite_closing"}

    if subject.strip().lower() == "unsubscribe" or re.search(r"\bunsubscribe\b|remove me|take me out|stop contacting|opt out", text, re.I):
        return {
            "category": "退订/停止联系",
            "action": "suppress",
            "reason": "unsubscribe",
            "suppression_email": sender,
            "notes": (top or subject)[:500],
        }

    if is_offline_campus_inquiry(subject, sender, top):
        return {
            "category": "线下校园大使/校园组织",
            "action": "flag",
            "reason": "offline_campus_ambassador_manual",
        }

    if is_meeting_related(text):
        return {
            "category": "约面谈/面试相关（人工）",
            "action": "flag",
            "reason": "meeting_manual_review",
        }

    if re.search(
        r"no thank|not interested|no longer interested|not the right fit|not.*best fit|going to pass|have to pass|have to(?:\s+\w+){0,2}\s+decline|will have to(?:\s+\w+){0,2}\s+decline|decided not to move forward|not moving forward|not be moving forward|don.?t think.*right fit|don.?t think i.?ll|do not participate|unfortunately.*decline|not available|don.?t partner with ai|not from the us|no longer in school|not a student|would like to withdraw|\bwithdraw\b",
        text,
        re.I,
    ):
        return {"category": "明确拒绝/不合适", "action": "reply", "template": "decline"}

    business_patterns = [
        r"\b(my|her|his|their|our)\s+rate\b",
        r"\brates?\s+(start|starts|is|are|would be|for)\b",
        r"\bcharge\s+(a\s+)?bas(e|ed)\s+of\b",
        r"\bcharge\b.{0,80}\$\d+",
        r"\$\d[\d,]*(\.\d+)?\s*(per|/)\s*(video|post|deliverable|month)\b",
        r"\b(doable|works for you|budget.*support)\b",
        r"\brate card\b",
        r"\bmedia kit\b",
        r"\bbusiness manager\b",
        r"\btalent manager\b",
        r"\bmanagement\b",
        r"\bagency\b",
        r"\bflat[- ]fee\b",
        r"\bone[- ]?off\b",
        r"\bone[- ]?time\b",
        r"\bshort[- ]term partnership\b",
        r"\bother opportunities\b",
        r"\bfuture opportunities\b",
        r"\bbrand campaigns?\b",
        r"\bsingle[- ]?time\b",
        r"\btraditional ugc\b",
        r"\bpaid ads\b",
        r"\bretainer\b",
        r"\busage rights?\b",
        r"\bpromotion duration\b",
        r"\binvoice\b",
        r"\bownership terms?\b",
        r"\bllc\b",
        r"\bsignatory\b",
    ]
    if any(re.search(pattern, business_scan, re.I) for pattern in business_patterns):
        return {"category": "经纪/报价/商务条件", "action": "flag", "reason": "business_terms"}

    if re.search(r"\b(contract|agreement|payment terms|usage duration)\b", business_scan, re.I):
        return {"category": "合同/付款特殊条款", "action": "flag", "reason": "contract_terms"}

    if "[paid] try scoopic before launch" in subject.lower() and re.search(r"\brate|collab|details|paid|campaign", text, re.I):
        return {"category": "Beta/collab 专用流程", "action": "flag", "reason": "beta_campaign_question"}

    if re.search(
        r"(link|website|site).{0,100}(doesn.?t|did not|didn.?t|not work|not load|unable to view|nothing|not legit|legit|can.?t open|cannot open|can you resend|resend|working link)|unable to view.{0,80}(link|website|site)|nothing when i click|please let me know the details|please send.{0,80}details",
        text,
        re.I,
    ):
        return {"category": "简单感兴趣/想了解更多", "action": "reply", "template": "more_info"}

    if attachments:
        return {"category": "含附件需人工", "action": "flag", "reason": "has_attachment"}

    if re.search(r"high school|senior year in high school|my son|my daughter|\bunderage\b|\b1[0-7][ -]?year[ -]?old\b|\bteen creator\b|i am (her|his|their) parent", text, re.I):
        return {"category": "其他需确认", "action": "flag", "reason": "family_member_or_non_college_applicant"}

    if re.search(r"completed the form|(?:i|we|i.?ve|we.?ve)\s+(?:just\s+)?submitted\s+(?:my|our|the)\s+(?:application|form)|submitted my application|just applied|i applied|we applied|applied already|(?:i|we|i.?ve|we.?ve)\s+(?:just\s+)?filled out|filled this out", text, re.I):
        return {"category": "已提交申请/填表", "action": "reply", "template": "applied"}

    faq_patterns = [
        r"how many",
        r"payment",
        r"\bpay\b",
        r"base pay",
        r"bonus",
        r"requirements?",
        r"platforms?",
        r"tiktok",
        r"instagram",
        r"personal account",
        r"scoopic.*account",
        r"account.*scoopic",
        r"brand.?s",
        r"my page",
        r"creative freedom",
        r"template",
        r"per month",
        r"per video",
        r"deliverables?",
        r"posting",
        r"views",
    ]
    if any(re.search(pattern, text, re.I) for pattern in faq_patterns):
        return {"category": "标准项目问题", "action": "reply", "template": "faq"}

    if re.search(r"interested|love to learn more|would love|learn more|tell me more|tell me a little more|more information|more about what scoopic is|what scoopic is|hear more|partnership would look like|i am interested|i.?m interested|yes,? i|sounds perfect|will apply|will be applying|planning to apply|please let me know the details", text, re.I):
        return {"category": "简单感兴趣/想了解更多", "action": "reply", "template": "more_info"}

    return {"category": "其他需确认", "action": "flag", "reason": "unclassified"}


def html_paragraphs(lines):
    return "\n".join(f"<p>{line}</p>" if line else "<p><br></p>" for line in lines)


def reply_body(template, name, msg):
    if template == "more_info":
        return html_paragraphs(
            [
                f"Hi {html.escape(name)},",
                "",
                "Thank you so much for your interest! We’re really excited to share more with you.",
                "",
                "Scoopic is a social app where your personal paparazzi puppy turns camera roll moments into playful stories, inside jokes, and fun little narratives to share with close friends. It’s built around real friend groups, everyday moments, and content that feels natural instead of overly polished.",
                "",
                "For our paid Creator Ambassador Program, you would create short-form Scoopic content, try the app with friends, and share honest feedback to help us improve the product. We’ll provide examples, content directions, and support, but we also want your videos to feel authentic to your own style.",
                "",
                "The program offers $500/month in base pay, plus performance bonuses of up to $2,000 per video based on view milestones.",
                "",
                'You can find the full details and apply here:<br><a href="https://scoopic-ambassador-site.scoopic-ai.workers.dev/" style="color:rgb(20,86,240);text-decoration:none">https://scoopic-ambassador-site.scoopic-ai.workers.dev/</a>',
                "",
                "We’d love to have you apply, and happy to answer any questions you have!",
                "",
                "Best Regards,<br>Zoe Lin<br>Scoopic Team",
            ]
        )
    if template == "faq":
        return html_paragraphs(
            [
                f"Hi {html.escape(name)},",
                "",
                "Thanks so much for reaching out. Happy to clarify.",
                "",
                "For the Creator Ambassador Program, creators usually make one short-form Scoopic video per day, around 10–20 seconds. The same video can be posted to both TikTok and Instagram.",
                "",
                "We usually set up new Scoopic-focused TikTok and Instagram accounts with a new Gmail, so your personal accounts stay separate. We’ll provide examples, hooks, creative directions, and guidance, but we still want the videos to feel authentic to your own style.",
                "",
                "The compensation structure is $500/month in base pay for completing the agreed deliverables and basic guidelines, plus performance bonuses based on eligible video views, up to $2,000 per video. Final details are confirmed during onboarding and in the official agreement.",
                "",
                'You can find the full details and apply here:<br><a href="https://scoopic-ambassador-site.scoopic-ai.workers.dev/" style="color:rgb(20,86,240);text-decoration:none">https://scoopic-ambassador-site.scoopic-ai.workers.dev/</a>',
                "",
                "Best Regards,<br>Zoe Lin<br>Scoopic Team",
            ]
        )
    if template == "applied":
        return html_paragraphs(
            [
                f"Hi {html.escape(name)},",
                "",
                "Thank you so much for completing the form! We really appreciate your interest in Scoopic.",
                "",
                "We’ll review your submission and follow up with the next steps as soon as possible.",
                "",
                "Best Regards,<br>Zoe Lin<br>Scoopic Team",
            ]
        )
    if template == "meeting_confirm":
        return html_paragraphs(
            [
                f"Hi {html.escape(name)},",
                "",
                "Thanks for confirming. Looking forward to speaking with you soon!",
                "",
                "Best,<br>Zoe",
            ]
        )
    if template == "schedule_call":
        return html_paragraphs(
            [
                f"Hi {html.escape(name)},",
                "",
                "Thank you so much for your interest. We’d be happy to connect and discuss the opportunity with you.",
                "",
                'You can choose the time that works best for you here: <a href="https://calendly.com/d/d2g8-54t-9qd/scoopic-ambassador-interview" style="color:rgb(20,86,240);text-decoration:none">https://calendly.com/d/d2g8-54t-9qd/scoopic-ambassador-interview</a>',
                "",
                "Looking forward to speaking with you!",
                "",
                "Best,<br>Zoe",
            ]
        )
    if template == "reschedule":
        top = top_body(msg.get("body_plain_text") or "").lower()
        opener = "No worries at all, and thank you for letting us know."
        if "no one joined" in top or "no one else showed" in top:
            opener = "I’m so sorry about the meeting issue, and thank you for letting us know."
        elif re.search(r"no available appointments?|calend(?:ly|ar|er).{0,80}(not work|isn.?t work|won.?t let me select|can.?t select|cannot select|no available)", top, re.I):
            opener = "Thanks so much for flagging this, and sorry for the booking trouble."
        return html_paragraphs(
            [
                f"Hi {html.escape(name)},",
                "",
                f"{opener} You’re very welcome to choose another time here: <a href=\"https://calendly.com/d/d2g8-54t-9qd/scoopic-ambassador-interview\" style=\"color:rgb(20,86,240);text-decoration:none\">https://calendly.com/d/d2g8-54t-9qd/scoopic-ambassador-interview</a>",
                "",
                "The times shown on the booking page should appear in your local time zone, so you can select whichever option works best for you.",
                "",
                "Looking forward to connecting!",
                "",
                "Best,<br>Zoe",
            ]
        )
    if template == "decline":
        return html_paragraphs(
            [
                f"Hi {html.escape(name)},",
                "",
                "Thank you so much for letting us know. We completely understand, and we really appreciate you taking the time to consider Scoopic.",
                "",
                "Wishing you all the best, and hopefully there will be a chance to connect again in the future.",
                "",
                "Best Regards,<br>Zoe Lin<br>Scoopic Team",
            ]
        )
    raise ValueError(f"Unknown template: {template}")


def build_plan(start):
    inbox = fetch_folder("INBOX", start)
    latest_inbox_by_thread = {}
    for item in inbox:
        thread_id = item.get("thread_id")
        if not thread_id:
            continue
        current = latest_inbox_by_thread.get(thread_id)
        if current is None or ts(item) > ts(current):
            latest_inbox_by_thread[thread_id] = item

    candidates = []
    for item in latest_inbox_by_thread.values():
        labels = {
            label.strip().upper()
            for label in str(item.get("labels") or "").split(",")
            if label.strip()
        }
        if "REPLIED" not in labels:
            candidates.append(item)

    ids = [item["message_id"] for item in candidates if item.get("message_id")]
    messages = fetch_messages(ids)
    suppression = fetch_suppression_emails()

    threads = defaultdict(list)
    for msg in messages:
        if msg.get("thread_id"):
            threads[msg["thread_id"]].append(msg)
    for items in threads.values():
        items.sort(key=ts)

    actions = []
    for thread_id, items in sorted(threads.items(), key=lambda kv: ts(kv[1][-1]), reverse=True):
        last = items[-1]
        if direction(last) != "received":
            continue
        sender = sender_email(last)
        result = classify(last)
        if sender in suppression and result.get("action") == "reply":
            result = {"category": "已在抑制名单", "action": "skip", "reason": "suppressed_sender"}
        action = {
            "thread_id": thread_id,
            "message_id": last.get("message_id"),
            "from": sender,
            "from_name": sender_name(last),
            "subject": last.get("subject") or "",
            "date": local_dt(last),
            "snippet": top_body(last.get("body_plain_text") or "")[:400],
            **result,
        }
        if action.get("action") == "reply":
            name = first_name(last)
            action["reply_name"] = name
            action["body_html"] = reply_body(action["template"], name, last)
        actions.append(action)

    stats = Counter(item["action"] for item in actions)
    categories = Counter(item["category"] for item in actions)
    status_update_candidates = build_status_update_candidates(actions)
    return {
        "generated_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "mailbox": MAILBOX,
        "range_start": start,
        "folder_counts": {"INBOX": len(inbox), "SENT": 0},
        "inbox_thread_count": len(latest_inbox_by_thread),
        "unreplied_inbox_thread_count": len(candidates),
        "message_count": len(messages),
        "thread_count": len(threads),
        "last_received_thread_count": len(actions),
        "stats": dict(stats),
        "categories": dict(categories),
        "status_update_candidates": status_update_candidates,
        "actions": actions,
    }


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def require_suppression_config():
    missing = [
        name
        for name, value in {
            "SUPPRESSION_BASE_TOKEN": BASE_TOKEN,
            "SUPPRESSION_TABLE_ID": TABLE_ID,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


def batch_create_suppression(records):
    if not records:
        return {"created": 0, "result": None}
    require_suppression_config()
    payload = {"create_records": records}
    path = "suppression_daily_new.json"
    write_json(path, payload)
    data = lark_json(
        [
            "base",
            "+record-batch-create",
            "--as",
            "user",
            "--base-token",
            BASE_TOKEN,
            "--table-id",
            TABLE_ID,
            "--json",
            f"@{path}",
        ]
    )
    ids = (data.get("data") or {}).get("record_id_list") or []
    return {"created": len(ids), "result": data}


def flag_messages(message_ids):
    if not message_ids:
        return {"success": [], "failed": []}
    data = lark_json(
        [
            "mail",
            "+message-modify",
            "--as",
            "user",
            "--mailbox",
            MAILBOX,
            "--message-ids",
            ",".join(message_ids),
            "--add-label-ids",
            "flagged",
            "--format",
            "json",
        ]
    )
    payload = data.get("data") or data
    return {
        "success": payload.get("success_message_ids") or [],
        "failed": payload.get("failed_message_ids") or [],
    }


def preserve_unread_message_ids(actions):
    reasons = {
        "offline_campus_ambassador_manual",
        "meeting_manual_review",
    }
    return list(
        dict.fromkeys(
            action["message_id"]
            for action in actions
            if action.get("action") == "flag"
            and action.get("reason") in reasons
            and action.get("message_id")
        )
    )


def mark_unread_messages(message_ids):
    if not message_ids:
        return {"success": [], "failed": []}
    data = lark_json(
        [
            "mail",
            "+message-modify",
            "--as",
            "user",
            "--mailbox",
            MAILBOX,
            "--message-ids",
            ",".join(message_ids),
            "--add-label-ids",
            "unread",
            "--format",
            "json",
        ]
    )
    payload = data.get("data") or data
    return {
        "success": payload.get("success_message_ids") or [],
        "failed": payload.get("failed_message_ids") or [],
    }


def mark_read_messages(message_ids):
    if not message_ids:
        return {"success": [], "failed": []}
    remaining = list(dict.fromkeys(message_ids))
    successful = []
    last_failures = []
    for attempt in range(3):
        proc = run_lark(
            [
                "mail",
                "+message-modify",
                "--as",
                "user",
                "--mailbox",
                MAILBOX,
                "--message-ids",
                ",".join(remaining),
                "--remove-label-ids",
                "unread",
                "--format",
                "json",
            ],
            check=False,
        )
        try:
            data = parse_json(proc.stdout, proc.stderr)
        except Exception:
            data = {}
        payload = data.get("data") or data
        batch_success = payload.get("success_message_ids") or []
        successful.extend(item for item in batch_success if item not in successful)
        last_failures = payload.get("failed_message_ids") or []
        failed_ids = {
            item.get("message_id") if isinstance(item, dict) else item
            for item in last_failures
        }
        remaining = [
            message_id
            for message_id in remaining
            if message_id not in successful and (not failed_ids or message_id in failed_ids)
        ]
        if not remaining:
            break
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    return {"success": successful, "failed": last_failures if remaining else []}


def reply_sent(result):
    data = (result.get("envelope") or {}).get("data") or {}
    return (
        result.get("returncode") == 0
        and (result.get("envelope") or {}).get("ok")
        and bool(data.get("message_id"))
        and not data.get("automation_send_disable_reason")
    )


STATUS_TEMPLATE_RULES = {
    "more_info": ("Interested", "showed interest or asked to learn more"),
    "faq": ("Interested", "asked program details"),
    "applied": ("Ask for Interview", "submitted an application or form"),
    "meeting_confirm": ("Ask for Interview", "is already in the interview flow"),
    "reschedule": ("Ask for Interview", "needs interview scheduling or rescheduling"),
    "schedule_call": ("Ask for Interview", "asked to schedule a call"),
}

STATUS_NO_REVIEW_CATEGORY_RULES = {
    "面试确认": ("Ask for Interview", "is already in the interview flow"),
}

STATUS_CATEGORY_RULES = {
    "Beta/collab 专用流程": ("Interested", "asked collaboration details"),
    "经纪/报价/商务条件": ("Interested", "discussing collaboration terms; review before updating"),
    "约面谈/面试相关（人工）": ("Ask for Interview", "meeting-related message; review before updating"),
}


def status_update_for_action(action):
    template = action.get("template")
    category = action.get("category")
    if template in STATUS_TEMPLATE_RULES:
        new_status, reason = STATUS_TEMPLATE_RULES[template]
        needs_review = False
    elif category in STATUS_NO_REVIEW_CATEGORY_RULES:
        new_status, reason = STATUS_NO_REVIEW_CATEGORY_RULES[category]
        needs_review = False
    elif category in STATUS_CATEGORY_RULES:
        new_status, reason = STATUS_CATEGORY_RULES[category]
        needs_review = True
    else:
        return None

    return {
        "name": action.get("from_name") or action.get("reply_name") or "",
        "email": action.get("from") or "",
        "recommended_status": new_status,
        "current_action": action.get("action"),
        "category": category,
        "template": template,
        "subject": action.get("subject") or "",
        "message_id": action.get("message_id") or "",
        "thread_id": action.get("thread_id") or "",
        "reason": reason,
        "needs_review_before_update": needs_review,
    }


def build_status_update_candidates(actions, reply_results=None):
    reply_by_message_id = {
        item.get("message_id"): item
        for item in (reply_results or [])
        if item.get("message_id")
    }
    candidates = []
    for action in actions:
        candidate = status_update_for_action(action)
        if not candidate:
            continue

        reply_result = reply_by_message_id.get(action.get("message_id"))
        if reply_results is not None and action.get("action") == "reply":
            if not reply_result or not reply_sent(reply_result):
                continue
            candidate["basis"] = "reply_sent"
        elif action.get("action") == "flag":
            candidate["basis"] = "flagged_for_user_review"
        else:
            candidate["basis"] = "classified"

        candidates.append(candidate)
    return candidates


def send_reply(action, run_dir):
    if action.get("template") in MANUAL_MEETING_TEMPLATES:
        return {
            "message_id": action.get("message_id"),
            "thread_id": action.get("thread_id"),
            "to": action.get("from"),
            "category": action.get("category"),
            "template": action.get("template"),
            "returncode": 1,
            "envelope": {
                "ok": False,
                "error": {
                    "type": "policy",
                    "subtype": "meeting_requires_manual_review",
                    "message": "Meeting-related replies must remain unread and be handled manually.",
                },
            },
        }
    body_path = run_dir / f"reply_{action['message_id']}.html"
    body_path.write_text(action["body_html"], encoding="utf-8")
    proc = run_lark(
        [
            "mail",
            "+reply",
            "--as",
            "user",
            "--mailbox",
            MAILBOX,
            "--from",
            MAILBOX,
            "--message-id",
            action["message_id"],
            "--body-file",
            str(body_path),
            "--no-signature",
            "--confirm-send",
            "--format",
            "json",
        ],
        check=False,
    )
    envelope = None
    try:
        envelope = parse_json(proc.stdout, proc.stderr)
    except Exception:
        envelope = {"ok": False, "raw_stdout": proc.stdout, "raw_stderr": proc.stderr}
    result = {
        "message_id": action["message_id"],
        "thread_id": action["thread_id"],
        "to": action["from"],
        "category": action["category"],
        "template": action.get("template"),
        "returncode": proc.returncode,
        "envelope": envelope,
    }
    data = envelope.get("data") or {}
    sent_id = data.get("message_id")
    if proc.returncode == 0 and sent_id and not data.get("automation_send_disable_reason"):
        status_proc = run_lark(
            [
                "mail",
                "user_mailbox.messages",
                "send_status",
                "--as",
                "user",
                "--params",
                json.dumps(
                    {"user_mailbox_id": MAILBOX, "message_id": sent_id},
                    ensure_ascii=False,
                ),
                "--format",
                "json",
            ],
            check=False,
        )
        try:
            result["send_status"] = parse_json(status_proc.stdout, status_proc.stderr)
        except Exception:
            result["send_status"] = {
                "ok": False,
                "raw_stdout": status_proc.stdout,
                "raw_stderr": status_proc.stderr,
            }
    return result


def execute_plan(plan_file):
    plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    run_dir = Path("autoreply_runs") / datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    suppression_fetch_error = None
    try:
        suppression = fetch_suppression_emails()
    except Exception as exc:
        suppression = set()
        suppression_fetch_error = str(exc)
    suppression_records = []
    for action in plan["actions"]:
        if action.get("action") != "suppress":
            continue
        email_addr = (action.get("suppression_email") or action.get("from") or "").lower()
        if not email_addr or email_addr in suppression:
            continue
        suppression.add(email_addr)
        suppression_records.append(
            {
                "Email": email_addr,
                "Reason": action.get("reason") or "do_not_contact",
                "Source": "Daily automation",
                "Last Seen At": action.get("date") or datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "Thread ID": action.get("thread_id") or "",
                "Message ID": action.get("message_id") or "",
                "Notes": action.get("notes") or action.get("snippet") or "",
            }
        )
    if suppression_fetch_error:
        suppression_result = {
            "created": 0,
            "failed": len(suppression_records),
            "error": suppression_fetch_error,
        }
    else:
        try:
            suppression_result = batch_create_suppression(suppression_records)
            suppression_result.setdefault("failed", 0)
        except Exception as exc:
            suppression_result = {
                "created": 0,
                "failed": len(suppression_records),
                "error": str(exc),
            }

    flag_ids = [
        action["message_id"]
        for action in plan["actions"]
        if action.get("action") == "flag" and action.get("message_id")
    ]
    flag_result = flag_messages(flag_ids)
    preserve_unread_result = mark_unread_messages(
        preserve_unread_message_ids(plan["actions"])
    )

    reply_results = []
    for action in plan["actions"]:
        if action.get("action") != "reply":
            continue
        reply_results.append(send_reply(action, run_dir))
    read_result = mark_read_messages(
        [
            item["message_id"]
            for item in reply_results
            if item.get("message_id") and reply_sent(item)
        ]
    )
    status_update_candidates = build_status_update_candidates(
        plan["actions"],
        reply_results,
    )

    result = {
        "executed_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "plan_file": str(plan_file),
        "suppression_result": suppression_result,
        "flag_result": flag_result,
        "preserve_unread_result": preserve_unread_result,
        "reply_results": reply_results,
        "read_result": read_result,
        "status_update_candidates": status_update_candidates,
        "summary": {
            "suppression_created": suppression_result["created"],
            "suppression_failed": suppression_result.get("failed", 0),
            "flag_success": len(flag_result["success"]),
            "flag_failed": len(flag_result["failed"]),
            "preserve_unread_success": len(preserve_unread_result["success"]),
            "preserve_unread_failed": len(preserve_unread_result["failed"]),
            "reply_attempted": len(reply_results),
            "reply_success": sum(1 for item in reply_results if reply_sent(item)),
            "reply_failed": sum(1 for item in reply_results if not reply_sent(item)),
            "mark_read_success": len(read_result["success"]),
            "mark_read_failed": len(read_result["failed"]),
            "status_update_candidates": len(status_update_candidates),
        },
    }
    result_path = run_dir / "result.json"
    write_json(result_path, result)
    print(
        json.dumps(
            {
                "result_path": str(result_path),
                **result["summary"],
                "status_update_candidates": status_update_candidates,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-08-01T00:00:00+08:00")
    parser.add_argument("--plan-file", default="")
    parser.add_argument("--execute-plan", default="")
    args = parser.parse_args()

    if args.execute_plan:
        execute_plan(args.execute_plan)
        return

    plan = build_plan(args.start)
    path = args.plan_file or f"campus_autoreply_plan_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json"
    write_json(path, plan)
    print(json.dumps({
        "plan_file": path,
        "folder_counts": plan["folder_counts"],
        "last_received_thread_count": plan["last_received_thread_count"],
        "stats": plan["stats"],
        "categories": plan["categories"],
        "status_update_candidates": plan["status_update_candidates"],
        "reply_candidates": [
            {
                "from": item["from"],
                "subject": item["subject"],
                "category": item["category"],
                "template": item.get("template"),
            }
            for item in plan["actions"]
            if item["action"] == "reply"
        ],
        "flag_candidates": [
            {
                "from": item["from"],
                "subject": item["subject"],
                "category": item["category"],
                "reason": item.get("reason"),
            }
            for item in plan["actions"]
            if item["action"] == "flag"
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
