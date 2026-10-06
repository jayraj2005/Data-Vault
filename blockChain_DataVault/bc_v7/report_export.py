# ──────────────────────────────────────────────────────────────────────
# report_export.py — turns the dict from ai_engine.build_full_report()
# into a downloadable PDF or Excel workbook.
# ──────────────────────────────────────────────────────────────────────
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, ListFlowable, ListItem)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.chart import BarChart, LineChart, Reference

TEAL = colors.HexColor("#0D9488")
SLATE = colors.HexColor("#475569")
AMBER = colors.HexColor("#F59E0B")


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("H1c", parent=ss["Heading1"], textColor=TEAL, spaceAfter=6))
    ss.add(ParagraphStyle("H2c", parent=ss["Heading2"], textColor=SLATE, spaceBefore=14, spaceAfter=6))
    ss.add(ParagraphStyle("Body2", parent=ss["BodyText"], fontSize=10, leading=14))
    return ss


def build_pdf(report, buf=None):
    """report: dict from ai_engine.build_full_report(). Returns BytesIO of the PDF."""
    buf = buf or io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=1.6 * cm, bottomMargin=1.6 * cm,
                             leftMargin=1.8 * cm, rightMargin=1.8 * cm)
    ss = _styles()
    flow = []

    flow.append(Paragraph("DataVault — AI Analytics Report", ss["H1c"]))
    flow.append(Paragraph(f"Dataset: <b>{report['dataset_name']}</b>", ss["Body2"]))
    flow.append(Paragraph(f"Generated: {datetime.now().strftime('%d %b %Y, %H:%M')}", ss["Body2"]))
    flow.append(Spacer(1, 10))

    # ── Dataset summary ──
    s = report["summary"]
    flow.append(Paragraph("Dataset Summary", ss["H2c"]))
    sum_table = Table([
        ["Rows", s["row_count"], "Columns", s["col_count"]],
        ["Numeric Columns", s["numeric_cols"], "Categorical Columns", s["categorical_cols"]],
        ["Duplicate Rows", s["duplicate_rows"], "Missing Values", s["missing_values"]],
        ["Data Quality Score", f"{s['quality_score']} / 100", "", ""],
    ], colWidths=[4.2 * cm, 3.3 * cm, 4.2 * cm, 3.3 * cm])
    sum_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F1F5F9")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#F1F5F9")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
    ]))
    flow.append(sum_table)

    # ── KPIs ──
    if report.get("kpis"):
        flow.append(Paragraph("Key Performance Indicators", ss["H2c"]))
        data = [["Metric", "Total", "Average", "Min", "Max"]]
        for k in report["kpis"]:
            data.append([k["label"], f"{k['total']:,}", f"{k['avg']:,}", f"{k['min']:,}", f"{k['max']:,}"])
        kpi_table = Table(data, colWidths=[4.5 * cm, 3.2 * cm, 3.2 * cm, 2.8 * cm, 2.8 * cm])
        kpi_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), TEAL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ]))
        flow.append(kpi_table)

    # ── Chart: top categories bar chart ──
    if report.get("chart"):
        ch = report["chart"]
        flow.append(Paragraph(f"{ch['metric']} by {ch['dimension']}", ss["H2c"]))
        d = Drawing(460, 200)
        bc = VerticalBarChart()
        bc.x, bc.y, bc.height, bc.width = 40, 20, 150, 400
        bc.data = [ch["values"]]
        bc.categoryAxis.categoryNames = [str(l)[:12] for l in ch["labels"]]
        bc.categoryAxis.labels.angle = 30
        bc.categoryAxis.labels.dy = -10
        bc.categoryAxis.labels.fontSize = 7
        bc.valueAxis.valueMin = 0
        bc.bars[0].fillColor = TEAL
        d.add(bc)
        flow.append(d)

    # ── Business Insights ──
    flow.append(Paragraph("Business Insights", ss["H2c"]))
    if report.get("insights"):
        flow.append(ListFlowable(
            [ListItem(Paragraph(b, ss["Body2"])) for b in report["insights"]],
            bulletType="bullet"))
    else:
        flow.append(Paragraph("No significant insights detected.", ss["Body2"]))

    # ── Recommendations ──
    flow.append(Paragraph("Recommendations", ss["H2c"]))
    if report.get("recommendations"):
        flow.append(ListFlowable(
            [ListItem(Paragraph(r, ss["Body2"])) for r in report["recommendations"]],
            bulletType="bullet"))
    else:
        flow.append(Paragraph("No specific actions recommended — metrics look stable.", ss["Body2"]))

    # ── What to Improve ──
    flow.append(Paragraph("What to Improve", ss["H2c"]))
    if report.get("improvements"):
        flow.append(ListFlowable(
            [ListItem(Paragraph(i, ss["Body2"])) for i in report["improvements"]],
            bulletType="bullet"))
    else:
        flow.append(Paragraph("No improvements needed right now.", ss["Body2"]))

    # ── Related Insights ──
    flow.append(Paragraph("Related Insights", ss["H2c"]))
    if report.get("related"):
        flow.append(ListFlowable(
            [ListItem(Paragraph(i, ss["Body2"])) for i in report["related"]],
            bulletType="bullet"))
    else:
        flow.append(Paragraph("No related patterns detected.", ss["Body2"]))

    # ── Forecast ──
    if report.get("forecast"):
        fc = report["forecast"]
        flow.append(Paragraph(f"Forecast — {fc['column']} (next {len(fc['forecast']['values'])} periods)", ss["H2c"]))
        flow.append(Paragraph(f"Trend detected: <b>{fc['trend']}</b> (R² = {fc['r2']})", ss["Body2"]))

        d2 = Drawing(460, 200)
        lc = HorizontalLineChart()
        lc.x, lc.y, lc.height, lc.width = 40, 20, 150, 400
        hist = fc["history"]["values"][-20:]
        fcvals = fc["forecast"]["values"]
        combined = hist + fcvals
        lc.data = [combined]
        lc.categoryAxis.categoryNames = [""] * len(combined)
        lc.valueAxis.valueMin = min(combined) * 0.9 if min(combined) >= 0 else min(combined) * 1.1
        lc.lines[0].strokeColor = AMBER
        lc.lines[0].strokeWidth = 2
        d2.add(lc)
        flow.append(d2)
        flow.append(Paragraph(
            f"Solid portion = historical data, remainder = predicted values.", ss["Body2"]))

    doc.build(flow)
    buf.seek(0)
    return buf


