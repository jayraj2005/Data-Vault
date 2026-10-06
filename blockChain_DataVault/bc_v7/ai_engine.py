# ──────────────────────────────────────────────────────────────────────
# ai_engine.py
#
# Lightweight, dependency-free "AI" analytics engine for DataVault.
# Everything here is plain Python (stdlib only) heuristics & statistics —
# no external API keys / network calls are required, so these features
# work fully offline, consistent with the rest of the app (see
# app.py -> ai_answer()).
#
# Exposed functions:
#   business_recommendations(headers, rows)
#   data_story(headers, rows)
#   predictive_forecast(headers, rows, column=None, metric_hint=None, periods=6)
#   data_quality_report(headers, rows)
#   clean_dataset(headers, rows)
#   build_full_report(headers, rows, dataset_name)
# ──────────────────────────────────────────────────────────────────────
import statistics
from collections import OrderedDict, Counter

MISSING_TOKENS = {"", "none", "null", "nan", "n/a", "na", "-", "?"}

METRIC_KEYWORDS = ["sales", "revenue", "profit", "amount", "price", "total",
                   "quantity", "qty", "income", "cost", "value", "demand"]
DIMENSION_KEYWORDS = ["region", "category", "product", "segment", "state",
                      "country", "city", "department", "channel", "type"]


# ── Generic helpers ─────────────────────────────────────────────────
def _clean_val(v):
    return str(v).strip() if v is not None else ""


def _is_missing(v):
    return _clean_val(v).lower() in MISSING_TOKENS


def to_float(v):
    try:
        return float(_clean_val(v).replace(",", "").replace("$", "").replace("%", ""))
    except (ValueError, TypeError):
        return None


def numeric_values(rows, col):
    out = []
    for r in rows:
        f = to_float(r.get(col, ""))
        if f is not None:
            out.append(f)
    return out


def is_numeric_col(rows, col, threshold=0.6):
    present = [r.get(col, "") for r in rows if not _is_missing(r.get(col, ""))]
    if not present:
        return False
    nums = [v for v in present if to_float(v) is not None]
    return len(nums) >= len(present) * threshold


def get_numeric_cols(headers, rows):
    return [h for h in headers if is_numeric_col(rows, h)]


def get_categorical_cols(headers, rows):
    return [h for h in headers if not is_numeric_col(rows, h)]


def _looks_like_id_col(h, rows):
    if h.lower() == "id" or h.lower().endswith("id") or h.lower().endswith("_id"):
        return True
    vals = numeric_values(rows, h)
    return bool(vals) and len(vals) > 5 and len(set(vals)) >= len(vals) * 0.98


def pick_metric_col(headers, rows, hint=None):
    num_cols = [h for h in get_numeric_cols(headers, rows) if not _looks_like_id_col(h, rows)]
    if not num_cols:
        num_cols = get_numeric_cols(headers, rows)
    if not num_cols:
        return None
    if hint:
        for h in num_cols:
            if hint.lower() in h.lower():
                return h
    for kw in METRIC_KEYWORDS:
        for h in num_cols:
            if kw in h.lower():
                return h
    return num_cols[0]


def pick_dimension_col(headers, rows, exclude=None):
    cat_cols = [h for h in get_categorical_cols(headers, rows) if h != exclude]
    if not cat_cols:
        return None
    scored = []
    for h in cat_cols:
        vals = [_clean_val(r.get(h, "")) for r in rows if not _is_missing(r.get(h, ""))]
        card = len(set(vals))
        if 1 < card <= 25:
            kw_bonus = 5 if any(kw in h.lower() for kw in DIMENSION_KEYWORDS) else 0
            scored.append((kw_bonus, -card, h))
    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][2]


def linreg(xs, ys):
    """Simple least-squares linear regression. Returns (slope, intercept, r2)."""
    n = len(xs)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0), 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sxy / sxx if sxx else 0.0
    intercept = my - slope * mx
    # R^2
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - (ss_res / ss_tot) if ss_tot else 1.0
    return slope, intercept, round(max(0.0, min(1.0, r2)), 3)


