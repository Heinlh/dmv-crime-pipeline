"""In-memory stand-ins for the five agency APIs.

FakeAgencies answers the requests the real extractors make, so the
integration and end-to-end tests run every extractor unmodified, through
request_json, with no network:

  Socrata  /api/views/{id}.json             column metadata
           /resource/{id}.json              $select, $where f > 'ts', $order, paging
  ArcGIS   .../FeatureServer?f=json         layer listing
           .../FeatureServer/{id}?f=json    field list
           .../FeatureServer/{id}/query     where f > TIMESTAMP 'ts', outFields, paging

Each dataset publishes more fields than the pipeline's allowlist,
mirroring what the real agencies publish (house numbers, exact street
addresses, victim demographics, officer IDs), so tests can assert those
are never requested and never landed. Every request is recorded in
`calls`.
"""

import re
from datetime import datetime, timedelta, timezone

import config

# Fields the real agencies publish that are outside the allowlist, some of
# them sensitive, which must never be requested or landed (PG County's
# street_address is the one exception: it is requested as fetch_only, to
# derive block_address, and must still never be landed).
UNLISTED = {
    "moco": {"address_number", "address_street", "geolocation_address", "place"},
    "pgc": {"street_number", "street_address", "location", "geo_reference_"},
    "dc": {"XBLOCK", "YBLOCK", "SHIFT", "ANC", "OBJECTID"},
    "fairfax": {"VictimAge", "VictimRace", "VictimSex", "OfficerID"},
    "pwc": {"Disposition", "ReportingOfficer"},
}

_SOCRATA_WHERE = re.compile(r"^(\w+) > '([^']+)'$")
_ARCGIS_WHERE = re.compile(r"^(\w+) > TIMESTAMP '([^']+)'$")


def iso(moment: datetime) -> str:
    """Socrata floating timestamp, as the real API returns it."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000")


def epoch_ms(moment: datetime) -> int:
    """ArcGIS date value, as the real API returns it."""
    return int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000)


class FakeAgencies:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.socrata: dict[str, dict] = {}   # resource url -> {columns, records, ts}
        self.arcgis: dict[str, dict] = {}    # layer url -> {fields, features}
        self.servers: dict[str, list] = {}   # FeatureServer url -> layer listing

    # --------------------------------------------------------- registry

    def add_socrata(self, resource_url: str, columns: list[str], records: list[dict]):
        self.socrata[resource_url] = {"columns": columns, "records": records}

    def add_arcgis_layer(self, server_url: str, layer_id: int, name: str,
                         fields: list[str], features: list[dict]):
        """features: [{"attributes": {...}, "geometry": {"x":..., "y":...} | None}]"""
        self.servers.setdefault(server_url, []).append({"id": layer_id, "name": name})
        self.arcgis[f"{server_url}/{layer_id}"] = {"fields": fields, "features": features}

    # --------------------------------------------------------- dispatch

    def __call__(self, session, url, params):
        params = dict(params or {})
        self.calls.append((url, params))
        if "/api/views/" in url:
            resource = url.replace("/api/views/", "/resource/")
            return {"columns": [{"fieldName": c} for c in self.socrata[resource]["columns"]]}
        if "/resource/" in url:
            return self._socrata_query(self.socrata[url], params)
        if url.endswith("/query"):
            return self._arcgis_query(self.arcgis[url[: -len("/query")]], params)
        if url in self.servers:
            return {"layers": self.servers[url]}
        if url in self.arcgis:
            return {"fields": [{"name": f} for f in self.arcgis[url]["fields"]]}
        raise AssertionError(f"unexpected request: {url} {params}")

    def _socrata_query(self, dataset, params):
        select = params["$select"].split(",")
        unknown = set(select) - set(dataset["columns"])
        assert not unknown, f"$select names columns the dataset lacks: {unknown}"
        rows = dataset["records"]
        match = _SOCRATA_WHERE.match(params.get("$where", ""))
        if match:
            field, since = match.groups()
            cut = datetime.fromisoformat(since)
            rows = [r for r in rows if r.get(field) and datetime.fromisoformat(r[field]) > cut]
            rows = sorted(rows, key=lambda r: r[field])
        offset, limit = int(params.get("$offset", 0)), int(params.get("$limit", 1000))
        # Socrata omits null fields from each returned record
        return [{f: r[f] for f in select if r.get(f) is not None} for r in rows[offset:offset + limit]]

    def _arcgis_query(self, layer, params):
        out_fields = params["outFields"].split(",")
        unknown = set(out_fields) - set(layer["fields"])
        assert not unknown, f"outFields names fields the layer lacks: {unknown}"
        features = layer["features"]
        match = _ARCGIS_WHERE.match(params.get("where", ""))
        if match:
            field, since = match.groups()
            cut = epoch_ms(datetime.strptime(since, "%Y-%m-%d %H:%M:%S"))
            features = [f for f in features if (f["attributes"].get(field) or 0) > cut]
            features = sorted(features, key=lambda f: f["attributes"][field])
        offset = int(params.get("resultOffset", 0))
        count = int(params.get("resultRecordCount", 1000))
        page = features[offset:offset + count]
        want_geometry = params.get("returnGeometry") == "true"
        return {"features": [
            {"attributes": {k: v for k, v in f["attributes"].items() if k in out_fields},
             **({"geometry": f.get("geometry")} if want_geometry else {})}
            for f in page
        ]}

    # --------------------------------------------------------- inspection

    def requested_fields(self) -> dict[str, set]:
        """Every field name any query asked for, by source."""
        found: dict[str, set] = {}
        for url, params in self.calls:
            for key in ("$select", "outFields"):
                if key in params:
                    found.setdefault(_source_of(url), set()).update(params[key].split(","))
        return found

    def install(self, monkeypatch):
        """Route every extractor's request_json through this fake."""
        for module in ("moco", "pgc", "dc", "fairfax", "pwc"):
            monkeypatch.setattr(f"extractors.{module}.request_json", self)
        monkeypatch.setattr("extractors.base.request_json", self)
        monkeypatch.setattr("extractors.base.time.sleep", lambda s: None)
        return self


