import os
import random
import re

import yfinance as yf
from ddgs import DDGS
from openai import OpenAI
from dotenv import load_dotenv

from sdk.tracer import Tracer

load_dotenv()

tracer = Tracer(
    os.environ.get("TELEMETRY_BASE_URL", "http://127.0.0.1:8000"),
    os.environ["INGEST_API_KEY"],
)
tracer.agent_name = "research_analyst"
openai_client = OpenAI(api_key=os.environ["LLM_API_KEY"])


def truncate(value, limit):
    text = str(value)
    return text if len(text) <= limit else text[:limit]


def extract_symbol(query):
    matches = re.findall(r"\b[A-Z]{1,5}\b", query)
    return matches[-1] if matches else "AAPL"


if __name__ == "__main__":
    user_query = os.sys.argv[1]
    symbol = extract_symbol(user_query)
    with tracer.span("research_analyst_run", "chain") as root_span:
        root_span.set_attribute("input", user_query)
        with tracer.span(
            "yfinance_fetch", "tool", parent_span_id=root_span.span_id
        ) as stock_span:
            info = yf.Ticker(symbol).info
            stock_data = {
                "symbol": symbol,
                "price": info.get("currentPrice"),
                "pe_ratio": info.get("trailingPE"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                "market_cap": info.get("marketCap"),
            }
            stock_span.set_attribute("output", stock_data)

        search_summary = ""
        try:
            with tracer.span(
                "web_search", "tool", parent_span_id=root_span.span_id
            ) as search_span:
                if random.random() < 0.2:
                    raise TimeoutError("simulated search timeout")
                data = list(DDGS().text(f"{symbol} stock news", max_results=3))
                search_summary = truncate(
                    " | ".join(item.get("title", "") for item in data)
                    or "No search summary found.",
                    500,
                )
                search_span.set_attribute("output", search_summary)
        except Exception as exc:
            search_summary = f"Search unavailable: {exc}"

        with tracer.span(
            "synthesis_call", "llm", parent_span_id=root_span.span_id
        ) as llm_span:
            prompt = truncate(
                f"User question: {user_query}\nStock data: {stock_data}\nSearch summary: {search_summary}\nProvide a concise grounded recommendation.",
                2000,
            )
            response = openai_client.responses.create(model="gpt-4o-mini", input=prompt)
            final_recommendation = truncate(response.output_text, 2000)
            llm_span.set_attribute("model", "gpt-4o-mini")
            llm_span.set_attribute("prompt_tokens", response.usage.input_tokens)
            llm_span.set_attribute("completion_tokens", response.usage.output_tokens)
            llm_span.set_attribute("input", prompt)
            llm_span.set_attribute("output", final_recommendation)

        tracer.finish({"output": final_recommendation})
        print(final_recommendation)