def half_split_change(values):
    """% change between avg of first half and avg of second half of an ordered series."""
    n = len(values)
    if n < 2:
        return 0.0, 0.0, 0.0
    mid = n // 2
    first, second = values[:mid], values[mid:]
    a1 = sum(first) / len(first) if first else 0.0
    a2 = sum(second) / len(second) if second else 0.0
    pct = ((a2 - a1) / a1 * 100) if a1 else (100.0 if a2 else 0.0)
    return round(a1, 2), round(a2, 2), round(pct, 1)


def _fmt_num(v):
    if v is None:
        return "0"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if float(v).is_integer():
        return f"{int(v)}"
    return f"{v:,.2f}"


# ── 1. AI Business Recommendation ───────────────────────────────────
ACTION_LIBRARY = {
    "mild":     ["Monitor the trend closely", "Run a small targeted promotion", "Review recent customer feedback"],
    "moderate": ["Increase Marketing Spend", "Provide Seasonal Discounts", "Improve Inventory Planning"],
    "severe":   ["Investigate Root Cause Urgently", "Reallocate Budget from Weaker Channels",
                 "Increase Marketing Spend", "Provide Discounts", "Improve Inventory Planning"],
}
GROWTH_ACTIONS = ["Scale Up Marketing Budget", "Expand Product Range", "Replicate Strategy in Other Segments"]


def business_recommendations(headers, rows):
    metric = pick_metric_col(headers, rows)
    dimension = pick_dimension_col(headers, rows)

    if not metric or not rows:
        return {"available": False,
                "message": "Not enough numeric data was found to generate business recommendations."}

    insights = []

    if dimension:
        groups = OrderedDict()
        for r in rows:
            key = _clean_val(r.get(dimension, ""))
            if not key:
                continue
            v = to_float(r.get(metric, ""))
            if v is not None:
                groups.setdefault(key, []).append(v)

        group_stats = []
        for key, vals in groups.items():
            if len(vals) < 2:
                a1 = a2 = vals[0] if vals else 0
                pct = 0.0
            else:
                a1, a2, pct = half_split_change(vals)
            group_stats.append({"group": key, "total": round(sum(vals), 2),
                                 "avg_before": a1, "avg_after": a2, "change_pct": pct})

        group_stats.sort(key=lambda g: g["change_pct"])

        worst = [g for g in group_stats if g["change_pct"] < -3][:3]
        best = [g for g in group_stats if g["change_pct"] > 3]
        best = sorted(best, key=lambda g: -g["change_pct"])[:2]

        for g in worst:
            severity = "severe" if g["change_pct"] <= -25 else ("moderate" if g["change_pct"] <= -10 else "mild")
            insights.append({
                "type": "decline",
                "headline": f"{g['group']} {metric} is decreasing by {abs(g['change_pct'])}%.",
                "detail": f"Average {metric} dropped from {_fmt_num(g['avg_before'])} to {_fmt_num(g['avg_after'])} for '{g['group']}'.",
                "recommendations": ACTION_LIBRARY[severity],
                "severity": severity,
            })

        for g in best:
            insights.append({
                "type": "growth",
                "headline": f"{g['group']} {metric} is growing by {g['change_pct']}%.",
                "detail": f"Average {metric} rose from {_fmt_num(g['avg_before'])} to {_fmt_num(g['avg_after'])} for '{g['group']}'.",
                "recommendations": GROWTH_ACTIONS,
                "severity": "growth",
            })

    if not insights:
        # Fallback: overall trend on the metric alone
        vals = numeric_values(rows, metric)
        a1, a2, pct = half_split_change(vals)
        if pct < -3:
            severity = "severe" if pct <= -25 else ("moderate" if pct <= -10 else "mild")
            insights.append({
                "type": "decline",
                "headline": f"Overall {metric} is decreasing by {abs(pct)}%.",
                "detail": f"Average {metric} dropped from {_fmt_num(a1)} to {_fmt_num(a2)}.",
                "recommendations": ACTION_LIBRARY[severity],
                "severity": severity,
            })
        elif pct > 3:
            insights.append({
                "type": "growth",
                "headline": f"Overall {metric} is growing by {pct}%.",
                "detail": f"Average {metric} rose from {_fmt_num(a1)} to {_fmt_num(a2)}.",
                "recommendations": GROWTH_ACTIONS,
                "severity": "growth",
            })
        else:
            insights.append({
                "type": "stable",
                "headline": f"{metric} is stable, with no significant change detected.",
                "detail": f"Average {metric} moved from {_fmt_num(a1)} to {_fmt_num(a2)} ({pct}%).",
                "recommendations": ["Maintain Current Strategy", "Explore New Growth Channels"],
                "severity": "stable",
            })

    return {"available": True, "metric": metric, "dimension": dimension, "insights": insights}


