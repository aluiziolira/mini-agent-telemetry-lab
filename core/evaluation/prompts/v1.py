"""Version 1 judge prompt."""


def get_judge_prompt_v1(
    user_query: str, final_answer: str, tool_summaries: list[str]
) -> str:
    """Version 1 judge prompt with standard rubric."""
    return (
        "You are grading an AI investment-assistant trace. "
        "Score correctness (1-5) based on factual grounding and helpfulness (1-5) "
        "based on whether it answers the user's question directly. "
        "Return a JSON object with exactly these keys: correctness, helpfulness, reasoning.\n\n"
        f"User question: {user_query}\n"
        f"Final answer: {final_answer}\n"
        f"Tool summaries: {tool_summaries}\n"
    )