def build_excel(report, buf=None):
    """report: dict from ai_engine.build_full_report(). Returns BytesIO of the .xlsx file."""
    buf = buf or io.BytesIO()
    wb = Workbook()

    head_fill = PatternFill("solid", fgColor="0D9488")
    head_font = Font(color="FFFFFF", bold=True)
    bold = Font(bold=True)

    # ── Summary sheet ──
    ws = wb.active
    ws.title = "Summary"
    ws.append(["DataVault — AI Analytics Report"])
    ws["A1"].font = Font(bold=True, size=14, color="0D9488")
    ws.append([f"Dataset: {report['dataset_name']}"])
    ws.append([f"Generated: {datetime.now().strftime('%d %b %Y, %H:%M')}"])
    ws.append([])
    s = report["summary"]
    for k, v in [("Rows", s["row_count"]), ("Columns", s["col_count"]),
                 ("Numeric Columns", s["numeric_cols"]), ("Categorical Columns", s["categorical_cols"]),
                 ("Duplicate Rows", s["duplicate_rows"]), ("Missing Values", s["missing_values"]),
                 ("Data Quality Score", s["quality_score"])]:
        ws.append([k, v])
        ws.cell(ws.max_row, 1).font = bold
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 20

    # ── KPI sheet ──
    ws2 = wb.create_sheet("KPIs")
    ws2.append(["Metric", "Total", "Average", "Min", "Max"])
    for c in ws2[1]:
        c.fill = head_fill
        c.font = head_font
    for k in report.get("kpis", []):
        ws2.append([k["label"], k["total"], k["avg"], k["min"], k["max"]])
    for col in "ABCDE":
        ws2.column_dimensions[col].width = 16

    if report.get("kpis"):
        chart = BarChart()
        chart.title = "KPI Totals"
        chart.style = 10
        data = Reference(ws2, min_col=2, min_row=1, max_row=ws2.max_row, max_col=2)
        cats = Reference(ws2, min_col=1, min_row=2, max_row=ws2.max_row)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        ws2.add_chart(chart, "G2")

    # ── Insights sheet ──
    ws3 = wb.create_sheet("Insights")
    ws3.append(["Business Insights"])
    ws3["A1"].font = bold
    for b in report.get("insights", []):
        ws3.append([b])
    ws3.column_dimensions["A"].width = 80

    # ── Recommendations sheet ──
    ws4 = wb.create_sheet("Recommendations")
    ws4.append(["Recommendation"])
    ws4["A1"].font = head_font
    ws4["A1"].fill = head_fill
    for r in report.get("recommendations", []):
        ws4.append([r])
    ws4.column_dimensions["A"].width = 50

    # ── What to Improve sheet ──
    ws_imp = wb.create_sheet("What to Improve")
    ws_imp.append(["Suggested Improvement"])
    ws_imp["A1"].font = head_font
    ws_imp["A1"].fill = head_fill
    for i in report.get("improvements", []):
        ws_imp.append([i])
    ws_imp.column_dimensions["A"].width = 90

    # ── Related Insights sheet ──
    ws_rel = wb.create_sheet("Related Insights")
    ws_rel.append(["Related Insight"])
    ws_rel["A1"].font = head_font
    ws_rel["A1"].fill = head_fill
    for i in report.get("related", []):
        ws_rel.append([i])
    ws_rel.column_dimensions["A"].width = 90

    # ── Forecast sheet ──
    ws5 = wb.create_sheet("Forecast")
    fc = report.get("forecast")
    if fc:
        ws5.append(["Period", "Value", "Type"])
        for c in ws5[1]:
            c.fill = head_fill
            c.font = head_font
        for lbl, val in zip(fc["history"]["labels"], fc["history"]["values"]):
            ws5.append([lbl, val, "Historical"])
        for lbl, val in zip(fc["forecast"]["labels"], fc["forecast"]["values"]):
            ws5.append([lbl, val, "Forecast"])

        chart2 = LineChart()
        chart2.title = f"{fc['column']} Forecast ({fc['trend']})"
        chart2.style = 12
        data = Reference(ws5, min_col=2, min_row=1, max_row=ws5.max_row)
        chart2.add_data(data, titles_from_data=True)
        ws5.add_chart(chart2, "E2")
        for col in "ABC":
            ws5.column_dimensions[col].width = 14
    else:
        ws5.append(["No forecast available for this dataset."])

    wb.save(buf)
    buf.seek(0)
    return buf
