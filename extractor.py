import json
import os
import time
import math
import anthropic
from dotenv import load_dotenv

load_dotenv()


class DataExtractor:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.batch_size = config.get("batch_size", 5)
        self.max_retries = 3
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=api_key)

    def _build_system_prompt(self):
        return (
            "You are a real estate data extraction specialist. Your task is to parse raw real estate "
            "listing data and extract structured information. You must return ONLY a valid JSON array "
            "with no additional text, markdown, or code fences.\n\n"
            "Field extraction rules:\n"
            "- address: Full street address (e.g., '123 Main St')\n"
            "- city: City name only\n"
            "- state: Two-letter state abbreviation (e.g., 'CA', 'TX')\n"
            "- zip_code: 5-digit ZIP code as a string\n"
            "- price: Numeric integer only. Remove $, commas, and normalize suffixes "
            "(e.g., '$1,250,000' -> 1250000, '850K' -> 850000, '1.2M' -> 1200000)\n"
            "- phone_number: Format as XXX-XXX-XXXX. Must have exactly 10 digits total. "
            "If the number does not have 10 digits, set to null.\n"
            "- seller_name: Name of the seller or listing agent\n"
            "- bedrooms: Number of bedrooms as an integer\n"
            "- bathrooms: Number of bathrooms as a float (e.g., 2.5)\n"
            "- square_footage: Interior square footage as an integer\n"
            "- listing_url: URL of the listing if present\n"
            "- notes: Any interesting or notable details not captured in other fields\n\n"
            "Use null for any field that is missing or cannot be determined. "
            "Return ONLY a JSON array, no other text."
        )

    def _build_user_message(self, batch):
        return (
            "Extract structured data from the following raw real estate listings. "
            "Return a JSON array where each element corresponds to one listing.\n\n"
            f"Raw listings:\n{json.dumps(batch, ensure_ascii=False, indent=2)}"
        )

    def _call_claude_with_retry(self, batch):
        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = self.client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=self._build_system_prompt(),
                    messages=[
                        {"role": "user", "content": self._build_user_message(batch)}
                    ],
                )
                return response.content[0].text
            except anthropic.APIStatusError as e:
                last_error = e
                if e.status_code in (429, 500, 502, 503, 504):
                    wait_time = (2 ** attempt) + (0.1 * attempt)
                    self.logger.warning(
                        f"API error {e.status_code} on attempt {attempt + 1}/{self.max_retries}. "
                        f"Retrying in {wait_time:.1f}s..."
                    )
                    time.sleep(wait_time)
                else:
                    raise
            except anthropic.APIConnectionError as e:
                last_error = e
                wait_time = (2 ** attempt) + (0.1 * attempt)
                self.logger.warning(
                    f"Connection error on attempt {attempt + 1}/{self.max_retries}. "
                    f"Retrying in {wait_time:.1f}s..."
                )
                time.sleep(wait_time)
        raise last_error

    def _parse_response(self, response_text):
        text = response_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            start = 1
            end = len(lines)
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip() == "```":
                    end = i
                    break
            text = "\n".join(lines[start:end]).strip()
        return json.loads(text)

    def _validate_record(self, record):
        if not isinstance(record, dict):
            return False
        address = record.get("address")
        if not address or not str(address).strip():
            return False
        price = record.get("price")
        phone = record.get("phone_number")
        if price is None and (phone is None or not str(phone).strip()):
            return False
        return True

    def _deduplicate(self, records):
        seen = {}
        for record in records:
            address = record.get("address")
            if address is None:
                continue
            key = str(address).strip().lower()
            if key not in seen:
                seen[key] = record
        return list(seen.values())

    def run(self, raw_listings: list) -> list:
        if not raw_listings:
            return []

        total = len(raw_listings)
        num_batches = math.ceil(total / self.batch_size)
        all_records = []

        for batch_index in range(num_batches):
            start = batch_index * self.batch_size
            end = min(start + self.batch_size, total)
            batch = raw_listings[start:end]

            self.logger.info(
                f"Processing batch {batch_index + 1}/{num_batches} ({len(batch)} listings)"
            )

            try:
                response_text = self._call_claude_with_retry(batch)
            except Exception as e:
                self.logger.error(
                    f"Failed to process batch {batch_index + 1}/{num_batches} after retries: {e}"
                )
                continue

            try:
                parsed = self._parse_response(response_text)
            except (json.JSONDecodeError, ValueError) as e:
                self.logger.error(
                    f"JSON parse error for batch {batch_index + 1}/{num_batches}: {e}. Skipping batch."
                )
                continue

            if not isinstance(parsed, list):
                self.logger.error(
                    f"Expected JSON array for batch {batch_index + 1}/{num_batches}, "
                    f"got {type(parsed).__name__}. Skipping batch."
                )
                continue

            valid_records = [r for r in parsed if self._validate_record(r)]
            all_records.extend(valid_records)

        self.logger.info(f"Extracted {len(all_records)} valid records")

        deduplicated = self._deduplicate(all_records)
        self.logger.info(f"Deduplicated to {len(deduplicated)} records")

        return deduplicated
