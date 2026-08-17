"""Statistical corrections applied on top of the fact table.

Three estimators, each addressing a way raw published crime data misleads
if you aggregate it naively. All three are computed at build time from
whatever the warehouse holds, so the numbers on the site are always
measured, never asserted from memory.

  aoristic()        Hour-of-day distributions are usually built from the
                    offense START time. For offenses discovered later
                    (burglary, theft from a vehicle) the start time is
                    really "when the victim was last there", so the
                    classic hour-of-day chart measures victim schedules,
                    not offense timing. Aoristic weighting spreads each
                    incident's unit of probability across its published
                    start-to-end window instead. Returns both curves plus
                    the measured distortion between them.

  nowcast()         Reports arrive days after the offense, so the most
                    recent days in any occurrence-date series are always
                    undercounted and the right edge of every trend line
                    slopes down for purely artificial reasons. Fits the
                    empirical reporting-delay distribution from mature
                    history and rescales recent days by their expected
                    completeness, with prediction intervals.

  detect_signals()  Flags unusual daily counts across hundreds of
                    (area x category) series at once. Testing that many
                    series guarantees false alarms, so per-series Poisson
                    tail probabilities are corrected with Benjamini-
                    Hochberg FDR, and calibrate_detector() measures the
                    realized false-discovery rate against simulated null
                    data so the control can be shown to work.

Not every source supports every correction: aoristic needs published end
times, nowcasting needs published report dates. The applicable
jurisdictions are derived from the data itself (see _sources_with) and
carried in each payload, so the site can label exactly what is covered
rather than implying the whole region was corrected.

Deliberately dependency-free (math only, no scipy) to keep the pipeline
install light; the distributions used here are small enough that direct
summation is both exact and fast.
"""

import logging
import math
import random

logger = logging.getLogger(__name__)

# ----------------------------------------------------------- parameters

# Aoristic: how far back to build the comparison, and the span beyond
# which a window tells us nothing about hour-of-day (>= 24h is uniform
# across the clock by construction).
AORISTIC_WINDOW_DAYS = 365
UNIFORM_SPAN_SECONDS = 24 * 3600

# Nowcast: a day is "mature" (fully reported) once it is this old, so the
# delay distribution is fitted only on days at least this far back.
MATURITY_DAYS = 60
# History used to fit the delay distribution. Long enough to be stable,
# short enough to reflect current reporting behavior.
DELAY_FIT_DAYS = 400
# Days at the right edge to correct.
NOWCAST_HORIZON_DAYS = 30
# Never divide by a completeness smaller than this: below it the
# correction amplifies noise more than it removes bias.
MIN_COMPLETENESS = 0.25

# Signals: same-weekday baseline over this many prior weeks.
BASELINE_WEEKS = 8
# A series must have at least this expected daily volume to be tested;
# below it a single incident is not distinguishable from noise.
MIN_BASELINE = 3.0
# Benjamini-Hochberg target false discovery rate.
FDR_Q = 0.10


# ------------------------------------------------------------- helpers