# ── 2. AI Data Storytelling ──────────────────────────────────────────
def data_story(headers, rows):
    metric = pick_metric_col(headers, rows)
    dimension = pick_dimension_col(headers, rows)

    if not metric or not rows:
        return {"available": False,
                "message": "Not enough numeric data was found to generate a data story."}

    bullets = []
    vals = numeric_values(rows, metric)
    a1, a2, pct = half_split_change(vals)

    if pct > 0.5:
        bullets.append(f"{metric} increased by {abs(pct)}%.")
    elif pct < -0.5:
        bullets.append(f"{metric} decreased by {abs(pct)}%.")
    else:
        bullets.append(f"{metric} stayed roughly flat ({pct}%).")

    top_group = bottom_group = None
    if dimension:
        totals = Counter()
        for r in rows:
            key = _clean_val(r.get(dimension, ""))
            v = to_float(r.get(metric, ""))
            if key and v is not None:
                totals[key] += v
        if totals:
            top_group = totals.most_common(1)[0]
            bottom_group = min(totals.items(), key=lambda kv: kv[1])
            bullets.append(f"Most of the {metric.lower()} came from {top_group[0]} ({_fmt_num(top_group[1])}).")

            # Decline detection per-category for the storytelling recommendation
            groups = OrderedDict()
            for r in rows:
                key = _clean_val(r.get(dimension, ""))
                v = to_float(r.get(metric, ""))
                if key and v is not None:
                    groups.setdefault(key, []).append(v)
            declining = []
            for key, gv in groups.items():
                if len(gv) >= 2:
                    _, _, gpct = half_split_change(gv)
                    if gpct < -3:
                        declining.append((key, gpct))
            declining.sort(key=lambda kv: kv[1])
            if declining:
                worst_key = declining[0][0]
                bullets.append(f"{worst_key} {metric.lower()} declined by {abs(declining[0][1])}%.")
                bullets.append(f"Recommendation: Increase {worst_key} Promotion.")
            elif bottom_group and bottom_group[0] != top_group[0]:
                bullets.append(f"{bottom_group[0]} contributed the least ({_fmt_num(bottom_group[1])}).")
                bullets.append(f"Recommendation: Investigate and Promote {bottom_group[0]}.")

    if len(bullets) < 2:
        bullets.append(f"Dataset contains {len(rows)} records across {len(headers)} columns.")

    return {"available": True, "metric": metric, "dimension": dimension, "bullets": bullets}


# ── 3. Predictive Analytics ─────────────────────────────────────────
PREDICT_KEYWORDS = {
    "sales": ["sales", "units", "qty", "quantity"],
    "revenue": ["revenue", "amount", "total", "income"],
    "demand": ["demand", "orders", "quantity"],
    "profit": ["profit", "margin", "earning"],
    "customer growth": ["customer", "users", "signup", "growth", "clients"],
}


