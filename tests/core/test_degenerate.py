"""Replies that are not language.

Shapes here are taken from what Argon actually sent to the phone on 16 Aug —
two decoding loops a minute apart, one of 4,557 characters that was 1.9%
alphanumeric with sixty full stops in a row. Nothing noticed: the turn
succeeded, the ledger recorded it as said, and it was stored as the assistant's
own words, so it became context for the next turn.
"""

from argon.core.degenerate import describe, looks_degenerate


class TestLoopsAreCaught:
    def test_the_message_that_started_this(self):
        # 1.9% alphanumeric, mostly newlines and full stops.
        text = "Weekend " + ("\n" * 1200) + ("." * 900) + ("…" * 60)

        assert looks_degenerate(text) is True

    def test_a_long_run_of_one_character(self):
        assert looks_degenerate("Weekend " + "." * 300) is True

    def test_padding_with_invisible_characters(self):
        # The real one was padded with non-breaking and zero-width spaces.
        assert looks_degenerate("Weekend" + "\xa0​" * 200) is True

    def test_a_wall_of_punctuation(self):
        assert looks_degenerate("- " * 200) is True


class TestRealRepliesSurvive:
    def test_ordinary_prose(self):
        text = (
            "Two Math summer assignments are due Sunday at 8 PM, then the AP USH "
            "Period 1 Progress Checks are due Sunday night. After that, HW 2 is due "
            "Monday night, and Chapter 2 Key Terms are due Wednesday night. When do "
            "you plan to start the Math An Summer Assignment?"
        )

        assert looks_degenerate(text) is False

    def test_heavy_markdown_and_emoji(self):
        # Taken from a real reply the guard must not swallow: rules, bold,
        # emoji and unicode spacing, all at once.
        text = (
            "Here's a quick mock-up of a rich-text message with buttons:\n\n---\n\n"
            "**Did this help you?**\n\n👍  **Yes**  👎  **No**"
            "  🤔  **Maybe**\n\n---\n\nYou can replace the emojis with "
            "actual Discord button components if your bot supports them."
        )

        assert looks_degenerate(text) is False

    def test_a_code_block(self):
        text = (
            "Run this:\n\n```bash\ncurl -sH \"Authorization: Bearer $TOKEN\" \\\n"
            "  https://argon.agentneon.dev/v1/ios/diagnostics | jq '.entries[0]'\n```\n\n"
            "It prints the newest report the phone sent."
        )

        assert looks_degenerate(text) is False

    def test_a_short_symbol_reply(self):
        # Below the length floor on purpose: "👍" and "3pm?" are real answers.
        assert looks_degenerate("👍") is False
        assert looks_degenerate("---") is False
        assert looks_degenerate("") is False

    def test_a_horizontal_rule_inside_prose(self):
        assert looks_degenerate("Done.\n\n" + "-" * 30 + "\n\nNext up: APUSH." * 6) is False


class TestDescribe:
    def test_it_says_enough_to_diagnose(self):
        detail = describe("Weekend " + "." * 300)

        assert "alphanumeric" in detail
        assert "in a row" in detail
