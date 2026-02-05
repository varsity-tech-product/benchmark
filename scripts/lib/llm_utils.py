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

# Available models for diversity in synthesis
MODELS = [
    "openai/gpt-4o-mini",
    "anthropic/claude-3-haiku",
    "google/gemini-2.5-flash",
    "x-ai/grok-4.1-fast",
]


class LLMError(Exception):
    """Custom exception for LLM API errors."""

    pass


class RateLimitError(LLMError):
    """Rate limit exceeded."""

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
    retry=retry_if_exception_type(RateLimitError),
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

            if response.status != 200:
                error_text = await response.text()
                raise LLMError(f"API error {response.status}: {error_text}")

            data = await response.json()

            if "error" in data:
                raise LLMError(f"API returned error: {data['error']}")

            content = data["choices"][0]["message"]["content"]
            return content, model

    finally:
        if close_session:
            await session.close()


async def call_llm_with_json(
    prompt: str,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
) -> tuple[dict, str]:
    """
    Call LLM and parse JSON response.

    Returns:
        Tuple of (parsed_json, model_used)
    """
    import json

    # Add JSON instruction to system prompt
    json_system = (system_prompt or "") + "\n\nRespond with valid JSON only."

    response, model_used = await call_llm(
        prompt=prompt,
        model=model,
        system_prompt=json_system,
        temperature=0.3,  # Lower temperature for structured output
        session=session,
    )

    # Try to extract JSON from response
    response = response.strip()

    # Handle markdown code blocks
    if response.startswith("```json"):
        response = response[7:]
    elif response.startswith("```"):
        response = response[3:]
    if response.endswith("```"):
        response = response[:-3]

    response = response.strip()

    try:
        parsed = json.loads(response)
        return parsed, model_used
    except json.JSONDecodeError as e:
        raise LLMError(
            f"Failed to parse JSON response: {e}\nResponse: {response[:500]}"
        )


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
