"""Build the synthetic house template, templates/monthly-cash-flow.xlsx.

The template stands in for a workbook the firm's staff built and maintain by hand: a monthly
indirect-method statement of cash flows with subtotals, a check row with conditional formatting,
a reviewer drop-down, a summary sheet with a combined chart and a text box, defined names and a
hidden list sheet. The automation never generates this file; it only fills the input cells.

    python scripts/build_template.py [--out PATH] [--extra-row]

--extra-row inserts a blank memo row under "Net income", the kind of edit staff make, so the
tests can prove that cells are found by label and not by address.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import xlsxwriter
from xlsxwriter.utility import xl_col_to_name, xl_rowcol_to_cell

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

OPERATING = [
    "Depreciation and amortization",
    "(Increase) decrease in accounts receivable",
    "(Increase) decrease in inventory",
    "(Increase) decrease in prepaid expenses and other current assets",
    "Increase (decrease) in accounts payable",
    "Increase (decrease) in credit cards payable",
    "Increase (decrease) in accrued payroll and payroll taxes",
    "Increase (decrease) in sales tax payable",
    "Increase (decrease) in deferred revenue",
    "Increase (decrease) in other current liabilities",
]
INVESTING = ["Purchases of property and equipment", "Other investing activities"]
FINANCING = [
    "Proceeds from (repayments of) loans, net",
    "Borrowings (repayments) on line of credit, net",
    "Owner contributions",
    "Owner distributions",
]
REVIEWERS = ["MQ", "DH", "TN"]
DISCLAIMER = (
    "Prepared from the client's QuickBooks Online records without audit or review. "
    "For management use only."
)


def build(out: Path, extra_row: bool = False) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(out))
    wb.set_properties({"title": "Statement of Cash Flows", "author": "Client accounting team"})

    title = wb.add_format({"bold": True, "font_size": 14})
    label = wb.add_format({"bold": True})
    section = wb.add_format({"bold": True, "bg_color": "#E8EEF4"})
    note = wb.add_format({"italic": True, "font_color": "#555555"})
    head = wb.add_format({"bold": True, "align": "center", "bottom": 1})
    money = wb.add_format({"num_format": '#,##0.00;(#,##0.00);"-"'})
    total = wb.add_format({"num_format": '#,##0.00;(#,##0.00);"-"', "bold": True, "top": 1})
    grand = wb.add_format({"num_format": '#,##0.00;(#,##0.00);"-"', "bold": True, "top": 1, "bottom": 6})
    inputf = wb.add_format({"bg_color": "#FFF8E1"})
    alert = wb.add_format({"bg_color": "#F4CCCC", "font_color": "#9C0006"})

    ws = wb.add_worksheet("Cash Flow")
    ws.set_column("A:A", 62)
    ws.set_column("B:M", 13)
    ws.set_column("N:N", 15)
    ws.set_landscape()
    ws.fit_to_pages(1, 0)
    ws.write("A1", "Statement of Cash Flows", title)
    for r, name in enumerate(["Client", "Fiscal year", "Books closed through", "Source"], start=1):
        ws.write(r, 0, name, label)
        ws.write_blank(r, 1, None, inputf)

    row = 6
    ws.write(row, 0, "Fiscal month", head)
    for m, name in enumerate(MONTHS):
        ws.write(row, 1 + m, name, head)
    ws.write(row, 13, "Year to date", head)
    ws.freeze_panes(row + 1, 1)

    rows: dict[str, int] = {}

    def put(text: str, fmt=None) -> int:
        nonlocal row
        row += 1
        ws.write(row, 0, text, fmt)
        rows[text] = row
        return row

    def blank() -> None:
        nonlocal row
        row += 1

    def flow_row(text: str) -> int:
        r = put(text)
        for m in range(12):
            ws.write_blank(r, 1 + m, None, money)
        ws.write_formula(r, 13, f"=SUM({xl_rowcol_to_cell(r, 1)}:{xl_rowcol_to_cell(r, 12)})", money)
        return r

    def subtotal(text: str, first: int, last: int, fmt=total) -> int:
        r = put(text, label)
        for m in range(13):
            col = xl_col_to_name(1 + m)
            ws.write_formula(r, 1 + m, f"=SUM({col}{first + 1}:{col}{last + 1})", fmt)
        return r

    put("Cash flows from operating activities", section)
    first_op = flow_row("Net income")
    if extra_row:
        put("Memo: owner's notes for this client", note)
    put("Adjustments to reconcile net income to net cash from operating activities:", note)
    for text in OPERATING:
        last_op = flow_row(text)
    op = subtotal("Net cash provided by (used in) operating activities", first_op, last_op)
    blank()
    put("Cash flows from investing activities", section)
    first_inv = flow_row(INVESTING[0])
    last_inv = flow_row(INVESTING[1])
    inv = subtotal("Net cash provided by (used in) investing activities", first_inv, last_inv)
    blank()
    put("Cash flows from financing activities", section)
    first_fin = flow_row(FINANCING[0])
    for text in FINANCING[1:]:
        last_fin = flow_row(text)
    fin = subtotal("Net cash provided by (used in) financing activities", first_fin, last_fin)
    blank()

    net = put("Net increase (decrease) in cash", label)
    for m in range(13):
        col = xl_col_to_name(1 + m)
        ws.write_formula(net, 1 + m, f"={col}{op + 1}+{col}{inv + 1}+{col}{fin + 1}", total)
    begin = put("Cash at beginning of period")
    end = put("Cash at end of period", label)
    ws.write_blank(begin, 1, None, money)
    for m in range(1, 12):
        ws.write_formula(begin, 1 + m, f"={xl_col_to_name(m)}{end + 1}", money)
    ws.write_formula(begin, 13, f"=B{begin + 1}", money)
    for m in range(13):
        col = xl_col_to_name(1 + m)
        ws.write_formula(end, 1 + m, f"={col}{begin + 1}+{col}{net + 1}", grand)
    blank()
    per_bs = put("Cash per balance sheet")
    diff = put("Difference", note)
    for m in range(12):
        col = xl_col_to_name(1 + m)
        ws.write_blank(per_bs, 1 + m, None, money)
        ws.write_formula(diff, 1 + m, f'=IF({col}{per_bs + 1}="","",ROUND({col}{end + 1}-{col}{per_bs + 1},2))', money)
    ws.conditional_format(diff, 1, diff, 12, {"type": "formula", "criteria": f'=AND(B{diff + 1}<>"",B{diff + 1}<>0)',
                                              "format": alert})
    blank()
    reviewed = put("Reviewed by", label)
    ws.write_blank(reviewed, 1, None, inputf)
    ws.data_validation(reviewed, 1, reviewed, 1, {"validate": "list", "source": "=Lists!$A$1:$A$3"})
    put("Review date", label)

    wb.define_name("NetChangeInCash", f"='Cash Flow'!$B${net + 1}:$M${net + 1}")
    wb.define_name("EndingCash", f"='Cash Flow'!$B${end + 1}:$M${end + 1}")

    s = wb.add_worksheet("Summary")
    s.set_column("A:A", 12)
    s.set_column("B:F", 16)
    s.write("A1", "Cash summary", title)
    s.write_formula("A2", "='Cash Flow'!B2", label)
    s.write_formula("A3", "=\"Fiscal year \"&'Cash Flow'!B3", note)
    for c, text in enumerate(["Month", "Operating", "Investing", "Financing", "Net change", "Ending cash"]):
        s.write(4, c, text, head)
    for m, name in enumerate(MONTHS):
        r = 5 + m
        col = xl_col_to_name(1 + m)
        s.write(r, 0, name)
        s.write_formula(r, 1, f"='Cash Flow'!{col}{op + 1}", money)
        s.write_formula(r, 2, f"='Cash Flow'!{col}{inv + 1}", money)
        s.write_formula(r, 3, f"='Cash Flow'!{col}{fin + 1}", money)
        s.write_formula(r, 4, f"='Cash Flow'!{col}{net + 1}", money)
        s.write_formula(r, 5, f"=IF('Cash Flow'!{col}{per_bs + 1}=\"\",NA(),'Cash Flow'!{col}{end + 1})", money)
    s.write(17, 0, "Total", label)
    for c in range(1, 5):
        col = xl_col_to_name(c)
        s.write_formula(17, c, f"=SUM({col}6:{col}17)", total)

    bars = wb.add_chart({"type": "column"})
    bars.add_series({"name": "Net change", "categories": "=Summary!$A$6:$A$17", "values": "=Summary!$E$6:$E$17",
                     "fill": {"color": "#7FA7C9"}})
    line = wb.add_chart({"type": "line"})
    line.add_series({"name": "Ending cash", "categories": "=Summary!$A$6:$A$17", "values": "=Summary!$F$6:$F$17",
                     "line": {"color": "#1F3B57", "width": 2.25}, "marker": {"type": "circle", "size": 5},
                     "y2_axis": True})
    bars.combine(line)
    bars.set_title({"name": "Monthly cash movement"})
    bars.set_legend({"position": "bottom"})
    bars.set_y_axis({"num_format": "#,##0", "major_gridlines": {"visible": True, "line": {"color": "#DDDDDD"}}})
    line.set_y2_axis({"num_format": "#,##0"})
    s.insert_chart("H2", bars, {"x_scale": 1.4, "y_scale": 1.25})
    s.insert_textbox("H22", DISCLAIMER, {"width": 500, "height": 48, "font": {"size": 9, "italic": True},
                                         "line": {"color": "#999999"}})

    lists = wb.add_worksheet("Lists")
    for i, initials in enumerate(REVIEWERS):
        lists.write(i, 0, initials)
    lists.hide()

    wb.close()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "templates" / "monthly-cash-flow.xlsx")
    parser.add_argument("--extra-row", action="store_true")
    args = parser.parse_args()
    print(build(args.out, args.extra_row))


if __name__ == "__main__":
    main()
