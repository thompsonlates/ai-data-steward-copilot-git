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
        "https://www.googleapis.com/auth/spreadsheets"
    )

    BIGQUERY_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/bigquery.readonly"
)

    GOOGLE_SHEETS_SCOPES = [
    SHEETS_SCOPE,
    BIGQUERY_READONLY_SCOPE,
]

    # Formulas that can return multi-cell / spilled output. Governed write-back
    # must not write physical values into a worksheet containing these because
    # doing so can break the formula expansion and cause #REF! errors.
    _SPILL_FORMULA_PATTERN = re.compile(
        r"^\s*=\s*(?:ARRAYFORMULA|QUERY|IMPORTRANGE|FILTER|SORT|SORTN|UNIQUE|"
        r"SEQUENCE|TRANSPOSE|TOCOL|TOROW|WRAPROWS|WRAPCOLS|CHOOSECOLS|"
        r"CHOOSEROWS|VSTACK|HSTACK|MAKEARRAY|MAP|BYROW|BYCOL|SCAN|REDUCE)\s*\(",
        flags=re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        credentials: Any = None,
        credentials_file: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> None:

        logger.warning(
        "GOOGLE SHEETS CONNECTOR INIT "
        "credentials_provided=%s "
        "credentials_file_provided=%s",
        credentials is not None,
        bool(credentials_file),
    )
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

        self.credentials = (credentials
        if credentials is not None
        else self._load_credentials()
    )

    def _load_credentials(self):
        if self.credentials_file:
            return (
                service_account.Credentials
                .from_service_account_file(
                    self.credentials_file,
                    scopes=self.GOOGLE_SHEETS_SCOPES,
                )
            )
        

        credentials, _ = google.auth.default(
            scopes=self.GOOGLE_SHEETS_SCOPES,
        )

        return credentials

    def _get_access_token(self) -> str:
        if not self.credentials.valid:
            request = google.auth.transport.requests.Request()

            self.credentials.refresh(request)
            
            logger.warning(
            "GOOGLE SHEETS TOKEN DEBUG "
            "credential_type=%s "
            "configured_scopes=%s "
            "requires_scopes=%s "
            "valid=%s",
            type(self.credentials).__name__,
            getattr(self.credentials, "scopes", None),
            getattr(self.credentials, "requires_scopes", None),
            self.credentials.valid,
        )



        token = self.credentials.token

        if not token:
            raise RuntimeError(
                "Unable to obtain Google Sheets access token."
            )

        return token

    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
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

                    "sheet_type": (
                        properties.get(
                            "sheetType"
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

    def _read_data_source_rows(
            self,
            *,
            spreadsheet_id: str,
            sheet_name: str,
            metadata: Dict[str, Any],
            header_row: int = 1,
            limit: Optional[int] = None,
        ) -> List[Dict[str, Any]]:
            """
            Read materialized values from a Google Sheets
            DATA_SOURCE worksheet without executing the
            underlying BigQuery query.

            Uses spreadsheets.getByDataFilter with a GridRange
            keyed by sheetId, avoiding A1 range parsing for
            Connected Sheets.
            """

            normalized_sheet_name = str(
                sheet_name or ""
            ).strip()

            if not normalized_sheet_name:
                raise ValueError(
                    "sheet_name is required."
                )

            selected_sheet = None

            for sheet in metadata.get(
                "sheets",
                [],
            ):
                properties = sheet.get(
                    "properties",
                    {},
                )

                if (
                    str(
                        properties.get(
                            "title"
                        )
                        or ""
                    ).strip()
                    == normalized_sheet_name
                ):
                    selected_sheet = sheet
                    break

            if selected_sheet is None:
                raise ValueError(
                    f"Worksheet not found: "
                    f"{normalized_sheet_name}"
                )

            properties = selected_sheet.get(
                "properties",
                {},
            )

            sheet_id = properties.get(
                "sheetId"
            )

            if sheet_id is None:
                raise ValueError(
                    "DATA_SOURCE worksheet "
                    "does not contain a sheetId."
                )

            data_source_properties = (
                properties.get(
                    "dataSourceSheetProperties",
                    {},
                )
            )

            data_source_columns = (
                data_source_properties.get(
                    "columns",
                    [],
                )
                or []
            )

            headers: List[str] = []

            for index, column in enumerate(
                data_source_columns
            ):
                reference = column.get(
                    "reference",
                    {},
                )

                column_name = str(
                    reference.get(
                        "name"
                    )
                    or ""
                ).strip()

                if not column_name:
                    column_name = (
                        f"column_{index + 1}"
                    )

                headers.append(
                    column_name
                )

            if not headers:
                raise ValueError(
                    "Connected Sheet does not "
                    "contain data source columns."
                )

            safe_header_row = max(
                1,
                int(header_row),
            )

            safe_limit: Optional[int] = None

            if limit is not None:
                safe_limit = max(
                    1,
                    min(
                        int(limit),
                        100_000,
                    ),
                )

            # Connected Sheets metadata already gives us
            # authoritative column names, so we do not
            # need to treat the first returned row as headers.
            start_row_index = max(
                safe_header_row - 1,
                0,
            )

            grid_range: Dict[str, Any] = {
                "sheetId": int(
                    sheet_id
                ),
                "startRowIndex": (
                    start_row_index
                ),
                "startColumnIndex": 0,
                "endColumnIndex": len(
                    headers
                ),
            }

            if safe_limit is not None:
                grid_range[
                    "endRowIndex"
                ] = (
                    start_row_index
                    + safe_limit
                )

            end_row = (
                start_row_index
                + safe_limit
                if safe_limit is not None
                else start_row_index + 500
            )

            end_column_letter = self._column_index_to_letter(
                len(headers)
            )

            escaped_sheet_name = self._escape_sheet_name(
                normalized_sheet_name
            )

            range_expression = (
                f"{escaped_sheet_name}"
                f"!A{safe_header_row}:"
                f"{end_column_letter}{end_row}"
            )

            url = (
                f"{self.SHEETS_API_BASE}/"
                f"{spreadsheet_id}"
            )

            logger.info(
                "Reading Google Sheets DATA_SOURCE "
                "materialized grid with spreadsheets.get. "
                "spreadsheet_id=%s "
                "sheet_name=%s "
                "range=%s "
                "columns=%s "
                "limit=%s",
                spreadsheet_id,
                normalized_sheet_name,
                range_expression,
                len(headers),
                safe_limit,
            )

            response = requests.get(
                url,
                headers=self._headers(),
                params={
                    "ranges": range_expression,
                    "includeGridData": "true",
                },
                timeout=self.timeout_seconds,
            )


            self._raise_for_google_error(
                response=response,
                action=(
                    "read Connected Sheet "
                    "materialized rows"
                ),
            )

            payload = response.json()

            returned_sheets = (
                payload.get(
                    "sheets",
                    [],
                )
                or []
            )

            if not returned_sheets:
                return []

            grid_data_blocks = (
                returned_sheets[0].get(
                    "data",
                    [],
                )
                or []
            )

            if not grid_data_blocks:
                return []

            rows: List[
                Dict[str, Any]
            ] = []

            for grid_data in grid_data_blocks:
                row_data = (
                    grid_data.get(
                        "rowData",
                        [],
                    )
                    or []
                )

                for row in row_data:
                    values = (
                        row.get(
                            "values",
                            [],
                        )
                        or []
                    )

                    record: Dict[
                        str,
                        Any
                    ] = {}

                    for index, header in enumerate(
                        headers
                    ):
                        cell = (
                            values[index]
                            if index < len(values)
                            else {}
                        )

                        effective_value = (
                            cell.get(
                                "effectiveValue",
                                {},
                            )
                            or {}
                        )

                        value: Any = None

                        if (
                            "stringValue"
                            in effective_value
                        ):
                            value = (
                                effective_value[
                                    "stringValue"
                                ]
                            )

                        elif (
                            "numberValue"
                            in effective_value
                        ):
                            value = (
                                effective_value[
                                    "numberValue"
                                ]
                            )

                        elif (
                            "boolValue"
                            in effective_value
                        ):
                            value = (
                                effective_value[
                                    "boolValue"
                                ]
                            )

                        elif (
                            "errorValue"
                            in effective_value
                        ):
                            value = None

                        else:
                            formatted_value = (
                                cell.get(
                                    "formattedValue"
                                )
                            )

                            if (
                                formatted_value
                                is not None
                            ):
                                value = (
                                    formatted_value
                                )

                        record[
                            header
                        ] = (
                            self._normalize_cell_value(
                                value
                            )
                        )

                    if not self._row_is_empty(
                        record
                    ):
                        rows.append(
                            record
                        )

                    if (
                        safe_limit is not None
                        and len(rows)
                        >= safe_limit
                    ):
                        return rows

            return rows

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

        metadata = (
            self._get_spreadsheet_metadata(
                spreadsheet_id=spreadsheet_id
            )
        )

        selected_sheet = None

        for sheet in metadata.get(
            "sheets",
            [],
        ):
            properties = sheet.get(
                "properties",
                {},
            )

            if (
                str(
                    properties.get(
                        "title"
                    )
                    or ""
                ).strip()
                == normalized_sheet_name
            ):
                selected_sheet = sheet
                break

        if selected_sheet is None:
            raise ValueError(
                f"Worksheet not found: "
                f"{normalized_sheet_name}"
            )

        sheet_type = str(
            selected_sheet
            .get(
                "properties",
                {},
            )
            .get(
                "sheetType"
            )
            or "GRID"
        ).strip().upper()

        if sheet_type == "DATA_SOURCE":
            return self._read_data_source_rows(
                spreadsheet_id=spreadsheet_id,
                sheet_name=normalized_sheet_name,
                metadata=metadata,
                header_row=header_row,
                limit=limit,
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

        escaped_sheet_name = (
        self._escape_sheet_name(
            normalized_sheet_name
        )
    )

        range_expression = (
        f"{escaped_sheet_name}"
        f"!A{range_start}:Z100000"
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

        return output

    def read_column_cells(
        self,
        *,
        spreadsheet_id: str,
        sheet_name: str,
        column_name: str,
        header_row: int = 1,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Read raw cell values for one governed worksheet column.

        Returns:
            [
                {
                    "row_number": 2,
                    "column_name": "product_variant",
                    "value": " 3   oz ",
                },
                ...
            ]

        Important:
        * Only standard GRID worksheets are supported.
        * Raw string whitespace is intentionally preserved.
        * Values are not normalized before governed remediation.
        """

        normalized_spreadsheet_id = (
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

        normalized_column_name = (
            self._normalize_header(
                column_name,
                fallback="",
            )
        )

        if not normalized_column_name:
            raise ValueError(
                "column_name is required."
            )

        safe_header_row = max(
            1,
            int(header_row),
        )

        safe_limit = (
            max(
                1,
                min(int(limit), 100_000),
            )
            if limit is not None
            else 100_000
        )

        # ---------------------------------------------------------
        # Confirm worksheet exists and is writable GRID
        # ---------------------------------------------------------

        metadata = self._get_spreadsheet_metadata(
            spreadsheet_id=normalized_spreadsheet_id
        )

        selected_sheet = None

        for sheet in metadata.get("sheets", []) or []:
            properties = sheet.get(
                "properties",
                {},
            )

            if (
                str(
                    properties.get("title")
                    or ""
                ).strip()
                == normalized_sheet_name
            ):
                selected_sheet = sheet
                break

        if selected_sheet is None:
            raise ValueError(
                f"Worksheet not found: {normalized_sheet_name}"
            )

        sheet_type = str(
            selected_sheet
            .get("properties", {})
            .get("sheetType")
            or "GRID"
        ).strip().upper()

        if sheet_type != "GRID":
            raise ValueError(
                "Governed Google Sheets remediation may read/write "
                "only standard GRID worksheets. Connected Sheets / "
                "DATA_SOURCE worksheets remain read-only."
            )

        # ---------------------------------------------------------
        # Resolve governed column from header
        # ---------------------------------------------------------

        header_map = self._get_header_column_map(
            spreadsheet_id=normalized_spreadsheet_id,
            sheet_name=normalized_sheet_name,
            header_row=safe_header_row,
        )

        column_index = header_map.get(
            normalized_column_name
        )

        if column_index is None:
            raise ValueError(
                "Google Sheets remediation target column was "
                f"not found: {normalized_column_name}"
            )

        column_letter = (
            self._column_index_to_letter(
                column_index + 1
            )
        )

        first_data_row = safe_header_row + 1
        last_data_row = (
            first_data_row
            + safe_limit
            - 1
        )

        escaped_sheet_name = (
            self._escape_sheet_name(
                normalized_sheet_name
            )
        )

        range_expression = (
            f"{escaped_sheet_name}!"
            f"{column_letter}{first_data_row}:"
            f"{column_letter}{last_data_row}"
        )

        url = (
            f"{self.SHEETS_API_BASE}/"
            f"{normalized_spreadsheet_id}/values/"
            f"{range_expression}"
        )

        logger.info(
            "Reading governed Google Sheets remediation column. "
            "spreadsheet_id=%s sheet_name=%s column_name=%s "
            "range=%s",
            normalized_spreadsheet_id,
            normalized_sheet_name,
            normalized_column_name,
            range_expression,
        )

        response = requests.get(
            url,
            headers=self._headers(),
            params={
                "majorDimension": "ROWS",
                "valueRenderOption": "UNFORMATTED_VALUE",
                "dateTimeRenderOption": "FORMATTED_STRING",
            },
            timeout=self.timeout_seconds,
        )

        self._raise_for_google_error(
            response=response,
            action="read governed remediation column",
        )

        payload = response.json()

        values = (
            payload.get("values")
            or []
        )

        output: List[Dict[str, Any]] = []

        for offset, row_values in enumerate(values):
            row_number = (
                first_data_row
                + offset
            )

            value = (
                row_values[0]
                if row_values
                else None
            )

            output.append(
                {
                    "row_number": row_number,
                    "column_name": normalized_column_name,
                    "value": value,
                }
            )

        return output

    def inspect_governed_write_safety(
        self,
        *,
        spreadsheet_id: str,
        sheet_name: str,
        updates: List[Dict[str, Any]],
        header_row: int = 1,
        max_updates: int = 10000,
    ) -> Dict[str, Any]:
        """
        Preflight a governed Google Sheets write.

        Safety rules:
          * only standard GRID worksheets are eligible;
          * exact target cells containing formulas are not writable;
          * worksheets containing known spill/array formulas are blocked,
            because writing a physical value into a spill range can collapse
            the formula output and produce #REF! errors;
          * no source values or formula text are logged or returned.

        This check is intentionally conservative. A steward-approved
        remediation should fail closed rather than damage formula-generated
        worksheet output.
        """
        normalized_spreadsheet_id = (
            self._normalize_spreadsheet_id(spreadsheet_id)
        )
        normalized_sheet_name = str(sheet_name or "").strip()
        if not normalized_sheet_name:
            raise ValueError("sheet_name is required.")
        safe_header_row = max(1, int(header_row))
        if max_updates < 1 or max_updates > 10000:
            raise ValueError("max_updates must be between 1 and 10000.")
        if not isinstance(updates, list):
            raise ValueError("updates must be a list.")
        if len(updates) > max_updates:
            raise ValueError(
                f"Too many cell updates. Maximum allowed is {max_updates}."
            )

        metadata = self._get_spreadsheet_metadata(
            spreadsheet_id=normalized_spreadsheet_id
        )
        selected_sheet = None
        for sheet in metadata.get("sheets", []) or []:
            properties = sheet.get("properties", {})
            if str(properties.get("title") or "").strip() == normalized_sheet_name:
                selected_sheet = sheet
                break
        if selected_sheet is None:
            raise ValueError(f"Worksheet not found: {normalized_sheet_name}")

        sheet_type = str(
            selected_sheet.get("properties", {}).get("sheetType") or "GRID"
        ).strip().upper()
        if sheet_type != "GRID":
            return {
                "safe_to_write": False,
                "reason": "NON_GRID_WORKSHEET",
                "sheet_type": sheet_type,
                "direct_formula_target_count": 0,
                "spill_formula_count": 0,
            }
        if not updates:
            return {
                "safe_to_write": True,
                "reason": "NO_UPDATES",
                "sheet_type": sheet_type,
                "direct_formula_target_count": 0,
                "spill_formula_count": 0,
            }

        header_map = self._get_header_column_map(
            spreadsheet_id=normalized_spreadsheet_id,
            sheet_name=normalized_sheet_name,
            header_row=safe_header_row,
        )
        escaped_sheet_name = self._escape_sheet_name(normalized_sheet_name)
        target_ranges: List[str] = []
        seen_cells: set[str] = set()
        for index, update in enumerate(updates, start=1):
            if not isinstance(update, dict):
                raise ValueError(f"Update {index} must be an object.")
            try:
                row_number = int(update.get("row_number"))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Update {index} has an invalid row_number."
                ) from exc
            if row_number <= safe_header_row:
                raise ValueError(
                    "Governed Google Sheets remediation cannot modify the header row or rows above it."
                )
            normalized_column_name = self._normalize_header(
                update.get("column_name"), fallback=""
            )
            if not normalized_column_name:
                raise ValueError(f"Update {index} is missing column_name.")
            column_index = header_map.get(normalized_column_name)
            if column_index is None:
                raise ValueError(
                    "Google Sheets remediation target column was not found: "
                    f"{normalized_column_name}"
                )
            column_letter = self._column_index_to_letter(column_index + 1)
            cell_range = f"{escaped_sheet_name}!{column_letter}{row_number}"
            if cell_range in seen_cells:
                raise ValueError(
                    "Duplicate Google Sheets cell update detected for "
                    f"{cell_range}."
                )
            seen_cells.add(cell_range)
            target_ranges.append(cell_range)

        direct_formula_target_count = 0
        batch_get_url = (
            f"{self.SHEETS_API_BASE}/{normalized_spreadsheet_id}/values:batchGet"
        )

        # Do not place thousands of individual A1 ranges into one GET request.
        # Large governed executions can exceed URL/query-string limits and make
        # the safety preflight fail before Google can evaluate the request.
        # Chunking keeps the exact-cell formula guard while remaining safe for
        # executions with thousands of approved updates.
        batch_size = 100

        for batch_start in range(
            0,
            len(target_ranges),
            batch_size,
        ):
            range_batch = target_ranges[
                batch_start:batch_start + batch_size
            ]

            batch_response = requests.get(
                batch_get_url,
                headers=self._headers(),
                params=[
                    ("majorDimension", "ROWS"),
                    ("valueRenderOption", "FORMULA"),
                    *[
                        ("ranges", cell_range)
                        for cell_range in range_batch
                    ],
                ],
                timeout=self.timeout_seconds,
            )

            self._raise_for_google_error(
                response=batch_response,
                action="preflight governed target cells",
            )

            for value_range in (
                batch_response.json().get("valueRanges")
                or []
            ):
                values = (
                    value_range.get("values")
                    or []
                )

                value = (
                    values[0][0]
                    if values
                    and values[0]
                    else None
                )

                if (
                    isinstance(value, str)
                    and value.lstrip().startswith("=")
                ):
                    direct_formula_target_count += 1

        spill_formula_count = 0

        # Read formula text from the worksheet through the Values API using
        # an explicit A1 range. Keep this as one request; unlike the target-cell
        # checks above, it does not carry thousands of query parameters.
        sheet_formula_range = (
            f"{escaped_sheet_name}!A1:Z100000"
        )

        sheet_formula_url = (
            f"{self.SHEETS_API_BASE}/"
            f"{normalized_spreadsheet_id}/values/"
            f"{sheet_formula_range}"
        )

        sheet_formula_response = requests.get(
            sheet_formula_url,
            headers=self._headers(),
            params={
                "majorDimension": "ROWS",
                "valueRenderOption": "FORMULA",
                "dateTimeRenderOption": "FORMATTED_STRING",
            },
            timeout=self.timeout_seconds,
        )

        self._raise_for_google_error(
            response=sheet_formula_response,
            action="preflight worksheet formulas",
        )

        for row_values in (
            sheet_formula_response.json().get("values")
            or []
        ):
            for value in row_values:
                if (
                    isinstance(value, str)
                    and self._SPILL_FORMULA_PATTERN.match(value)
                ):
                    spill_formula_count += 1

        safe_to_write = (
            direct_formula_target_count == 0 and spill_formula_count == 0
        )
        reason = "SAFE_TO_WRITE"
        if direct_formula_target_count:
            reason = "TARGET_CELL_CONTAINS_FORMULA"
        elif spill_formula_count:
            reason = "SPILL_FORMULA_PRESENT"

        logger.info(
            "Governed Google Sheets write preflight completed. "
            "spreadsheet_id=%s sheet_name=%s target_count=%s "
            "direct_formula_targets=%s spill_formulas=%s safe=%s",
            normalized_spreadsheet_id,
            normalized_sheet_name,
            len(target_ranges),
            direct_formula_target_count,
            spill_formula_count,
            safe_to_write,
        )
        return {
            "safe_to_write": safe_to_write,
            "reason": reason,
            "sheet_type": sheet_type,
            "target_count": len(target_ranges),
            "direct_formula_target_count": direct_formula_target_count,
            "spill_formula_count": spill_formula_count,
        }

    def apply_governed_cell_updates(
        self,
        *,
        spreadsheet_id: str,
        sheet_name: str,
        updates: List[Dict[str, Any]],
        header_row: int = 1,
        max_updates: int = 5000,
    ) -> Dict[str, Any]:
        """
        Apply an exact set of steward-approved cell replacements to a normal
        Google Sheets GRID worksheet.

        Expected update shape:
            {
                "row_number": 2,
                "column_name": "product_variant",
                "value": "3.4 OZ",
            }

        Governance / safety rules:
          * DATA_SOURCE / Connected Sheets worksheets are never writable here.
          * Header names must resolve to an existing worksheet column.
          * Only rows below the header row may be changed.
          * Formula execution is avoided by using valueInputOption=RAW.
          * No row insert/delete, sheet creation, formatting, formulas, or
            structural mutations are supported.
          * Source values are not logged by this connector.
        """

        normalized_spreadsheet_id = (
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

        if max_updates < 1 or max_updates > 10000:
            raise ValueError(
                "max_updates must be between 1 and 10000."
            )

        if not isinstance(updates, list):
            raise ValueError(
                "updates must be a list."
            )

        if not updates:
            return {
                "success": True,
                "spreadsheet_id": normalized_spreadsheet_id,
                "sheet_name": normalized_sheet_name,
                "requested_updates": 0,
                "updated_cells": 0,
                "message": "No Google Sheets cell updates were required.",
            }

        if len(updates) > max_updates:
            raise ValueError(
                f"Too many cell updates. Maximum allowed is {max_updates}."
            )

        safety = self.inspect_governed_write_safety(
            spreadsheet_id=normalized_spreadsheet_id,
            sheet_name=normalized_sheet_name,
            updates=updates,
            header_row=safe_header_row,
            max_updates=max_updates,
        )

        if not safety.get("safe_to_write"):
            reason = str(safety.get("reason") or "UNSAFE_WRITE_TARGET")
            if reason == "TARGET_CELL_CONTAINS_FORMULA":
                raise ValueError(
                    "Governed Google Sheets remediation is blocked because one or more target cells contain formulas. Execute against physical source cells instead."
                )
            if reason == "SPILL_FORMULA_PRESENT":
                raise ValueError(
                    "Governed Google Sheets remediation is blocked because the worksheet contains a spill/array formula. Writing physical values could collapse formula output and cause #REF! errors. Execute against the underlying physical source worksheet instead."
                )
            raise ValueError(
                "Governed Google Sheets remediation is blocked by write-safety preflight: "
                f"{reason}."
            )

        metadata = self._get_spreadsheet_metadata(
            spreadsheet_id=normalized_spreadsheet_id
        )

        selected_sheet = None

        for sheet in metadata.get(
            "sheets",
            [],
        ):
            properties = sheet.get(
                "properties",
                {},
            )

            if (
                str(
                    properties.get("title")
                    or ""
                ).strip()
                == normalized_sheet_name
            ):
                selected_sheet = sheet
                break

        if selected_sheet is None:
            raise ValueError(
                f"Worksheet not found: {normalized_sheet_name}"
            )

        sheet_type = str(
            selected_sheet
            .get(
                "properties",
                {},
            )
            .get(
                "sheetType"
            )
            or "GRID"
        ).strip().upper()

        if sheet_type != "GRID":
            raise ValueError(
                "Governed Google Sheets write-back is permitted only "
                "for standard GRID worksheets. Connected Sheets / "
                "DATA_SOURCE worksheets remain read-only."
            )

        header_map = self._get_header_column_map(
            spreadsheet_id=normalized_spreadsheet_id,
            sheet_name=normalized_sheet_name,
            header_row=safe_header_row,
        )

        escaped_sheet_name = self._escape_sheet_name(
            normalized_sheet_name
        )

        data: List[Dict[str, Any]] = []
        seen_cells: set[str] = set()

        for index, update in enumerate(
            updates,
            start=1,
        ):
            if not isinstance(update, dict):
                raise ValueError(
                    f"Update {index} must be an object."
                )

            row_number_raw = update.get(
                "row_number"
            )
            column_name_raw = update.get(
                "column_name"
            )

            try:
                row_number = int(
                    row_number_raw
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Update {index} has an invalid row_number."
                ) from exc

            if row_number <= safe_header_row:
                raise ValueError(
                    "Governed Google Sheets remediation cannot "
                    "modify the header row or rows above it."
                )

            normalized_column_name = (
                self._normalize_header(
                    column_name_raw,
                    fallback="",
                )
            )

            if not normalized_column_name:
                raise ValueError(
                    f"Update {index} is missing column_name."
                )

            column_index = header_map.get(
                normalized_column_name
            )

            if column_index is None:
                raise ValueError(
                    "Google Sheets remediation target column was "
                    f"not found: {normalized_column_name}"
                )

            column_letter = (
                self._column_index_to_letter(
                    column_index + 1
                )
            )

            cell_range = (
                f"{escaped_sheet_name}!"
                f"{column_letter}{row_number}"
            )

            if cell_range in seen_cells:
                raise ValueError(
                    "Duplicate Google Sheets cell update detected "
                    f"for {cell_range}."
                )

            seen_cells.add(
                cell_range
            )

            # RAW prevents values beginning with "=" from being interpreted
            # as formulas. This service performs exact value replacement only.
            data.append(
                {
                    "range": cell_range,
                    "majorDimension": "ROWS",
                    "values": [
                        [
                            update.get("value")
                        ]
                    ],
                }
            )

        url = (
            f"{self.SHEETS_API_BASE}/"
            f"{normalized_spreadsheet_id}/"
            "values:batchUpdate"
        )

        request_body = {
            "valueInputOption": "RAW",
            "includeValuesInResponse": False,
            "data": data,
        }

        logger.info(
            "Applying governed Google Sheets cell updates. "
            "spreadsheet_id=%s sheet_name=%s update_count=%s",
            normalized_spreadsheet_id,
            normalized_sheet_name,
            len(data),
        )

        response = requests.post(
            url,
            headers=self._headers(),
            json=request_body,
            timeout=self.timeout_seconds,
        )

        self._raise_for_google_error(
            response=response,
            action="apply governed cell updates",
        )

        payload = response.json()

        total_updated_cells = int(
            payload.get("totalUpdatedCells")
            or 0
        )

        return {
            "success": True,
            "spreadsheet_id": normalized_spreadsheet_id,
            "sheet_name": normalized_sheet_name,
            "requested_updates": len(data),
            "updated_cells": total_updated_cells,
            "updated_rows": int(
                payload.get("totalUpdatedRows")
                or 0
            ),
            "updated_columns": int(
                payload.get("totalUpdatedColumns")
                or 0
            ),
            "updated_sheets": int(
                payload.get("totalUpdatedSheets")
                or 0
            ),
            "message": (
                "Governed Google Sheets remediation applied successfully."
            ),
        }

    def _get_header_column_map(
        self,
        *,
        spreadsheet_id: str,
        sheet_name: str,
        header_row: int,
    ) -> Dict[str, int]:
        """
        Resolve normalized worksheet headers to zero-based column indexes.

        Duplicate normalized headers are rejected because write-back must
        resolve every target field to exactly one physical column.
        """

        escaped_sheet_name = self._escape_sheet_name(
            sheet_name
        )

        range_expression = (
            f"{escaped_sheet_name}!"
            f"{header_row}:{header_row}"
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
                "valueRenderOption": "UNFORMATTED_VALUE",
            },
            timeout=self.timeout_seconds,
        )

        self._raise_for_google_error(
            response=response,
            action="read worksheet headers for governed write-back",
        )

        payload = response.json()

        values = (
            payload.get("values")
            or []
        )

        if not values:
            raise ValueError(
                "Google Sheets worksheet header row is empty."
            )

        raw_headers = values[0]

        header_map: Dict[str, int] = {}

        for index, raw_header in enumerate(
            raw_headers
        ):
            normalized = self._normalize_header(
                raw_header,
                fallback="",
            )

            if not normalized:
                continue

            if normalized in header_map:
                raise ValueError(
                    "Governed Google Sheets write-back requires "
                    "unique normalized column headers. Duplicate "
                    f"header detected: {normalized}"
                )

            header_map[
                normalized
            ] = index

        if not header_map:
            raise ValueError(
                "Google Sheets worksheet does not contain usable headers."
            )

        return header_map

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
                "Google Sheets authentication failed "
                f"during {action}: "
                f"{detail or 'token expired or invalid'}"
            )

        if response.status_code == 403:
            raise RuntimeError(
                "Google Sheets request was rejected "
                f"with HTTP 403 during {action}: "
                f"{detail or 'Unknown Google API error'}"
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

    @staticmethod
    def _column_index_to_letter(
        column_count: int,
    ) -> str:
        if column_count < 1:
            raise ValueError(
                "column_count must be at least 1."
            )

        letters = ""
        number = column_count

        while number:
            number, remainder = divmod(
                number - 1,
                26,
            )
            letters = (
                chr(65 + remainder)
                + letters
            )

        return letters

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
        """
        Escape a Google Sheets worksheet title for A1 notation.

        Examples:
            Sheet1 -> 'Sheet1'
            Connected Sheet 1 -> 'Connected Sheet 1'
            Matt's Sheet -> 'Matt''s Sheet'
        """

        normalized = str(
            sheet_name or ""
        ).strip()

        if not normalized:
            raise ValueError(
                "sheet_name is required."
            )

        escaped = normalized.replace(
            "'",
            "''",
        )

        return f"'{escaped}'"