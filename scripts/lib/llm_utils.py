"""LLM utilities using OpenRouter API."""

import asyncio
import os
import random
from typing import Optional

import aiohttp
from dotenv import load_dotenv
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

load_dotenv()

# OpenRouter API endpoint
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# # Available models for diversity in synthesis (10 models) — FULL
# MODELS = [
#     # Claude (2)
#     "anthropic/claude-opus-4.6",
#     "anthropic/claude-sonnet-4.5",
#     # OpenAI (2)
#     "openai/gpt-5.2",
#     "openai/gpt-oss-120b",
#     # Gemini (3)
#     "google/gemini-3-pro-preview",
#     "google/gemini-2.5-pro",
#     "google/gemini-3-flash-preview",
#     # DeepSeek (1)
#     "deepseek/deepseek-v3.2",
#     # Qwen (1)
#     "qwen/qwen3-max",
#     # Grok (1)
#     "x-ai/grok-4.1-fast",
# ]

# Available models for diversity in synthesis (10 models) — LITE
MODELS = [
    # Claude (2)
    "anthropic/claude-opus-4.5",
    "anthropic/claude-sonnet-4.5",
    # OpenAI (2)
    "openai/gpt-oss-120b",
    "openai/gpt-5-mini",
    # Gemini (2)
    "google/gemini-3-flash-preview",
    "google/gemini-2.5-flash",
    # DeepSeek (1)
    "deepseek/deepseek-chat-v3.1",
    # Grok (1)
    "x-ai/grok-4.1-fast",
]

# Fallback model: stable, reliable for structured JSON output
FALLBACK_MODEL = "anthropic/claude-sonnet-4.5"


class LLMError(Exception):
    """Custom exception for LLM API errors."""

    pass


class RateLimitError(LLMError):
    """Rate limit exceeded."""

    pass


class ServerError(LLMError):
    """Server-side error (5xx)."""

    pass