def predictive_forecast(headers, rows, column=None, metric_hint=None, periods=6):
    num_cols = get_numeric_cols(headers, rows)
    if not num_cols or not rows:
        return {"available": False, "message": "No numeric column available to predict."}

    target = None
    if column and column in num_cols:
        target = column
    elif metric_hint:
        kws = PREDICT_KEYWORDS.get(metric_hint.lower(), [metric_hint.lower()])
        for h in num_cols:
            if any(kw in h.lower() for kw in kws):
                target = h
                break
    if not target:
        target = pick_metric_col(headers, rows, hint=metric_hint)

    values = numeric_values(rows, target)
    if len(values) < 2:
        return {"available": False, "message": f"Not enough data points in '{target}' to predict."}

    xs = list(range(len(values)))
    slope, intercept, r2 = linreg(xs, values)

    periods = max(1, min(int(periods or 6), 24))
    forecast_vals = []
    for i in range(len(values), len(values) + periods):
        pred = slope * i + intercept
        forecast_vals.append(round(pred, 2))

    trend = "increasing" if slope > 0.001 else ("decreasing" if slope < -0.001 else "flat")

    hist_labels = [f"#{i+1}" for i in range(len(values))]
    fc_labels = [f"#{i+1}" for i in range(len(values), len(values) + periods)]

    return {
        "available": True,
        "column": target,
        "trend": trend,
        "slope": round(slope, 4),
        "r2": r2,
        "history": {"labels": hist_labels, "values": [round(v, 2) for v in values]},
        "forecast": {"labels": fc_labels, "values": forecast_vals},
    }


