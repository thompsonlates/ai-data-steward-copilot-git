from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


MAX_CSV_BYTES = 10 * 1024 * 1024
DEFAULT_PREVIEW_ROWS = 25
MAX_PREVIEW_ROWS = 100


class CsvIngestionError(ValueError):
    pass


@dataclass
class CsvPreviewResult:
    organization_id: str
    file_name: str
    columns: List[str]
    rows: List[Dict[str, Any]]
    total_preview_rows: int
    delimiter: str
    encoding: str


class CsvIngestionService:
    @staticmethod
    def _require_organization_id(
        organization_id: str,
    ) -> str:
        value = str(
            organization_id or ""
        ).strip()

        if not value:
            raise CsvIngestionError(
                "organization_id is required"
            )

        return value

    @staticmethod
    def _normalize_file_name(
        file_name: Optional[str],
    ) -> str:
        value = str(
            file_name or "upload.csv"
        ).strip()

        if not value.lower().endswith(".csv"):
            raise CsvIngestionError(
                "Only .csv files are supported"
            )

        return value

    @staticmethod
    def _decode_csv(
        file_bytes: bytes,
    ) -> tuple[str, str]:
        if not file_bytes:
            raise CsvIngestionError(
                "CSV file is empty"
            )

        if len(file_bytes) > MAX_CSV_BYTES:
            raise CsvIngestionError(
                "CSV file exceeds the 10 MB limit"
            )

        encodings = (
            "utf-8-sig",
            "utf-8",
            "cp1252",
        )

        last_error: Optional[Exception] = None

        for encoding in encodings:
            try:
                return (
                    file_bytes.decode(encoding),
                    encoding,
                )
            except UnicodeDecodeError as exc:
                last_error = exc

        raise CsvIngestionError(
            "Unable to decode CSV file"
        ) from last_error

    @staticmethod
    def _detect_delimiter(
        text: str,
    ) -> str:
        sample = text[:8192]

        try:
            dialect = csv.Sniffer().sniff(
                sample,
                delimiters=",;\t|",
            )
            return dialect.delimiter
        except csv.Error:
            return ","

    @staticmethod
    def _normalize_header(
        value: Optional[str],
        index: int,
    ) -> str:
        header = str(
            value or ""
        ).strip()

        if not header:
            return f"column_{index + 1}"

        return header

    @staticmethod
    def _deduplicate_headers(
        headers: List[str],
    ) -> List[str]:
        seen: Dict[str, int] = {}
        result: List[str] = []

        for header in headers:
            base = header
            count = seen.get(base, 0)

            if count == 0:
                result.append(base)
            else:
                result.append(
                    f"{base}_{count + 1}"
                )

            seen[base] = count + 1

        return result

    @staticmethod
    def _normalize_value(
        value: Any,
    ) -> Optional[str]:
        if value is None:
            return None

        text = str(value).strip()

        if text == "":
            return None

        return text

    def preview_csv(
        self,
        *,
        organization_id: str,
        file_name: Optional[str],
        file_bytes: bytes,
        preview_rows: int = DEFAULT_PREVIEW_ROWS,
    ) -> CsvPreviewResult:
        organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        normalized_file_name = (
            self._normalize_file_name(
                file_name
            )
        )

        safe_preview_rows = max(
            1,
            min(
                int(preview_rows),
                MAX_PREVIEW_ROWS,
            ),
        )

        text, encoding = (
            self._decode_csv(
                file_bytes
            )
        )

        delimiter = (
            self._detect_delimiter(
                text
            )
        )

        reader = csv.reader(
            io.StringIO(text),
            delimiter=delimiter,
        )

        try:
            raw_headers = next(reader)
        except StopIteration as exc:
            raise CsvIngestionError(
                "CSV file does not contain a header row"
            ) from exc

        normalized_headers = [
            self._normalize_header(
                value,
                index,
            )
            for index, value
            in enumerate(raw_headers)
        ]

        headers = (
            self._deduplicate_headers(
                normalized_headers
            )
        )

        rows: List[Dict[str, Any]] = []

        for raw_row in reader:
            if len(rows) >= safe_preview_rows:
                break

            if not any(
                str(value or "").strip()
                for value in raw_row
            ):
                continue

            padded_row = list(raw_row)

            if len(padded_row) < len(headers):
                padded_row.extend(
                    [None] * (
                        len(headers)
                        - len(padded_row)
                    )
                )

            if len(padded_row) > len(headers):
                padded_row = padded_row[
                    : len(headers)
                ]

            row = {
                headers[index]:
                    self._normalize_value(
                        padded_row[index]
                    )
                for index
                in range(len(headers))
            }

            rows.append(row)

        return CsvPreviewResult(
            organization_id=organization_id,
            file_name=normalized_file_name,
            columns=headers,
            rows=rows,
            total_preview_rows=len(rows),
            delimiter=delimiter,
            encoding=encoding,
        )

    def parse_csv_records(
        self,
        *,
        organization_id: str,
        file_name: Optional[str],
        file_bytes: bytes,
    ) -> Dict[str, Any]:
        organization_id = (
            self._require_organization_id(
                organization_id
            )
        )

        normalized_file_name = (
            self._normalize_file_name(
                file_name
            )
        )

        text, encoding = (
            self._decode_csv(
                file_bytes
            )
        )

        delimiter = (
            self._detect_delimiter(
                text
            )
        )

        reader = csv.reader(
            io.StringIO(text),
            delimiter=delimiter,
        )

        try:
            raw_headers = next(reader)
        except StopIteration as exc:
            raise CsvIngestionError(
                "CSV file does not contain a header row"
            ) from exc

        normalized_headers = [
            self._normalize_header(
                value,
                index,
            )
            for index, value
            in enumerate(raw_headers)
        ]

        headers = (
            self._deduplicate_headers(
                normalized_headers
            )
        )

        records: List[Dict[str, Any]] = []

        for raw_row in reader:
            if not any(
                str(value or "").strip()
                for value in raw_row
            ):
                continue

            padded_row = list(raw_row)

            if len(padded_row) < len(headers):
                padded_row.extend(
                    [None] * (
                        len(headers)
                        - len(padded_row)
                    )
                )

            if len(padded_row) > len(headers):
                padded_row = padded_row[
                    : len(headers)
                ]

            record = {
                headers[index]:
                    self._normalize_value(
                        padded_row[index]
                    )
                for index
                in range(len(headers))
            }

            records.append(record)

        return {
            "organization_id":
                organization_id,
            "file_name":
                normalized_file_name,
            "columns":
                headers,
            "records":
                records,
            "record_count":
                len(records),
            "delimiter":
                delimiter,
            "encoding":
                encoding,
        }