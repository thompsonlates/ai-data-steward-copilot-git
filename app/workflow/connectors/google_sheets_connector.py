from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import google.auth
import google.auth.transport.requests
import requests
from google.oauth2 import service_account


logger = logging.getLogger(__name__)


class GoogleSheetsConnector:
    SHEETS_API_BASE = (
        "https://sheets.googleapis.com/v4/spreadsheets"
    )

    SHEETS_SCOPE = (
        "https://www.googleapis.com/auth/spreadsheets.readonly"
    )

    def __init__(
        self,
        *,
        credentials_file: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.credentials_file = (
            credentials_file
            or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            or ""
        ).strip()

        self.timeout_seconds = max(
            5,
            min(
                int(timeout_seconds),
                120,
            ),
        )

        self.credentials = self._load_credentials()

    def _load_credentials(self):
        if self.credentials_file:
            return service_account.Credentials.from_service_account_file(
                self.credentials_file,
                scopes=[self.SHEETS_SCOPE],
            )

        credentials, _ = google.auth.default(
            scopes=[self.SHEETS_SCOPE]
        )

        return credentials

    def _get_access_token(self) -> str:
        if not self.credentials.valid:
            request = google.auth.transport.requests.Request()
            self.credentials.refresh(request)

        token = self.credentials.token

        if not token:
            raise RuntimeError(
                "Unable to obtain Google Sheets access token."
            )

        return token

    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": (
                f"Bearer {self._get_access_token()}"
            ),
        }

    # ---------------------------------------------------------
    # Public methods
    # ---------------------------------------------------------

    def test_connection(
        self,
        *,
        spreadsheet_id: str,
    ) -> Dict[str, Any]:
        spreadsheet_id = (
            self._normalize_spreadsheet_id(
                spreadsheet_id
            )
        )

        metadata = self._get_spreadsheet_metadata(
            spreadsheet_id=spreadsheet_id
        )

        return {
            "success": True,
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_title": (
                metadata
                .get("properties", {})
                .get("title")
            ),
            "sheet_count": len(
                metadata.get("sheets", [])
            ),
            "message": (
                "Google Sheets connection successful."
            ),
        }

    def list_sheets(
        self,
        *,
        spreadsheet_id: str,
    ) -> List[Dict[str, Any]]:
        spreadsheet_id = (
            self._normalize_spreadsheet_id(
                spreadsheet_id
            )
        )

        metadata = self._get_spreadsheet_metadata(
            spreadsheet_id=spreadsheet_id
        )

        output: List[
            Dict[str, Any]
        ] = []

        for sheet in metadata.get(
            "sheets",
            [],
        ):
            properties = (
                sheet.get(
                    "properties",
                    {}
                )
            )

            output.append(
                {
                    "sheet_id": (
                        properties.get(
                            "sheetId"
                        )
                    ),
                    "title": (
                        properties.get(
                            "title"
                        )
                    ),
                    "index": (
                        properties.get(
                            "index"
                        )
                    ),
                    "row_count": (
                        properties
                        .get(
                            "gridProperties",
                            {}
                        )
                        .get(
                            "rowCount"
                        )
                    ),
                    "column_count": (
                        properties
                        .get(
                            "gridProperties",
                            {}
                        )
                        .get(
                            "columnCount"
                        )
                    ),
                }
            )

        return output

    def read_rows(
        self,
        *,
        spreadsheet_id: str,
        sheet_name: str,
        header_row: int = 1,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Read a worksheet and return normalized row dictionaries.

        header_row is 1-based.
        """

        spreadsheet_id = (
            self._normalize_spreadsheet_id(
                spreadsheet_id
            )
        )

        normalized_sheet_name = str(
            sheet_name or ""
        ).strip()

        if not normalized_sheet_name:
            raise ValueError(
                "sheet_name is required."
            )

        safe_header_row = max(
            1,
            int(header_row),
        )

        safe_limit: Optional[
            int
        ] = None

        if limit is not None:
            safe_limit = max(
                1,
                min(
                    int(limit),
                    100_000,
                ),
            )

        range_start = (
            safe_header_row
        )

        range_expression = (
            f"{normalized_sheet_name}"
            f"!A{range_start}:Z"
        )

        url = (
            f"{self.SHEETS_API_BASE}/"
            f"{spreadsheet_id}/values/"
            f"{range_expression}"
)

        response = requests.get(
            url,
            headers=self._headers(),
            params={
                "majorDimension": "ROWS",
                "valueRenderOption": (
                    "UNFORMATTED_VALUE"
                ),
                "dateTimeRenderOption": (
                    "FORMATTED_STRING"
                ),
            },
            timeout=self.timeout_seconds,
        )

        self._raise_for_google_error(
            response=response,
            action="read worksheet rows",
        )

        payload = response.json()

        values = (
            payload.get(
                "values",
                []
            )
            or []
        )

        if not values:
            return []

        raw_headers = values[0]

        normalized_headers = (
            self._normalize_headers(
                raw_headers
            )
        )

        output: List[
            Dict[str, Any]
        ] = []

        for raw_row in values[1:]:
            row: Dict[
                str,
                Any
            ] = {}

            for index, header in enumerate(
                normalized_headers
            ):
                value = (
                    raw_row[index]
                    if index
                    < len(raw_row)
                    else None
                )

                row[header] = (
                    self._normalize_cell_value(
                        value
                    )
                )

            if self._row_is_empty(
                row
            ):
                continue

            output.append(
                row
            )

            if (
                safe_limit is not None
                and len(output)
                >= safe_limit
            ):
                break

        logger.info(
            "Google Sheets rows loaded. "
            "spreadsheet_id=%s "
            "sheet_name=%s "
            "row_count=%s",
            spreadsheet_id,
            normalized_sheet_name,
            len(output),
        )

        return output

    # ---------------------------------------------------------
    # Spreadsheet URL / ID handling
    # ---------------------------------------------------------

    @classmethod
    def _normalize_spreadsheet_id(
        cls,
        value: str,
    ) -> str:
        raw = str(
            value or ""
        ).strip()

        if not raw:
            raise ValueError(
                "spreadsheet_id is required."
            )

        if raw.startswith(
            "http://"
        ) or raw.startswith(
            "https://"
        ):
            spreadsheet_id = (
                cls._extract_id_from_url(
                    raw
                )
            )
        else:
            spreadsheet_id = raw

        if not re.fullmatch(
            r"[A-Za-z0-9_-]{20,}",
            spreadsheet_id,
        ):
            raise ValueError(
                "Invalid Google Sheets "
                "spreadsheet identifier."
            )

        return spreadsheet_id

    @staticmethod
    def _extract_id_from_url(
        url: str,
    ) -> str:
        parsed = urlparse(
            url
        )

        path = parsed.path

        match = re.search(
            r"/spreadsheets/d/"
            r"([A-Za-z0-9_-]+)",
            path,
        )

        if not match:
            raise ValueError(
                "Unable to extract spreadsheet ID "
                "from Google Sheets URL."
            )

        return match.group(1)

    # ---------------------------------------------------------
    # Google API helpers
    # ---------------------------------------------------------

    def _get_spreadsheet_metadata(
        self,
        *,
        spreadsheet_id: str,
    ) -> Dict[str, Any]:
        url = (
            f"{self.SHEETS_API_BASE}/"
            f"{spreadsheet_id}"
        )

        response = requests.get(
            url,
            headers=self._headers(),
            params={
                "includeGridData": "false",
            },
            timeout=self.timeout_seconds,
        )

        self._raise_for_google_error(
            response=response,
            action=(
                "load spreadsheet metadata"
            ),
        )

        return response.json()

    @staticmethod
    def _raise_for_google_error(
        *,
        response: requests.Response,
        action: str,
    ) -> None:
        if response.ok:
            return

        detail = ""

        try:
            body = (
                response.json()
            )

            detail = str(
                body
                .get(
                    "error",
                    {}
                )
                .get(
                    "message",
                    ""
                )
            ).strip()

        except Exception:
            detail = (
                response.text
                or ""
            ).strip()

        if response.status_code == 401:
            raise RuntimeError(
                "Google Sheets authentication "
                "failed or token expired."
            )

        if response.status_code == 403:
            raise RuntimeError(
                "Google Sheets access denied. "
                "Verify the user/service account "
                "has permission to the spreadsheet."
            )

        if response.status_code == 404:
            raise RuntimeError(
                "Google Sheet was not found "
                "or is not accessible."
            )

        raise RuntimeError(
            f"Google Sheets {action} failed "
            f"with HTTP "
            f"{response.status_code}: "
            f"{detail or 'Unknown Google API error'}"
        )

    # ---------------------------------------------------------
    # Header normalization
    # ---------------------------------------------------------

    @classmethod
    def _normalize_headers(
        cls,
        headers: List[
            Any
        ],
    ) -> List[str]:
        output: List[
            str
        ] = []

        seen: Dict[
            str,
            int
        ] = {}

        for index, raw_header in enumerate(
            headers
        ):
            normalized = (
                cls._normalize_header(
                    raw_header,
                    fallback=(
                        f"column_{index + 1}"
                    ),
                )
            )

            count = (
                seen.get(
                    normalized,
                    0,
                )
                + 1
            )

            seen[
                normalized
            ] = count

            if count > 1:
                normalized = (
                    f"{normalized}_"
                    f"{count}"
                )

            output.append(
                normalized
            )

        return output

    @staticmethod
    def _normalize_header(
        value: Any,
        *,
        fallback: str,
    ) -> str:
        text = str(
            value
            if value is not None
            else ""
        ).strip()

        text = text.lower()

        text = re.sub(
            r"[^a-z0-9]+",
            "_",
            text,
        )

        text = re.sub(
            r"_+",
            "_",
            text,
        )

        text = text.strip(
            "_"
        )

        if not text:
            return fallback

        if text[0].isdigit():
            text = (
                f"field_{text}"
            )

        return text

    # ---------------------------------------------------------
    # Cell normalization
    # ---------------------------------------------------------

    @staticmethod
    def _normalize_cell_value(
        value: Any,
    ) -> Any:
        if value is None:
            return None

        if isinstance(
            value,
            str,
        ):
            stripped = (
                value.strip()
            )

            return (
                stripped
                if stripped
                else None
            )

        return value

    @staticmethod
    def _row_is_empty(
        row: Dict[
            str,
            Any
        ],
    ) -> bool:
        return all(
            value is None
            or (
                isinstance(
                    value,
                    str,
                )
                and not value.strip()
            )
            for value
            in row.values()
        )

    @staticmethod
    def _escape_sheet_name(
        sheet_name: str,
    ) -> str:
        return (
            sheet_name.replace(
                "'",
                "''",
            )
        )