def get_api_key() -> str:
    """Get OpenRouter API key from environment."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENROUTER_API_KEY not found. Set it in .env or environment variables."
        )
    return api_key


def select_random_model() -> str:
    """Select a random model for diversity."""
    return random.choice(MODELS)


@retry(
    retry=retry_if_exception_type((RateLimitError, ServerError)),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(5),
)
async def call_llm(
    prompt: str,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    session: Optional[aiohttp.ClientSession] = None,
) -> tuple[str, str]:
    """
    Call LLM via OpenRouter API.

    Args:
        prompt: User prompt
        model: Model to use (random if None)
        system_prompt: Optional system prompt
        temperature: Sampling temperature
        max_tokens: Maximum tokens in response
        session: Optional aiohttp session for connection reuse

    Returns:
        Tuple of (response_text, model_used)

    Raises:
        LLMError: On API errors
        RateLimitError: On rate limiting (triggers retry)
    """
    model = model or select_random_model()
    api_key = get_api_key()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/quant-tutor-benchmark",
        "X-Title": "Quant Tutor Benchmark",
    }

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        close_session = True

    try:
        async with session.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            if response.status == 429:
                raise RateLimitError("Rate limit exceeded")

            if 500 <= response.status < 600:
                error_text = await response.text()
                raise ServerError(f"Server error {response.status}: {error_text}")

            if response.status != 200:
                error_text = await response.text()
                raise LLMError(f"API error {response.status}: {error_text}")

            data = await response.json()

            if "error" in data:
                error_info = data["error"]
                code = error_info.get("code", 0) if isinstance(error_info, dict) else 0
                if isinstance(code, int) and 500 <= code < 600:
                    raise ServerError(f"Server error: {error_info}")
                raise LLMError(f"API returned error: {error_info}")

            content = data["choices"][0]["message"]["content"]
            return content, model

    finally:
        if close_session:
            await session.close()


def _extract_json(text: str) -> dict:
    """Extract a JSON object from text that may contain extra content.

    Handles:
    - Pure JSON responses
    - Markdown ```json ... ``` code blocks
    - Reasoning/thinking text before or after the JSON object
    """
    import json
    import re

    text = text.strip()

    # 1. Strip markdown code blocks
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    # 2. Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. Find the outermost { ... } in the text (handles reasoning leakage)
    match = re.search(r"\{", text)
    if match:
        start = match.start()
        # Walk forward to find matching closing brace
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    raise LLMError(
        f"Failed to parse JSON response: no valid JSON object found\nResponse: {text[:500]}"
    )


async def call_llm_with_json(
    prompt: str,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
    max_retries: int = 2,
) -> tuple[dict, str]:
    """
    Call LLM and parse JSON response.

    Retry strategy on parse failure:
      attempt 0: requested model (or random)
      attempt 1: different random model
      attempt 2: fallback to FALLBACK_MODEL (stable, guaranteed JSON)

    Returns:
        Tuple of (parsed_json, model_used)
    """
    json_system = (system_prompt or "") + "\n\nRespond with valid JSON only."

    last_error = None
    for attempt in range(1 + max_retries):
        if attempt == 0:
            current_model = model or select_random_model()
        elif attempt < max_retries:
            current_model = select_random_model()
        else:
            # Final attempt: use stable fallback model
            current_model = FALLBACK_MODEL
        try:
            response, model_used = await call_llm(
                prompt=prompt,
                model=current_model,
                system_prompt=json_system,
                temperature=0.3,  # Lower temperature for structured output
                max_tokens=4096,  # Higher limit to prevent JSON truncation
                session=session,
            )
            parsed = _extract_json(response)
            return parsed, model_used
        except LLMError as e:
            last_error = e
            if attempt < max_retries:
                await asyncio.sleep(1)
    raise last_error


class LLMBatchProcessor:
    """Process multiple LLM calls with concurrency control."""

    def __init__(
        self,
        max_concurrent: int = 5,
        checkpoint_every: int = 100,
        checkpoint_file: Optional[str] = None,
    ):
        self.max_concurrent = max_concurrent
        self.checkpoint_every = checkpoint_every
        self.checkpoint_file = checkpoint_file
        self.processed_ids: set[str] = set()
        self.semaphore: Optional[asyncio.Semaphore] = None

        if checkpoint_file:
            self._load_checkpoint()

    def _load_checkpoint(self):
        """Load processed IDs from checkpoint file."""
        import json
        from pathlib import Path

        if self.checkpoint_file and Path(self.checkpoint_file).exists():
            with open(self.checkpoint_file) as f:
                data = json.load(f)
                self.processed_ids = set(data.get("processed_ids", []))
            print(f"Loaded checkpoint with {len(self.processed_ids)} processed records")

    def _save_checkpoint(self):
        """Save processed IDs to checkpoint file."""
        import json

        if self.checkpoint_file:
            with open(self.checkpoint_file, "w") as f:
                json.dump({"processed_ids": list(self.processed_ids)}, f)

    async def process_batch(
        self,
        items: list[tuple[str, str]],  # List of (id, prompt) tuples
        processor_fn,  # async function(id, prompt, session) -> result
    ) -> list:
        """
        Process items in batch with concurrency control.

        Args:
            items: List of (id, prompt) tuples
            processor_fn: Async function to process each item

        Returns:
            List of results
        """
        self.semaphore = asyncio.Semaphore(self.max_concurrent)
        results = []
        processed_count = 0

        async with aiohttp.ClientSession() as session:
            tasks = []

            for item_id, prompt in items:
                if item_id in self.processed_ids:
                    continue

                task = self._process_with_semaphore(
                    item_id, prompt, processor_fn, session
                )
                tasks.append(task)

            for coro in asyncio.as_completed(tasks):
                try:
                    result = await coro
                    if result:
                        results.append(result)
                        self.processed_ids.add(result.get("id", ""))
                        processed_count += 1

                        if processed_count % self.checkpoint_every == 0:
                            self._save_checkpoint()
                            print(f"Checkpoint saved: {processed_count} records")

                except Exception as e:
                    print(f"Error processing item: {e}")

        # Final checkpoint
        self._save_checkpoint()
        return results

    async def _process_with_semaphore(self, item_id, prompt, processor_fn, session):
        """Process item with semaphore for concurrency control."""
        async with self.semaphore:
            return await processor_fn(item_id, prompt, session)
