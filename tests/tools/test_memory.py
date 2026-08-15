from argon.tools.memory import RememberTool


def test_remember_description_does_not_invite_incidental_durable_capture(tmp_path):
    description = RememberTool(tmp_path).description

    assert "operational facts" in description
    assert "incidental conversation" in description
    assert "anything about his life" not in description


async def test_durable_remember_rejects_relative_day_wording(tmp_path):
    result = await RememberTool(tmp_path).execute(
        fact="Tomorrow is a rest day.", lasting=True
    )

    assert result == "Error: durable memories must use an absolute YYYY-MM-DD date."


async def test_durable_remember_rejects_broader_relative_date_wording(tmp_path):
    for fact in (
        "The deadline is next Friday.",
        "He is away this weekend.",
        "The test is in two days.",
        "The project starts next week.",
    ):
        result = await RememberTool(tmp_path).execute(fact=fact, lasting=True)
        assert result == "Error: durable memories must use an absolute YYYY-MM-DD date."


async def test_durable_remember_allows_a_non_temporal_proper_name(tmp_path):
    result = await RememberTool(tmp_path).execute(
        fact="The Tonight Show is his favorite show.", lasting=True
    )

    assert result == "Stored long-term: The Tonight Show is his favorite show."