def _rows(con, sql: str) -> list[dict]:
    result = con.execute(sql)
    columns = [d[0] for d in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def _sources_with(con, column: str) -> list[str]:
    """Jurisdictions that actually publish a given optional column.

    Derived from the data rather than hardcoded, so adding a source that
    publishes end times or report dates enrolls it automatically.
    """
    rows = _rows(con, f"""
        SELECT jurisdiction
        FROM marts.fct_incidents
        GROUP BY 1
        HAVING COUNT({column}) > 0
        ORDER BY 1
    """)
    return [r["jurisdiction"] for r in rows]


def _sql_list(values: list[str]) -> str:
    """Quote a list of identifiers known to come from the warehouse."""
    safe = [v.replace("'", "''") for v in values]
    return ", ".join(f"'{v}'" for v in safe)


def _total_variation(p: list[float], q: list[float]) -> float:
    """Total variation distance between two discrete distributions:
    the largest possible disagreement about any set of hours, and the
    share of probability mass that would have to move to reconcile
    the two curves."""
    sp, sq = sum(p), sum(q)
    if sp <= 0 or sq <= 0:
        return 0.0
    return 0.5 * sum(abs(a / sp - b / sq) for a, b in zip(p, q))


# ------------------------------------------------------------- aoristic

def aoristic(con) -> dict:
    """Compare the conventional start-time hour-of-day curve against an
    aoristically weighted one.

    Each incident contributes exactly 1.0 of probability mass in both
    curves; only its placement differs. An incident with a known window
    spreads its mass across the hours the window covers, in proportion to
    how much of each hour the window occupies. Windows of a day or more
    are uniform across the clock by construction and are added as 1/24
    per hour rather than expanded, which is both exact and bounded.
    """
    jurisdictions = _sources_with(con, "occurred_end_at")
    empty = {
        "window_days": AORISTIC_WINDOW_DAYS,
        "jurisdictions": jurisdictions,
        "coverage": {"incidents": 0, "with_window": 0, "pct_with_window": 0.0},
        "by_hour": [],
        "by_category": [],
        "distortion": None,
    }
    if not jurisdictions:
        logger.info("aoristic: no source publishes end times; skipping")
        return empty

    scope = f"""
        WITH scoped AS (
            SELECT
                offense_category,
                occurred_at AS s,
                CASE WHEN occurred_end_at > occurred_at THEN occurred_end_at END AS e
            FROM marts.fct_incidents
            WHERE jurisdiction IN ({_sql_list(jurisdictions)})
              AND occurred_at IS NOT NULL
              AND occurred_at >= now() - INTERVAL {AORISTIC_WINDOW_DAYS} DAY
              AND occurred_at <= now()
        ),
        typed AS (
            SELECT *,
                date_diff('second', s, e) AS dur_s,
                CASE
                    WHEN e IS NULL THEN 'point'
                    WHEN date_diff('second', s, e) >= {UNIFORM_SPAN_SECONDS} THEN 'uniform'
                    ELSE 'spread'
                END AS kind
            FROM scoped
        )
    """

    coverage = _rows(con, scope + """
        SELECT
            COUNT(*) AS incidents,
            COUNT(*) FILTER (WHERE kind <> 'point') AS with_window,
            COUNT(*) FILTER (WHERE kind = 'uniform') AS uniform_windows
        FROM typed
    """)[0]
    if not coverage["incidents"]:
        return empty

    # Conventional curve: every incident sits on its start hour.
    start_rows = _rows(con, scope + """
        SELECT offense_category, hour(s) AS hr, COUNT(*)::DOUBLE AS w
        FROM typed GROUP BY 1, 2
    """)

    # Aoristic curve: mass spread across each incident's window.
    aoristic_rows = _rows(con, scope + f"""
        , point_mass AS (
            SELECT offense_category, hour(s) AS hr, COUNT(*)::DOUBLE AS w
            FROM typed WHERE kind = 'point' GROUP BY 1, 2
        ),
        uniform_mass AS (
            SELECT t.offense_category, g.hr, COUNT(*)::DOUBLE / 24.0 AS w
            FROM typed t
            CROSS JOIN (SELECT unnest(generate_series(0, 23)) AS hr) g
            WHERE t.kind = 'uniform'
            GROUP BY 1, 2
        ),
        expanded AS (
            SELECT offense_category, s, e, dur_s,
                   unnest(generate_series(date_trunc('hour', s),
                                          date_trunc('hour', e),
                                          INTERVAL 1 HOUR)) AS ts
            FROM typed WHERE kind = 'spread'
        ),
        spread_mass AS (
            SELECT offense_category, hour(ts) AS hr,
                   SUM(
                       GREATEST(
                           date_diff('second',
                                     GREATEST(s, ts),
                                     LEAST(e, ts + INTERVAL 1 HOUR)),
                           0)::DOUBLE / dur_s
                   ) AS w
            FROM expanded GROUP BY 1, 2
        )
        SELECT offense_category, hr, SUM(w) AS w FROM (
            SELECT * FROM point_mass
            UNION ALL SELECT * FROM uniform_mass
            UNION ALL SELECT * FROM spread_mass
        ) GROUP BY 1, 2
    """)

    def curve(rows, category=None) -> list[float]:
        out = [0.0] * 24
        for r in rows:
            if category is not None and r["offense_category"] != category:
                continue
            out[int(r["hr"])] += float(r["w"])
        return out

    start_all = curve(start_rows)
    aoristic_all = curve(aoristic_rows)

    by_hour = [
        {"hour": h, "start": round(start_all[h], 3), "aoristic": round(aoristic_all[h], 3)}
        for h in range(24)
    ]

    # Per category, ranked by how much the correction moves the curve.
    categories = sorted({r["offense_category"] for r in start_rows})
    by_category = []
    for cat in categories:
        s_curve, a_curve = curve(start_rows, cat), curve(aoristic_rows, cat)
        total = sum(s_curve)
        if total < 30:  # too thin for a stable shape comparison
            continue
        by_category.append({
            "category": cat,
            "incidents": int(round(total)),
            "tvd": round(_total_variation(s_curve, a_curve), 4),
            "start": [round(v, 3) for v in s_curve],
            "aoristic": [round(v, 3) for v in a_curve],
        })
    by_category.sort(key=lambda c: -c["tvd"])

    # Where the conventional curve is most wrong, in incident terms.
    diffs = [(h, aoristic_all[h] - start_all[h]) for h in range(24)]
    overstated = min(diffs, key=lambda d: d[1])   # start time claims too many here
    understated = max(diffs, key=lambda d: d[1])  # start time misses these

    total_start = sum(start_all)
    distortion = {
        "tvd": round(_total_variation(start_all, aoristic_all), 4),
        "mass_moved_pct": round(_total_variation(start_all, aoristic_all) * 100, 1),
        "most_overstated_hour": overstated[0],
        "most_overstated_by": round(abs(overstated[1]), 1),
        "most_overstated_pct": round(abs(overstated[1]) / total_start * 100, 2) if total_start else 0.0,
        "most_understated_hour": understated[0],
        "most_understated_by": round(understated[1], 1),
        "most_understated_pct": round(understated[1] / total_start * 100, 2) if total_start else 0.0,
    }

    return {
        "window_days": AORISTIC_WINDOW_DAYS,
        "jurisdictions": jurisdictions,
        "coverage": {
            "incidents": int(coverage["incidents"]),
            "with_window": int(coverage["with_window"]),
            "uniform_windows": int(coverage["uniform_windows"]),
            "pct_with_window": round(
                100.0 * coverage["with_window"] / coverage["incidents"], 1),
        },
        "by_hour": by_hour,
        "by_category": by_category,
        "distortion": distortion,
    }


# ------------------------------------------------------------- nowcast

def nowcast(con) -> dict:
    """Correct the artificial decline at the right edge of occurrence-date
    series using the empirical reporting-delay distribution.

    The delay distribution is fitted only on occurrence days old enough to
    be complete; applying it to a day of age `a` gives the share of that
    day's eventual reports that should have arrived by now, and dividing
    the observed count by that share estimates the eventual total.
    """
    jurisdictions = _sources_with(con, "reported_at")
    empty = {
        "jurisdictions": jurisdictions,
        "maturity_days": MATURITY_DAYS,
        "horizon_days": NOWCAST_HORIZON_DAYS,
        "delay": None,
        "completeness": [],
        "series": [],
    }
    if not jurisdictions:
        logger.info("nowcast: no source publishes report dates; skipping")
        return empty

    where = f"""
        jurisdiction IN ({_sql_list(jurisdictions)})
        AND occurred_at IS NOT NULL AND reported_at IS NOT NULL
    """

    # Delay distribution, fitted on mature days only. Negative delays are
    # source data errors (report filed before the offense window) and are
    # excluded, but counted so the payload can disclose them.
    delays = _rows(con, f"""
        SELECT
            date_diff('day', CAST(occurred_at AS DATE), CAST(reported_at AS DATE)) AS delay_days,
            COUNT(*) AS n
        FROM marts.fct_incidents
        WHERE {where}
          AND CAST(occurred_at AS DATE) >= current_date - {DELAY_FIT_DAYS}
          AND CAST(occurred_at AS DATE) <= current_date - {MATURITY_DAYS}
        GROUP BY 1 ORDER BY 1
    """)
    if not delays:
        return empty

    negative = sum(r["n"] for r in delays if r["delay_days"] < 0)
    valid = [(int(r["delay_days"]), int(r["n"])) for r in delays if r["delay_days"] >= 0]
    total = sum(n for _, n in valid)
    if not total:
        return empty

    # Empirical CDF of the delay: completeness[a] = share reported within
    # a days of the offence.
    cumulative, running = {}, 0
    for d, n in valid:
        running += n
        cumulative[d] = running
    completeness = []
    running = 0
    for age in range(NOWCAST_HORIZON_DAYS + 1):
        running = cumulative.get(age, running)
        completeness.append(running / total)

    def quantile(p: float) -> int:
        target, running_q = p * total, 0
        for d, n in valid:
            running_q += n
            if running_q >= target:
                return d
        return valid[-1][0]

    # Observed counts for the recent window, by occurrence date.
    observed = _rows(con, f"""
        SELECT CAST(occurred_at AS DATE) AS d, COUNT(*) AS n
        FROM marts.fct_incidents
        WHERE {where}
          AND CAST(occurred_at AS DATE) > current_date - {NOWCAST_HORIZON_DAYS}
          AND CAST(occurred_at AS DATE) <= current_date
        GROUP BY 1 ORDER BY 1
    """)
    today = con.execute("SELECT current_date").fetchone()[0]

    series = []
    for row in observed:
        day = row["d"]
        age = (today - day).days
        obs = int(row["n"])
        p = completeness[age] if age < len(completeness) else 1.0
        entry = {
            "date": str(day),
            "age_days": age,
            "observed": obs,
            "completeness": round(p, 4),
        }
        if p >= MIN_COMPLETENESS and p > 0:
            estimate = obs / p
            # Observed is a binomial thinning of the eventual total, so
            # the estimator's variance grows as completeness falls.
            se = math.sqrt(estimate * (1 - p) / p) if p < 1 else 0.0
            entry.update({
                "estimate": round(estimate, 1),
                "lower": round(max(obs, estimate - 1.96 * se), 1),
                "upper": round(estimate + 1.96 * se, 1),
            })
        else:
            # Too incomplete to rescale honestly: report the observation
            # and say so rather than publishing an unstable multiplier.
            entry.update({"estimate": None, "lower": None, "upper": None})
        series.append(entry)

    return {
        "jurisdictions": jurisdictions,
        "maturity_days": MATURITY_DAYS,
        "horizon_days": NOWCAST_HORIZON_DAYS,
        "delay": {
            "fitted_on": total,
            "negative_excluded": negative,
            "same_day_pct": round(100.0 * completeness[0], 1),
            "median_days": quantile(0.5),
            "p90_days": quantile(0.9),
        },
        "completeness": [round(c, 4) for c in completeness],
        "series": series,
    }


# ------------------------------------------- changepoint / anomaly flags

def _poisson_sf(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam), by direct summation of the lower
    tail. Counts here are small, so this is exact and cheap."""
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0 if k > 0 else 1.0
    # P(X <= k-1)
    term = math.exp(-lam)
    total = term
    for i in range(1, k):
        term *= lam / i
        total += term
        if total >= 1.0:
            return 0.0
    return max(0.0, min(1.0, 1.0 - total))


def _poisson_cdf(k: int, lam: float) -> float:
    """P(X <= k) for X ~ Poisson(lam)."""
    if k < 0:
        return 0.0
    if lam <= 0:
        return 1.0
    term = math.exp(-lam)
    total = term
    for i in range(1, k + 1):
        term *= lam / i
        total += term
    return max(0.0, min(1.0, total))


def benjamini_hochberg(pvalues: list[float], q: float = FDR_Q) -> list[bool]:
    """Step-up FDR control. Returns a mask of which hypotheses to reject.

    Testing hundreds of series at once means a plain 0.05 cut would fire
    on a handful of series every single day by chance alone; BH keeps the
    expected share of false alarms among fired alerts at or below q.
    """
    m = len(pvalues)
    if not m:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    threshold_rank = -1
    for rank, idx in enumerate(order, start=1):
        if pvalues[idx] <= q * rank / m:
            threshold_rank = rank
    mask = [False] * m
    if threshold_rank > 0:
        for rank, idx in enumerate(order, start=1):
            if rank <= threshold_rank:
                mask[idx] = True
    return mask


def _series_baselines(con, day, level: str) -> list[dict]:
    """Observed count on `day` and the same-weekday baseline for every
    series at the requested level ('jurisdiction' or 'area')."""
    key = "jurisdiction, offense_category" if level == "jurisdiction" \
        else "jurisdiction, COALESCE(area_name, '(unknown)') AS area_name, offense_category"
    group_area = "" if level == "jurisdiction" else ", area_name"
    return _rows(con, f"""
        WITH day_counts AS (
            SELECT jurisdiction,
                   COALESCE(area_name, '(unknown)') AS area_name,
                   offense_category,
                   CAST(occurred_at AS DATE) AS d,
                   COUNT(*) AS n
            FROM marts.fct_incidents
            WHERE CAST(occurred_at AS DATE) BETWEEN DATE '{day}' - {BASELINE_WEEKS * 7}
                                                AND DATE '{day}'
              AND dayofweek(occurred_at) = dayofweek(DATE '{day}')
            GROUP BY 1, 2, 3, 4
        ),
        agg AS (
            SELECT jurisdiction{group_area}, offense_category,
                   SUM(n) FILTER (WHERE d = DATE '{day}') AS observed,
                   SUM(n) FILTER (WHERE d < DATE '{day}') AS prior_total
            FROM day_counts
            GROUP BY ALL
        )
        SELECT jurisdiction{group_area}, offense_category,
               COALESCE(observed, 0) AS observed,
               COALESCE(prior_total, 0)::DOUBLE / {BASELINE_WEEKS} AS baseline
        FROM agg
    """)


def detect_signals(con, day=None, level: str = "area") -> dict:
    """Flag series whose latest count departs from their own baseline,
    with the multiple-comparisons problem handled explicitly.

    Every (area x category) pair is a separate hypothesis test. Testing
    hundreds of them daily at a naive 5% threshold would produce a
    steady drip of meaningless alerts, which is how "crime spike" claims
    usually get manufactured. Poisson tail probabilities are corrected
    with Benjamini-Hochberg so the expected false-discovery share among
    reported signals stays at or below FDR_Q.
    """
    if day is None:
        day = con.execute("""
            SELECT MAX(CAST(occurred_at AS DATE)) FROM marts.fct_incidents
            WHERE occurred_at <= now()
        """).fetchone()[0]
    if day is None:
        return {"day": None, "tested": 0, "flagged": [], "fdr_q": FDR_Q}

    rows = _series_baselines(con, day, level)
    tested, pvalues = [], []
    for r in rows:
        baseline = float(r["baseline"] or 0.0)
        if baseline < MIN_BASELINE:
            continue  # too thin to distinguish a spike from arithmetic
        observed = int(r["observed"] or 0)
        if observed >= baseline:
            p = _poisson_sf(observed, baseline)
            direction = "spike"
        else:
            p = _poisson_cdf(observed, baseline)
            direction = "lull"
        tested.append({
            "jurisdiction": r["jurisdiction"],
            "area_name": r.get("area_name"),
            "offense_category": r["offense_category"],
            "observed": observed,
            "baseline": round(baseline, 2),
            "ratio": round(observed / baseline, 2) if baseline else None,
            "direction": direction,
            "p_value": p,
        })
        pvalues.append(p)

    mask = benjamini_hochberg(pvalues, FDR_Q)
    flagged = []
    for entry, keep in zip(tested, mask):
        if not keep:
            continue
        entry = dict(entry)
        entry["p_value"] = float(f"{entry['p_value']:.2e}")
        flagged.append(entry)
    flagged.sort(key=lambda e: e["p_value"])

    return {
        "day": str(day),
        "level": level,
        "tested": len(tested),
        "flagged": flagged,
        "fdr_q": FDR_Q,
        "naive_would_flag": sum(1 for p in pvalues if p <= 0.05),
    }


def calibrate_detector(con, trials: int = 200, seed: int = 12345) -> dict:
    """Backtest the detector against simulated null data.

    Real alerts have no ground truth, so a "precision" number would be
    invented. What can be measured honestly is calibration: generate
    counts from each series' own fitted baseline (so every series is null
    by construction, no real spikes exist), run the same detector, and
    count how often it fires. Under correct FDR control essentially every
    firing here is a false discovery, so the realized rate should sit at
    or below the target.
    """
    day = con.execute("""
        SELECT MAX(CAST(occurred_at AS DATE)) FROM marts.fct_incidents
        WHERE occurred_at <= now()
    """).fetchone()[0]
    if day is None:
        return {"trials": 0}

    baselines = [
        float(r["baseline"])
        for r in _series_baselines(con, day, "area")
        if float(r["baseline"] or 0.0) >= MIN_BASELINE
    ]
    if not baselines:
        return {"trials": 0, "series": 0}

    rng = random.Random(seed)

    def poisson_draw(lam: float) -> int:
        # Knuth's method; the lambdas here are small daily counts.
        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= rng.random()
            if p <= limit:
                return k
            k += 1

    naive_fires = bh_fires = 0
    for _ in range(trials):
        pvalues = []
        for lam in baselines:
            observed = poisson_draw(lam)
            p = _poisson_sf(observed, lam) if observed >= lam else _poisson_cdf(observed, lam)
            pvalues.append(p)
        naive_fires += sum(1 for p in pvalues if p <= 0.05)
        bh_fires += sum(benjamini_hochberg(pvalues, FDR_Q))

    total_tests = trials * len(baselines)
    return {
        "trials": trials,
        "series": len(baselines),
        "tests": total_tests,
        "fdr_q": FDR_Q,
        # Under a pure null every firing is a false alarm, so these rates
        # are directly interpretable as false-alarm rates.
        "naive_false_alarms_per_day": round(naive_fires / trials, 2),
        "bh_false_alarms_per_day": round(bh_fires / trials, 2),
        "naive_false_alarm_rate": round(naive_fires / total_tests, 4),
        "bh_false_alarm_rate": round(bh_fires / total_tests, 4),
    }
