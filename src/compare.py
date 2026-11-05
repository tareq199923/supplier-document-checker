"""Purchase request comparison (F005): plain code, no LLM.

Finds the purchase request line a supplier quotation belongs to and hands the
rule engine two numbers: the maximum unit price and the requested quantity.

Matching is strict on purpose. PR number and item letter pick the row, then the
description and the unit must say the same thing (case and extra spaces are
ignored, nothing else). Anything less sure is a problem for a person to look at.
A failed match returns NO row, so the rules never see another item's price.

The CSV has a byte order mark, so it is read with encoding="utf-8-sig".
"""
from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from src.parsing import to_decimal
from src.schema import Extraction

Key = tuple[str, str]

COL_PR = "PR number"
COL_ITEM = "Item"
COL_DESCRIPTION = "Description"
COL_UNIT = "Unit"
COL_QUANTITY = "Quantity requested"
COL_MAX_PRICE = "Maximum unit price (BDT)"
REQUIRED_COLUMNS = (
    COL_PR,
    COL_ITEM,
    COL_DESCRIPTION,
    COL_UNIT,
    COL_QUANTITY,
    COL_MAX_PRICE,
)

_SHOWN_CHARS = 60


class PurchaseRequestError(Exception):
    """The purchase request file cannot be used. The message says why."""


@dataclass(frozen=True)
class PurchaseRequestRow:
    pr_number: str
    item: str
    description: str
    unit: str
    quantity: Decimal
    max_unit_price: Decimal


PurchaseRequestTable = Mapping[Key, PurchaseRequestRow]


@dataclass(frozen=True)
class MatchResult:
    """row is set only when the match is fully confirmed; otherwise problems say why.

    notes are remarks that do not block the match (for example "VAT included").
    """

    row: PurchaseRequestRow | None = None
    problems: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.row is not None

    @property
    def max_price(self) -> Decimal | None:
        return None if self.row is None else self.row.max_unit_price

    @property
    def requested_qty(self) -> Decimal | None:
        return None if self.row is None else self.row.quantity


# ---- small text helpers ---------------------------------------------------------
def _clean(text: str | None) -> str | None:
    """Collapse whitespace; blank or missing becomes None."""
    if text is None:
        return None
    cleaned = " ".join(text.split())
    return cleaned or None


def _same(left: str, right: str) -> bool:
    return left.casefold() == right.casefold()


def _shown(text: str) -> str:
    if len(text) <= _SHOWN_CHARS:
        return text
    return text[: _SHOWN_CHARS - 3] + "..."


# ---- loader -----------------------------------------------------------------------
def _positive(text: str, column: str, line: int) -> Decimal:
    number = to_decimal(text)
    if number is None or number <= 0:
        raise PurchaseRequestError(
            f"Line {line}: '{column}' must be a positive number, got '{_shown(text)}'."
        )
    return number


def load_purchase_requests(path: str | Path) -> dict[Key, PurchaseRequestRow]:
    """Read the purchase request CSV into {(PR number, item letter): row}.

    Keys are upper-cased with spaces collapsed. Any unusable file raises
    PurchaseRequestError with a message that says what is wrong.
    """
    path = Path(path)
    table: dict[Key, PurchaseRequestRow] = {}
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header is None:
                raise PurchaseRequestError(f"{path.name} is empty.")
            names = [" ".join(name.split()) for name in header]
            missing = [column for column in REQUIRED_COLUMNS if column not in names]
            if missing:
                raise PurchaseRequestError(
                    f"{path.name} is missing column(s): {', '.join(missing)}."
                )
            index = {column: names.index(column) for column in REQUIRED_COLUMNS}

            for cells in reader:
                if not any(cell.strip() for cell in cells):
                    continue  # a blank line
                line = reader.line_num
                if len(cells) < len(names):
                    raise PurchaseRequestError(
                        f"Line {line}: expected {len(names)} columns, found {len(cells)}."
                    )
                row = _read_row(cells, index, line)
                key = (row.pr_number.upper(), row.item.upper())
                if key in table:
                    raise PurchaseRequestError(
                        f"Line {line}: PR {row.pr_number} item {row.item} appears twice."
                    )
                table[(key[0], key[1])] = row
    except FileNotFoundError:
        # Name the file only. The folder is a private detail of the person's machine.
        raise PurchaseRequestError(
            f"Purchase request file not found: {path.name}"
        ) from None
    except OSError as error:
        # An OS error text embeds the full path and an error number: show the type only.
        raise PurchaseRequestError(
            f"Purchase request file could not be read: {type(error).__name__}"
        ) from None
    except (UnicodeDecodeError, csv.Error) as error:
        # These texts carry no path; the person needs them to fix the file.
        raise PurchaseRequestError(
            f"Purchase request file could not be read: {error}"
        ) from None

    if not table:
        raise PurchaseRequestError(f"{path.name} has no purchase request lines.")
    return table


def _read_row(cells: list[str], index: dict[str, int], line: int) -> PurchaseRequestRow:
    def text(column: str) -> str:
        value = _clean(cells[index[column]])
        if value is None:
            raise PurchaseRequestError(f"Line {line}: '{column}' is blank.")
        return value

    return PurchaseRequestRow(
        pr_number=text(COL_PR).upper(),
        item=text(COL_ITEM).upper(),
        description=text(COL_DESCRIPTION),
        unit=text(COL_UNIT),
        quantity=_positive(text(COL_QUANTITY), COL_QUANTITY, line),
        max_unit_price=_positive(text(COL_MAX_PRICE), COL_MAX_PRICE, line),
    )


# ---- matcher ----------------------------------------------------------------------
def match_item(extraction: Extraction, table: PurchaseRequestTable) -> MatchResult:
    """Find the purchase request line for one extracted quotation line. Never raises."""
    pr = _clean(extraction.pr_reference.value)
    item = _clean(extraction.item_code.value)
    description = _clean(extraction.description.value)
    unit = _clean(extraction.unit.value)

    problems: list[str] = []
    for name, value in (
        ("pr_reference", pr),
        ("item_code", item),
        ("description", description),
        ("unit", unit),
    ):
        if value is None:
            problems.append(
                f"{name} is missing, so the quotation cannot be matched "
                "to the purchase request."
            )

    row: PurchaseRequestRow | None = None
    if pr is not None and item is not None:
        row = table.get((pr.upper(), item.upper()))
        if row is None:
            problems.append(
                f"Purchase request {_shown(pr)} item {_shown(item)} was not found "
                "in the purchase request file."
            )

    if row is not None:
        if description is not None and not _same(description, row.description):
            problems.append(
                f"description '{_shown(description)}' does not match the purchase "
                f"request description '{row.description}' for item {row.item}."
            )
        if unit is not None and not _same(unit, row.unit):
            problems.append(
                f"unit '{_shown(unit)}' does not match the purchase request unit "
                f"'{row.unit}' for item {row.item}."
            )

    notes: list[str] = []
    includes_vat = _clean(extraction.price_includes_vat.value)
    if includes_vat is not None and includes_vat.casefold() == "true":
        notes.append(
            "The unit price includes VAT, so it may not be comparable to the "
            "maximum unit price in the purchase request."
        )

    return MatchResult(
        row=row if not problems else None,
        problems=tuple(problems),
        notes=tuple(notes),
    )
