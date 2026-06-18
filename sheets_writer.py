"""
Agent 3 - Google Sheets Writer
Writes property listings to a Google Sheet, deduplicating by address.
"""

import datetime
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

HEADER_ROW = [
    "Address",
    "City",
    "State",
    "Zip Code",
    "Price",
    "Phone Number",
    "Seller Name",
    "Bedrooms",
    "Bathrooms",
    "Sq Footage",
    "Listing URL",
    "Notes",
    "Status",
    "Follow Up",
    "Date Added",
]

ADDRESS_COL_INDEX = 0  # "Address" is the first column


class SheetsWriter:
    def __init__(self, config: Any, logger: Any) -> None:
        self.config = config
        self.logger = logger
        self._service = None

    def _get_service(self):
        if self._service is None:
            creds_file = self.config.get("google_credentials_file", "google_credentials.json")
            creds = service_account.Credentials.from_service_account_file(
                creds_file,
                scopes=SCOPES,
            )
            self._service = build("sheets", "v4", credentials=creds)
        return self._service

    def _get_sheet_id(self) -> str:
        return self.config.get("google_sheet_id", "")

    def _read_existing_rows(self, service, sheet_id: str) -> list:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range="A:O")
            .execute()
        )
        return result.get("values", [])

    def _ensure_header(self, service, sheet_id: str, existing_rows: list) -> None:
        if not existing_rows:
            body = {"values": [HEADER_ROW]}
            service.spreadsheets().values().append(
                spreadsheetId=sheet_id,
                range="A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body=body,
            ).execute()
            self.logger.info("Created header row in empty sheet.")

    def _existing_addresses(self, existing_rows: list) -> set:
        addresses = set()
        for row in existing_rows[1:]:  # skip header
            if row and len(row) > ADDRESS_COL_INDEX:
                addresses.add(row[ADDRESS_COL_INDEX].strip().lower())
        return addresses

    def _listing_to_row(self, listing: dict) -> list:
        today = datetime.date.today().strftime("%m/%d/%Y")
        return [
            listing.get("address", ""),
            listing.get("city", ""),
            listing.get("state", ""),
            listing.get("zip_code", ""),
            listing.get("price", ""),
            listing.get("phone_number", ""),
            listing.get("seller_name", ""),
            listing.get("bedrooms", ""),
            listing.get("bathrooms", ""),
            listing.get("square_footage", ""),
            listing.get("listing_url", ""),
            listing.get("notes", ""),
            "New",   # Status default
            "",      # Follow Up — left blank for user
            today,   # Date Added
        ]

    def run(self, listings: list) -> dict:
        """
        Write listings to Google Sheets.
        Returns dict with keys: added, skipped, errors
        """
        sheet_id = self._get_sheet_id()
        if not sheet_id:
            self.logger.error(
                "google_sheet_id is not configured (empty string). "
                "Add it to config.json and re-run."
            )
            return {"added": 0, "skipped": 0, "errors": 1}

        try:
            service = self._get_service()
        except Exception as exc:
            self.logger.error(f"Failed to authenticate with Google Sheets API: {exc}")
            return {"added": 0, "skipped": 0, "errors": 1}

        try:
            existing_rows = self._read_existing_rows(service, sheet_id)
        except HttpError as exc:
            self.logger.error(f"Failed to read existing rows from sheet: {exc}")
            return {"added": 0, "skipped": 0, "errors": 1}

        self._ensure_header(service, sheet_id, existing_rows)

        if not existing_rows:
            existing_rows = [HEADER_ROW]

        existing_addresses = self._existing_addresses(existing_rows)
        existing_count = len(existing_rows) - 1  # subtract header
        self.logger.info(f"Sheet has {existing_count} existing listings.")

        new_rows = []
        skipped = 0

        for listing in listings:
            address = listing.get("address", "").strip().lower()
            if address in existing_addresses:
                skipped += 1
            else:
                new_rows.append(self._listing_to_row(listing))
                existing_addresses.add(address)  # prevent intra-batch duplicates

        self.logger.info(f"Adding {len(new_rows)} new listings.")
        self.logger.info(f"Skipping {skipped} duplicates.")

        if not new_rows:
            return {"added": 0, "skipped": skipped, "errors": 0}

        try:
            body = {"values": new_rows}
            service.spreadsheets().values().append(
                spreadsheetId=sheet_id,
                range="A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body=body,
            ).execute()
        except HttpError as exc:
            self.logger.error(f"Failed to append rows to sheet: {exc}")
            return {"added": 0, "skipped": skipped, "errors": 1}
        except Exception as exc:
            self.logger.error(f"Unexpected error while appending rows: {exc}")
            return {"added": 0, "skipped": skipped, "errors": 1}

        return {"added": len(new_rows), "skipped": skipped, "errors": 0}