# ── 4. Data Cleaning Module — detection ─────────────────────────────
def data_quality_report(headers, rows):
    n = len(rows)
    col_reports = []
    dup_seen, dup_indices = set(), []
    for i, r in enumerate(rows):
        sig = tuple(_clean_val(r.get(h, "")) for h in headers)
        if sig in dup_seen:
            dup_indices.append(i)
        else:
            dup_seen.add(sig)

    total_missing = 0
    total_wrong_type = 0
    total_outliers = 0

    for h in headers:
        missing = sum(1 for r in rows if _is_missing(r.get(h, "")))
        total_missing += missing

        numeric_col = is_numeric_col(rows, h)
        wrong_type = 0
        outliers = 0
        if numeric_col:
            present = [r.get(h, "") for r in rows if not _is_missing(r.get(h, ""))]
            wrong_type = sum(1 for v in present if to_float(v) is None)
            nums = numeric_values(rows, h)
            if len(nums) >= 4:
                s = sorted(nums)
                q1 = s[len(s) // 4]
                q3 = s[(len(s) * 3) // 4]
                iqr = q3 - q1
                lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                outliers = sum(1 for v in nums if v < lo or v > hi)

        total_wrong_type += wrong_type
        total_outliers += outliers

        col_reports.append({
            "column": h, "type": "numeric" if numeric_col else "categorical",
            "missing": missing, "wrong_type": wrong_type, "outliers": outliers,
        })

    penalty = min(100, (total_missing + total_wrong_type + len(dup_indices) + total_outliers) / max(1, n * max(1, len(headers))) * 100)
    quality_score = round(max(0, 100 - penalty), 1)

    return {
        "row_count": n, "col_count": len(headers),
        "duplicate_rows": len(dup_indices),
        "missing_values": total_missing,
        "wrong_type_values": total_wrong_type,
        "outlier_values": total_outliers,
        "quality_score": quality_score,
        "columns": col_reports,
        # Duplicates, missing values, and wrong-type values are genuine data-integrity
        # problems and should block/flag a dataset. Statistical outliers are not
        # necessarily errors (real data legitimately has extreme values), so they're
        # still reported for visibility but no longer treated as a blocking "issue" —
        # this was causing perfectly clean datasets to be rejected/flagged.
        "has_issues": bool(dup_indices or total_missing or total_wrong_type),
    }


# ── 4b. Data Cleaning Module — apply cleaning ───────────────────────
def clean_dataset(headers, rows):
    """Returns (cleaned_rows, report) — non-destructive; caller decides where to save."""
    report = {"duplicates_removed": 0, "missing_filled": 0,
              "wrong_type_fixed": 0, "outliers_capped": 0}

    # 1. Drop exact duplicate rows
    seen = set()
    deduped = []
    for r in rows:
        sig = tuple(_clean_val(r.get(h, "")) for h in headers)
        if sig in seen:
            report["duplicates_removed"] += 1
            continue
        seen.add(sig)
        deduped.append(dict(r))

    numeric_cols = get_numeric_cols(headers, deduped)

    # 2. Fix wrong types + missing values for numeric columns (mean fill)
    for h in numeric_cols:
        nums = numeric_values(deduped, h)
        mean_val = round(statistics.mean(nums), 2) if nums else 0
        for r in deduped:
            raw = r.get(h, "")
            if _is_missing(raw):
                r[h] = mean_val
                report["missing_filled"] += 1
            elif to_float(raw) is None:
                r[h] = mean_val
                report["wrong_type_fixed"] += 1

    # 3. Fill missing categorical values with column mode
    cat_cols = [h for h in headers if h not in numeric_cols]
    for h in cat_cols:
        present = [_clean_val(r.get(h, "")) for r in deduped if not _is_missing(r.get(h, ""))]
        mode_val = Counter(present).most_common(1)[0][0] if present else "Unknown"
        for r in deduped:
            if _is_missing(r.get(h, "")):
                r[h] = mode_val
                report["missing_filled"] += 1

    # 4. Cap outliers (winsorize) for numeric columns using IQR bounds
    for h in numeric_cols:
        nums = numeric_values(deduped, h)
        if len(nums) < 4:
            continue
        s = sorted(nums)
        q1 = s[len(s) // 4]
        q3 = s[(len(s) * 3) // 4]
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        for r in deduped:
            v = to_float(r.get(h, ""))
            if v is None:
                continue
            if v < lo:
                r[h] = round(lo, 2)
                report["outliers_capped"] += 1
            elif v > hi:
                r[h] = round(hi, 2)
                report["outliers_capped"] += 1

    return deduped, report


# ── 4c. "What to Improve" — turns quality issues into concrete actions ──
def improvement_suggestions(quality, headers, rows):
    """Translate the data-quality report into a short, prioritized list of
    concrete things the user should fix or improve next."""
    items = []
    n = quality["row_count"]

    if quality["duplicate_rows"]:
        items.append(
            f"Remove {quality['duplicate_rows']} duplicate row(s) — they inflate totals and skew KPIs. "
            "Use the Data Cleaning tool to drop them automatically.")

    if quality["missing_values"]:
        worst = sorted(quality["columns"], key=lambda c: c["missing"], reverse=True)[:3]
        worst = [c for c in worst if c["missing"] > 0]
        cols_txt = ", ".join(f"{c['column']} ({c['missing']})" for c in worst)
        items.append(
            f"Fill {quality['missing_values']} missing value(s), concentrated in: {cols_txt}. "
            "Missing data reduces the accuracy of forecasts and business insights.")

    if quality["wrong_type_values"]:
        items.append(
            f"Fix {quality['wrong_type_values']} value(s) with an unexpected type (text in a numeric column, etc.) "
            "so calculations and charts read them correctly.")

    if quality["outlier_values"]:
        items.append(
            f"Review {quality['outlier_values']} outlier value(s) — they may be genuine extremes or data-entry "
            "errors. Capping/winsorizing them will make averages and trends more reliable.")

    if quality["quality_score"] < 70:
        items.append(
            f"Overall data quality score is {quality['quality_score']}/100 — below the recommended 70. "
            "Run the AI Data Cleaning module before relying on this dataset for decisions.")

    if n < 30:
        items.append(
            f"Only {n} row(s) available — insights, correlations and forecasts will be more reliable "
            "with a larger sample if more data can be collected.")

    if not items:
        items.append("No data-quality issues detected — this dataset is clean and ready to use.")

    return items[:6]


# ── 4d. "Related Insights" — correlations & column relationships ──────
def related_insights(headers, rows):
    """Surface relationships between columns the user might not have looked
    at yet: correlated numeric pairs and standout categorical breakdowns."""
    insights = []
    num_cols = get_numeric_cols(headers, rows)

    # Pairwise correlation between numeric columns (simple Pearson r)
    pairs = []
    for i in range(len(num_cols)):
        for j in range(i + 1, len(num_cols)):
            a_h, b_h = num_cols[i], num_cols[j]
            xs, ys = [], []
            for r in rows:
                fa, fb = to_float(r.get(a_h)), to_float(r.get(b_h))
                if fa is not None and fb is not None:
                    xs.append(fa); ys.append(fb)
            if len(xs) < 5:
                continue
            try:
                mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
                cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
                sx = (sum((x - mean_x) ** 2 for x in xs)) ** 0.5
                sy = (sum((y - mean_y) ** 2 for y in ys)) ** 0.5
                r = cov / (sx * sy) if sx and sy else 0
            except ZeroDivisionError:
                r = 0
            if abs(r) >= 0.5:
                pairs.append((a_h, b_h, round(r, 2)))

    pairs.sort(key=lambda p: abs(p[2]), reverse=True)
    for a_h, b_h, r in pairs[:3]:
        direction = "positive" if r > 0 else "negative"
        strength = "strong" if abs(r) >= 0.75 else "moderate"
        insights.append(
            f"{a_h} and {b_h} show a {strength} {direction} relationship (r = {r}) — "
            f"{'they tend to rise together' if r > 0 else 'as one rises, the other tends to fall'}.")

    # Related categorical breakdown: which category dominates the data
    cat_cols = get_categorical_cols(headers, rows)
    for h in cat_cols[:2]:
        counts = Counter(_clean_val(r.get(h, "")) for r in rows if not _is_missing(r.get(h, "")))
        if not counts:
            continue
        top_val, top_n = counts.most_common(1)[0]
        share = round(top_n / len(rows) * 100, 1) if rows else 0
        if share >= 40 and len(counts) > 1:
            insights.append(
                f"'{top_val}' makes up {share}% of all rows in {h} — worth checking if this concentration "
                "is expected or a sign of skewed/incomplete data collection.")

    if not insights:
        insights.append("No strong correlations or dominant categories detected between columns.")

    return insights[:5]


# ── 5. AI Report Generator — assembles everything above ─────────────
def build_full_report(headers, rows, dataset_name):
    quality = data_quality_report(headers, rows)
    story = data_story(headers, rows)
    biz = business_recommendations(headers, rows)
    forecast = predictive_forecast(headers, rows, periods=6)

    def _looks_like_id(h, rows):
        if h.lower() == "id" or h.lower().endswith("id") or h.lower().endswith("_id"):
            return True
        vals = numeric_values(rows, h)
        return bool(vals) and len(set(vals)) >= len(vals) * 0.98 and len(vals) > 5

    num_cols = [h for h in get_numeric_cols(headers, rows) if not _looks_like_id(h, rows)][:4]
    kpis = []
    for h in num_cols:
        vals = numeric_values(rows, h)
        if not vals:
            continue
        kpis.append({
            "label": h, "total": round(sum(vals), 2),
            "avg": round(sum(vals) / len(vals), 2),
            "min": round(min(vals), 2), "max": round(max(vals), 2),
        })

    chart_data = None
    dimension = biz.get("dimension") if biz.get("available") else pick_dimension_col(headers, rows)
    metric = biz.get("metric") if biz.get("available") else pick_metric_col(headers, rows)
    if dimension and metric:
        totals = Counter()
        for r in rows:
            key = _clean_val(r.get(dimension, ""))
            v = to_float(r.get(metric, ""))
            if key and v is not None:
                totals[key] += v
        top = totals.most_common(8)
        if top:
            chart_data = {"labels": [t[0] for t in top], "values": [round(t[1], 2) for t in top],
                          "dimension": dimension, "metric": metric}

    recommendations = []
    if biz.get("available"):
        for ins in biz["insights"]:
            for rec in ins["recommendations"]:
                if rec not in recommendations:
                    recommendations.append(rec)
    recommendations = recommendations[:8]

    return {
        "dataset_name": dataset_name,
        "summary": {
            "row_count": quality["row_count"], "col_count": quality["col_count"],
            "numeric_cols": len(get_numeric_cols(headers, rows)),
            "categorical_cols": len(get_categorical_cols(headers, rows)),
            "quality_score": quality["quality_score"],
            "duplicate_rows": quality["duplicate_rows"],
            "missing_values": quality["missing_values"],
        },
        "kpis": kpis,
        "insights": story.get("bullets", []) if story.get("available") else [],
        "recommendations": recommendations,
        "improvements": improvement_suggestions(quality, headers, rows),
        "related": related_insights(headers, rows),
        "forecast": forecast if forecast.get("available") else None,
        "chart": chart_data,
        "business": biz,
    }
