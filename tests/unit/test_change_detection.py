"""What gets landed: change detection (land_changed) and the helpers that
make paging stable. Findings 1 and 2 rest on these."""

import pandas as pd
import pytest

import config
from extractors.base import (arcgis_order, incident_key_sql, land_changed, layer_object_id,
                            socrata_order)


def moco(inc, code, crime, **extra):
    return {"incident_id": inc, "offence_code": code, "crimename2": crime,
            "start_date": "2026-11-01T10:00:00.000", **extra}


def landed_rows(path) -> list[dict]:
    return [] if path is None else pd.read_parquet(path).to_dict("records")


# ---------------------------------------------------------------- helpers

@pytest.mark.parametrize("source, expected", [
    ("moco", "NULLIF(TRIM(\"incident_id\"), '')"),
    ("dc", "NULLIF(TRIM(\"CCN\"), '')"),
    ("pwc", "COALESCE(NULLIF(TRIM(\"CaseNo\"), ''), NULLIF(TRIM(\"InstanceID\"), ''))"),
])
def test_incident_key_matches_the_transform(source, expected):
    assert incident_key_sql(source) == expected
    assert expected.replace('"', "") in open(config.PROJECT_ROOT / "sql" / "transform.sql").read()


def test_paging_sorts_end_on_a_unique_id():
    assert socrata_order(":updated_at") == ":updated_at ASC, :id ASC"
    assert arcgis_order("REPORT_DAT", "OBJECTID") == "REPORT_DAT ASC, OBJECTID ASC"
    assert arcgis_order(None, "ObjectID") == "ObjectID ASC"


@pytest.mark.parametrize("layer, expected", [
    ({"objectIdField": "ObjectID"}, "ObjectID"),
    ({"fields": [{"name": "FID", "type": "esriFieldTypeOID"}]}, "FID"),
    ({}, "OBJECTID"),
])
def test_object_id_field_discovery(layer, expected):
    assert layer_object_id(layer) == expected


# --------------------------------------------------- incident versioning

def test_first_sight_lands_everything(paths):
    rows = landed_rows(land_changed([moco("1", "A", "Robbery"), moco("2", "A", "Theft")], "moco"))
    assert len(rows) == 2


def test_an_identical_re_pull_lands_nothing(paths):
    batch = [moco("1", "A", "Robbery"), moco("1", "B", "Assault")]
    land_changed(batch, "moco")
    assert land_changed(batch, "moco") is None


def test_a_correction_lands_the_whole_incident_and_nothing_else(paths):
    land_changed([moco("1", "A", "Robbery"), moco("1", "B", "Assault"), moco("2", "A", "Theft")], "moco")
    rows = landed_rows(land_changed(
        [moco("1", "A", "Robbery"), moco("1", "B", "Homicide"), moco("2", "A", "Theft")], "moco"))
    assert {r["incident_id"] for r in rows} == {"1"}
    assert sorted(r["crimename2"] for r in rows) == ["Homicide", "Robbery"]


def test_a_partial_re_pull_still_lands_the_incident_as_fetched(paths):
    """land_changed compares what it is given; completing an incident is
    the extractor's job (Montgomery re-fetches every touched incident)."""
    land_changed([moco("1", "A", "Robbery"), moco("1", "B", "Assault")], "moco")
    rows = landed_rows(land_changed([moco("1", "B", "Assault")], "moco"))
    assert [r["offence_code"] for r in rows] == ["B"]


def test_a_revert_lands_again(paths):
    land_changed([moco("1", "A", "Robbery")], "moco")
    land_changed([moco("1", "A", "Homicide")], "moco")
    rows = landed_rows(land_changed([moco("1", "A", "Robbery")], "moco"))
    assert [r["crimename2"] for r in rows] == ["Robbery"]


def test_duplicate_rows_in_a_batch_land_once(paths):
    rows = landed_rows(land_changed([moco("1", "A", "Theft")] * 3, "moco"))
    assert len(rows) == 1


def test_padded_keys_are_one_incident(paths):
    """Prince William pads CaseNo; trimmed, both spellings are one incident."""
    rec = {"CaseNo": "PD1", "IBRCode": "THEFT", "OccurredOn": "1"}
    land_changed([rec], "pwc")
    assert land_changed([{**rec, "CaseNo": "PD1  "}], "pwc") is not None  # content differs
    assert land_changed([{**rec, "CaseNo": "PD1  "}], "pwc") is None      # but same incident now


def test_a_blank_key_falls_back_to_the_next(paths):
    rows = landed_rows(land_changed([{"CaseNo": " ", "InstanceID": "i-9", "IBRCode": "X"}], "pwc"))
    assert len(rows) == 1
    assert land_changed([{"CaseNo": " ", "InstanceID": "i-9", "IBRCode": "X"}], "pwc") is None


def test_keyless_rows_are_kept_once_each(paths):
    rows = [{"incident_id": None, "crimename2": "Theft"}, {"incident_id": None, "crimename2": "Fraud"}]
    assert len(landed_rows(land_changed(rows, "moco"))) == 2
    assert land_changed(rows, "moco") is None


# -------------------------------------------------- row versioning (Fairfax)

def ffx(uid, offense, service_date="1792104600000"):
    return {"UniqueID": uid, "IncidentNumber": "20262880232", "DateReported": service_date,
            "ViolationCodeReference_Descript": offense}


def test_fairfax_keeps_rows_a_later_pull_did_not_resend(paths):
    """The real case: one day's pull re-sends only the Property row, under
    a new UniqueID. Nothing new is landed, and the assault is not lost."""
    land_changed([ffx("u1", "ASSAULT (13C)"), ffx("u2", "EXTORTION (210)")], "fairfax")
    assert land_changed([ffx("u3-regenerated", "EXTORTION (210)")], "fairfax") is None


def test_fairfax_lands_genuinely_new_content(paths):
    land_changed([ffx("u1", "ASSAULT (13C)")], "fairfax")
    rows = landed_rows(land_changed([ffx("u9", "ROBBERY (120)")], "fairfax"))
    assert [r["ViolationCodeReference_Descript"] for r in rows] == ["ROBBERY (120)"]
