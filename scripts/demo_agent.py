import atexit
import logging
import logging.config
import os
import random
import re

import yfinance as yf
from ddgs import DDGS
from dotenv import load_dotenv
from openai import OpenAI

from scripts.demo_tools import run_tool_with_retries
from sdk.tracer import Tracer
from telemetry_lab.logging_config import get_logging_config

load_dotenv()
logging.config.dictConfig(get_logging_config())
logger = logging.getLogger("telemetry_lab")

ingest_api_key = os.environ.get("INGEST_API_KEY")
if not ingest_api_key:
    raise RuntimeError("INGEST_API_KEY is required to run scripts/demo_agent.py")

llm_api_key = os.environ.get("LLM_API_KEY")
if not llm_api_key:
    raise RuntimeError("LLM_API_KEY is required to run scripts/demo_agent.py")

tracer = Tracer(
    os.environ.get("TELEMETRY_BASE_URL", "http://127.0.0.1:8000"),
    ingest_api_key,
)
tracer.agent_name = "research_analyst"
atexit.register(tracer.shutdown)
openai_client = OpenAI(api_key=llm_api_key)


def truncate(value, limit):
    text = str(value)
    return text if len(text) <= limit else text[:limit]


def extract_symbol(query):
    matches = re.findall(r"\b[A-Z]{1,5}\b", query)
    return matches[-1] if matches else "AAPL"


def _fetch_stock_data(stock_span, symbol):
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
    return stock_data


def _run_web_search(search_span, symbol):
    if random.random() < 0.2:
        raise TimeoutError("simulated search timeout")

    data = list(DDGS().text(f"{symbol} stock news", max_results=3))
    search_summary = truncate(
        " | ".join(item.get("title", "") for item in data) or "No search summary found.",
        500,
    )
    search_span.set_attribute("output", search_summary)
    return search_summary


if __name__ == "__main__":
    user_query = os.sys.argv[1]
    symbol = extract_symbol(user_query)
    with tracer.span("research_analyst_run", "chain") as root_span:
        root_span.set_attribute("input", user_query)
        try:
            stock_data = run_tool_with_retries(
                tracer,
                tool_name="yfinance_fetch",
                parent_span_id=root_span.span_id,
                max_attempts=2,
                operation=lambda stock_span: _fetch_stock_data(stock_span, symbol),
            )
        except Exception as exc:
            stock_data = {"symbol": symbol, "error": f"Stock data unavailable: {exc}"}

        search_summary = ""
        try:
            search_summary = run_tool_with_retries(
                tracer,
                tool_name="web_search",
                parent_span_id=root_span.span_id,
                max_attempts=2,
                operation=lambda search_span: _run_web_search(search_span, symbol),
            )
        except Exception as exc:
            search_summary = f"Search unavailable: {exc}"

        with tracer.span("synthesis_call", "llm", parent_span_id=root_span.span_id) as llm_span:
            prompt_body = (
                f"User question: {user_query}\n"
                f"Stock data: {stock_data}\n"
                f"Search summary: {search_summary}\n"
                "Provide a concise grounded recommendation."
            )
            prompt = truncate(
                prompt_body,
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
        logger.info(
            "Demo agent completed",
            extra={
                "extra_fields": {
                    "output_length": len(final_recommendation),
                    "symbol": symbol,
                }
            },
        )
