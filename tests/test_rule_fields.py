"""RULE_FIELDS must only name fields that exist in the extraction schema.

The report step will use this mapping to attach exact quotes to findings.
"""
from src import schema
from src.rules import RULE_FIELDS

EXPECTED_RULE_IDS = {
    "R1", "R2", "R3", "R4", "R5",
    "H1", "H2", "H3", "H4", "H5", "H6", "H7",
    "N1", "N2", "M",
}


def test_every_mapped_field_exists_in_schema():
    for rule_id, fields in RULE_FIELDS.items():
        for name in fields:
            assert name in schema.FIELD_NAMES, (
                f"{rule_id} maps unknown schema field {name!r}"
            )


def test_every_rule_has_at_least_one_field():
    assert set(RULE_FIELDS) == EXPECTED_RULE_IDS
    for rule_id, fields in RULE_FIELDS.items():
        assert fields, f"{rule_id} maps no schema fields"
