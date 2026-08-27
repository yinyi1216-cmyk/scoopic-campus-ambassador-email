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

    def test_paid_campus_ambassador_subject_is_offline(self):
        result = mailer.classify(
            message(
                "Re: Paid Campus Ambassador Opportunity at UCB",
                "I'm still highly interested and look forward to the next steps.",
            )
        )
        self.assertEqual(result["action"], "flag")
        self.assertEqual(result["reason"], "offline_campus_ambassador_manual")

    def test_university_club_network_is_offline(self):
        result = mailer.classify(
            message(
                "Re: Paid Opportunity for USC Students",
                "I'd be happy to share this with our club network. President, American Marketing Association, University of Southern California",
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

    def test_agent_kindly_pass_is_polite_reply(self):
        result = mailer.classify(
            message(
                "Re: Creator Program",
                "Thank you for reaching out! Libby will kindly pass!",
                sender="agent@talentgroup.com",
            )
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

    def test_yahoo_quoted_unsubscribe_does_not_override_application(self):
        result = mailer.classify(
            message(
                "Re: Creator Program",
                "Applied! Thank you! Sent from Yahoo Mail for iPhone On Wednesday, August 26, 2026, 2:15 PM, campusambassador@scoopic.ai wrote: Unsubscribe",
            )
        )
        self.assertEqual(result["action"], "reply")
        self.assertEqual(result["template"], "applied")

    def test_completed_application_is_acknowledged(self):
        result = mailer.classify(
            message(
                "Re: Creator Program",
                "I've just completed the application and I'm excited about the opportunity.",
            )
        )
        self.assertEqual(result["action"], "reply")
        self.assertEqual(result["template"], "applied")

    def test_former_employee_forward_is_not_replied_to(self):
        result = mailer.classify(
            message(
                "No Longer Works Here Re: Creator Program",
                "Jasmine no longer works here. Your email has been automatically forwarded to another agent.",
                sender="jasmine@agency.com",
            )
        )
        self.assertNotEqual(result["action"], "reply")
        self.assertEqual(result["reason"], "auto_response")

    def test_short_thank_you_too_needs_no_reply(self):
        result = mailer.classify(
            message("Re: Creator Program", "Hi Zoe, Thank you so much! You too!")
        )
        self.assertEqual(result["action"], "no_reply")

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