_HOSTS = {
    "montgomerycountymd": "moco",
    "princegeorgescountymd": "pgc",
    "dcgis": "dc",
    "kYvfX7YK8OobHItA": "fairfax",
    "Public_Crime_Reports": "pwc",
}


def _source_of(url: str) -> str:
    return next((source for marker, source in _HOSTS.items() if marker in url), "unknown")


# ------------------------------------------------------------------ world

def build_world(now: datetime | None = None) -> FakeAgencies:
    """A small, realistic snapshot of all five agencies: recent incidents
    in each, with the full published field sets (sensitive extras
    included). Times are relative to `now` so the site's default 30 day
    window always shows them."""
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0, tzinfo=None)
    day = lambda n, h=12: now - timedelta(days=n) + timedelta(hours=h - now.hour)
    fake = FakeAgencies()

    # ---- Montgomery County (Socrata)
    moco_cols = ["incident_id", "offence_code", "case_number", "date", "start_date", "end_date",
                 "nibrs_code", "victims", "crimename1", "crimename2", "crimename3", "district",
                 "location", "city", "state", "zip_code", "agency", "place", "sector", "beat",
                 "pra", "address_number", "address_street", "street_type", "latitude",
                 "longitude", "geolocation_address"]
    moco = [
        {"incident_id": "201300001", "offence_code": "2305", "case_number": "26050001",
         "date": iso(day(3, 14)), "start_date": iso(day(3, 13)), "nibrs_code": "23F",
         "victims": "1", "crimename1": "Crime Against Property",
         "crimename2": "Theft From Motor Vehicle", "crimename3": "LARCENY - FROM AUTO",
         "district": "ROCKVILLE", "location": "100 BLK  MONROE ST", "city": "ROCKVILLE",
         "state": "MD", "zip_code": "20850", "place": "Street - Residential",
         "address_number": "123", "address_street": "MONROE", "street_type": "ST",
         "latitude": "39.0840", "longitude": "-77.1528",
         "geolocation_address": "123 MONROE ST ROCKVILLE MD"},
        {"incident_id": "201300002", "offence_code": "0901", "case_number": "26050002",
         "date": iso(day(2, 22)), "start_date": iso(day(2, 21)), "nibrs_code": "09A",
         "victims": "1", "crimename1": "Crime Against Person",
         "crimename2": "Murder and Nonnegligent Manslaughter", "crimename3": "HOMICIDE",
         "district": "GERMANTOWN", "location": "19900 BLK  FREDERICK RD",
         "city": "GERMANTOWN", "state": "MD", "zip_code": "20874",
         "address_number": "19942", "address_street": "FREDERICK",
         "latitude": "39.1732", "longitude": "-77.2717"},
    ]
    fake.add_socrata(config.MOCO["base_url"], moco_cols, moco)

    # ---- Prince George's County (Socrata, two datasets, one schema)
    pgc_cols = ["incident_case_id", "date", "clearance_code_inc_type", "pgpd_reporting_area",
                "pgpd_sector", "pgpd_beat", "street_number", "street_address", "latitude",
                "longitude", "location", "geo_reference_"]
    pgc = [
        {"incident_case_id": "PG26000101", "date": iso(day(4, 9)),
         "clearance_code_inc_type": "THEFT FROM AUTO", "pgpd_sector": "H", "pgpd_beat": "H2",
         "street_number": "9700 BLOCK", "street_address": "9759 GOOD LUCK RD",
         "latitude": "38.98377172648907", "longitude": "-76.84168869256973",
         "location": "(38.98377172648907,-76.84168869256973)"},
        {"incident_case_id": "PG26000102", "date": iso(day(5, 23)),
         "clearance_code_inc_type": "ASSAULT, SHOOTING", "pgpd_sector": "K",
         "street_address": "SANSBURY RD / RITCHIE MARLBORO RD SB",
         "latitude": "38.867302149534225", "longitude": "-76.8384078592062"},
    ]
    fake.add_socrata(config.PGC["base_url"], pgc_cols, pgc)
    fake.add_socrata(config.PGC["historical_url"], pgc_cols[:-1], [])

    # ---- DC (ArcGIS, one layer per year)
    dc_fields = ["CCN", "REPORT_DAT", "SHIFT", "METHOD", "OFFENSE", "BLOCK", "XBLOCK", "YBLOCK",
                 "WARD", "ANC", "DISTRICT", "LATITUDE", "LONGITUDE", "START_DATE", "END_DATE",
                 "OBJECTID"]
    dc = [
        {"attributes": {"CCN": "26150001", "REPORT_DAT": epoch_ms(day(1, 8)), "SHIFT": "DAY",
                        "METHOD": "GUN", "OFFENSE": "ROBBERY",
                        "BLOCK": "1400 - 1499 BLOCK OF U STREET NW", "XBLOCK": 397200.1,
                        "YBLOCK": 138200.2, "WARD": "1", "ANC": "1B", "DISTRICT": "3",
                        "LATITUDE": 38.9170, "LONGITUDE": -77.0339,
                        "START_DATE": epoch_ms(day(1, 2)), "END_DATE": epoch_ms(day(1, 3)),
                        "OBJECTID": 1}},
        {"attributes": {"CCN": "26150002", "REPORT_DAT": epoch_ms(day(2, 9)), "SHIFT": "EVENING",
                        "METHOD": "OTHERS", "OFFENSE": "THEFT F/AUTO",
                        "BLOCK": "3200 - 3299 BLOCK OF M STREET NW", "WARD": "2",
                        "LATITUDE": 38.9050, "LONGITUDE": -77.0620,
                        "START_DATE": epoch_ms(day(2, 1)), "OBJECTID": 2}},
    ]
    dc_url = config.DC["base_url"]
    fake.add_arcgis_layer(dc_url, 41, f"Crime Incidents - {now.year}", dc_fields, dc)
    if now.year - 1 >= config.BACKFILL_START.year:
        fake.add_arcgis_layer(dc_url, 7, f"Crime Incidents - {now.year - 1}", dc_fields, [])

    # ---- Fairfax (ArcGIS, three services with different schemas)
    person_fields = ["UniqueID", "DateReported", "BeginDate", "IBRCode", "IncidentNumber",
                     "ViolationCodeReference_Descript", "Category", "Station", "PatrolArea",
                     "DISTRICT", "VictimAge", "VictimRace", "VictimSex", "OfficerID"]
    person = [
        {"attributes": {"UniqueID": "ffx-1", "DateReported": epoch_ms(day(2, 15)),
                        "BeginDate": epoch_ms(day(2, 13)), "IBRCode": "13A",
                        "IncidentNumber": "20260330001 ",
                        "ViolationCodeReference_Descript": "ASSAULT - AGGRAVATED (13A) ",
                        "Category": "Aggravated Assault", "Station": "6", "DISTRICT": "MASON",
                        "VictimAge": 34, "VictimRace": "W", "VictimSex": "F", "OfficerID": "4471"},
         "geometry": {"x": -77.1560, "y": 38.7984}},
    ]
    base = config.FAIRFAX["base_url"]
    for service, fields, feats in (
        (config.FAIRFAX["services"][0], person_fields, person),
        (config.FAIRFAX["services"][1], person_fields, []),
        (config.FAIRFAX["services"][2],
         ["UniqueID", "ReportDate", "BeginDate", "IBRCode", "IncidentNumber", "IBRDescription",
          "EventDescription", "Station", "PatrolArea", "OfficerID"], []),
    ):
        fake.add_arcgis_layer(f"{base}/{service}/FeatureServer", 0, service, fields, feats)

    # ---- Prince William (ArcGIS, single layer)
    pwc_fields = ["InstanceID", "CaseNo", "OccurredOn", "OccurredBetween", "BlockAddress", "City",
                  "State", "ZipCode", "IBRCode", "CrimeCategory", "Disposition", "ReportingOfficer"]
    pwc = [
        {"attributes": {"InstanceID": "pwc-1", "CaseNo": "PD260009001 ",
                        "OccurredOn": epoch_ms(day(3, 4)), "BlockAddress": "13800 Smoketown Rd",
                        "City": "WOODBRIDGE", "State": "VA", "ZipCode": "22192",
                        "IBRCode": "MOTOR VEHICLE THEFT", "CrimeCategory": "Motor Vehicle Theft",
                        "Disposition": "OPEN", "ReportingOfficer": "Ofc. 2231"},
         "geometry": {"x": -77.2963, "y": 38.6410}},
    ]
    fake.add_arcgis_layer(config.PWC_VA["base_url"], 0, "Public_Crime_Reports", pwc_fields, pwc)

    return fake
