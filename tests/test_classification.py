import unittest

import run_campus_mail_autoreply as mailer


def message(subject, body, sender="student@example.com", name="Student", attachments=None):
    return {
        "subject": subject,
        "head_from": {"mail_address": sender, "name": name},
        "body_plain_text": body,
        "attachments": attachments or [],
    }


class ClassificationTests(unittest.TestCase):
    def test_offline_campus_organization_is_manual(self):
        result = mailer.classify(
            message(
                "Re: University Opportunities for your organization",
                "President | Interfraternity Council - State University",
                sender="ifc.president@example.com",
            )
        )
        self.assertEqual(result["action"], "flag")
        self.assertEqual(result["reason"], "offline_campus_ambassador_manual")

    def test_meeting_request_is_manual(self):
        result = mailer.classify(
            message(
                "Re: Paid Campus Ambassador Opportunity",
                "I'd love to hop on a quick call this week. Send a few times that work.",
            )
        )
        self.assertEqual(result["action"], "flag")
        self.assertEqual(result["reason"], "meeting_manual_review")

    def test_application_plus_interview_is_still_manual(self):
        result = mailer.classify(
            message(
                "Re: Creator Program",
                "I submitted my application and would love to schedule an interview.",
            )
        )
        self.assertEqual(result["action"], "flag")
        self.assertEqual(result["reason"], "meeting_manual_review")

    def test_meeting_confirmation_is_manual(self):
        result = mailer.classify(message("Re: Next Step", "Confirmed for tomorrow at 11:30."))
        self.assertEqual(result["action"], "flag")
        self.assertEqual(result["reason"], "meeting_manual_review")

    def test_plain_interest_can_use_more_info(self):
        result = mailer.classify(
            message("Re: Creator Program", "I'm interested and would love to learn more.")
        )
        self.assertEqual(result["action"], "reply")
        self.assertEqual(result["template"], "more_info")

    def test_decline_with_adverb_is_polite_reply(self):
        result = mailer.classify(
            message("Re: Creator Program", "I have to kindly decline this offer.")
        )
        self.assertEqual(result["action"], "reply")
        self.assertEqual(result["template"], "decline")

    def test_quoted_unsubscribe_does_not_override_application(self):
        result = mailer.classify(
            message(
                "Re: Creator Program",
                "I filled out the form. Thanks. From: scoopic "
                "<campusambassador@scoopic.ai> Unsubscribe",
            )
        )
        self.assertEqual(result["action"], "reply")
        self.assertEqual(result["template"], "applied")

    def test_meeting_and_offline_flags_are_forced_back_to_unread(self):
        actions = [
            {
                "action": "flag",
                "reason": "meeting_manual_review",
                "message_id": "meeting-id",
            },
            {
                "action": "flag",
                "reason": "offline_campus_ambassador_manual",
                "message_id": "offline-id",
            },
            {
                "action": "flag",
                "reason": "system_alert",
                "message_id": "system-id",
            },
        ]

        self.assertEqual(
            mailer.preserve_unread_message_ids(actions),
            ["meeting-id", "offline-id"],
        )


if __name__ == "__main__":
    unittest.main()
