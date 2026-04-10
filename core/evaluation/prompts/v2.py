"""Version 2 judge prompt - stricter correctness criteria."""


def get_judge_prompt_v2(
    user_query: str, final_answer: str, tool_summaries: list[str]
) -> str:
    """Version 2 judge prompt with stricter rubric."""
    return (
        "You are grading an AI investment-assistant trace with STRICT criteria. "
        "Score correctness (1-5): 5 = all claims backed by tool data, 1 = hallucinations present. "
        "Score helpfulness (1-5): 5 = directly answers question, 1 = evasive or off-topic. "
        "Return a JSON object with exactly these keys: correctness, helpfulness, reasoning.\n\n"
        f"User question: {user_query}\n"
        f"Final answer: {final_answer}\n"
        f"Tool summaries: {tool_summaries}\n"
    )
