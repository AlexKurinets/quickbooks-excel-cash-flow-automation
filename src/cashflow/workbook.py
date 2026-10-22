"""Fill the firm's Excel template without letting anything else in the file change.

General spreadsheet libraries load a workbook into their own model and write a new file from it,
which drops whatever the model does not cover. openpyxl, for example, loses text boxes and
rebuilds charts with less formatting. So this module never re-saves the workbook. It edits the
worksheet XML inside the .xlsx package directly:

1. `locate` finds every target cell by its row label and month header, never by a fixed address,
   and refuses a template where a label is missing, appears twice, or an input cell holds a formula.
2. `patch` writes values into those input cells only and copies every other part of the package
   byte for byte.
3. `recalculate` evaluates the template's own formulas (with the `formulas` library) on the
   patched file, and `patch` stores the results as each formula cell's cached value, so a viewer
   that does not recalculate (a phone's mail preview, for one) shows real numbers. The workbook is
   also flagged for a full recalculation when Excel opens it.
4. `verify` proves the formula inventory, every untouched part and every untouched cell match the
   template exactly.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml
from lxml import etree

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
Q = f"{{{NS}}}"
CELL_REF = re.compile(r"^([A-Z]{1,3})([0-9]+)$")
CALC_KEY = re.compile(r"^'\[[^\]]+\](.+)'!([A-Z]{1,3}[0-9]+)$")


class TemplateError(Exception):
    """The template does not have the shape the map describes; nothing is written."""


# -- the map ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class InputSpec:
    label: str
    first_month_only: bool = False


@dataclass(frozen=True)
class TemplateMap:
    sheet: str
    label_column: str
    month_header: str
    months: tuple[str, ...]
    fields: dict[str, str]
    inputs: dict[str, InputSpec]
    outputs: dict[str, str]

    @classmethod
    def load(cls, path: Path) -> TemplateMap:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        inputs = {}
        for key, spec in raw["inputs"].items():
            if isinstance(spec, str):
                inputs[key] = InputSpec(spec)
            else:
                inputs[key] = InputSpec(spec["label"], spec.get("months") == "first")
        return cls(raw["sheet"], raw.get("label_column", "A"), raw["month_header"], tuple(raw["months"]),
                   dict(raw["fields"]), inputs, dict(raw["outputs"]))


# -- package helpers ----------------------------------------------------------------------------------

def sheet_parts(zf: zipfile.ZipFile) -> dict[str, str]:
    """Sheet name -> worksheet part path, read from workbook.xml and its relationships."""
    wb = etree.fromstring(zf.read("xl/workbook.xml"))
    rels = etree.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels.iter(f"{{{PKG_REL_NS}}}Relationship")}
    out = {}
    for sheet in wb.iter(f"{Q}sheet"):
        target = targets[sheet.get(f"{{{REL_NS}}}id")]
        out[sheet.get("name")] = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
    return out


def _split(ref: str) -> tuple[str, int]:
    m = CELL_REF.match(ref)
    if not m:
        raise TemplateError(f"bad cell reference {ref!r}")
    return m.group(1), int(m.group(2))


def col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def col_letters(index: int) -> str:
    out = ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _cells(root: etree._Element) -> dict[str, etree._Element]:
    return {c.get("r"): c for c in root.iter(f"{Q}c")}


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = etree.fromstring(zf.read("xl/sharedStrings.xml"))
    return ["".join(si.itertext()) for si in root.iter(f"{Q}si")]


def _text(c: etree._Element, strings: list[str]) -> str | None:
    t = c.get("t")
    if t == "s":
        v = c.find(f"{Q}v")
        return strings[int(v.text)] if v is not None and v.text is not None else None
    if t == "inlineStr":
        return "".join(c.find(f"{Q}is").itertext())
    if t == "str":
        v = c.find(f"{Q}v")
        return v.text if v is not None else None
    return None


# -- locating cells by label ------------------------------------------------------------------------

@dataclass
class Layout:
    sheet: str
    rows: dict[str, int]
    columns: dict[str, str]
    fields: dict[str, str]
    first_month_only: set[str] = field(default_factory=set)

    def cell(self, key: str, month: str) -> str:
        return f"{self.columns[month]}{self.rows[key]}"


def locate(xlsx: bytes, tmap: TemplateMap) -> Layout:
    zf = zipfile.ZipFile(io.BytesIO(xlsx))
    parts = sheet_parts(zf)
    if tmap.sheet not in parts:
        raise TemplateError(f"the template has no sheet named {tmap.sheet!r}")
    root = etree.fromstring(zf.read(parts[tmap.sheet]))
    strings = _shared_strings(zf)
    cells = _cells(root)
    by_label: dict[str, list[int]] = defaultdict(list)
    for ref, c in cells.items():
        letters, row = _split(ref)
        if letters == tmap.label_column:
            text = _text(c, strings)
            if text:
                by_label[text.strip()].append(row)

    def row_of(label: str) -> int:
        found = by_label.get(label, [])
        if not found:
            raise TemplateError(f"row label {label!r} is missing from column {tmap.label_column} of {tmap.sheet!r}")
        if len(found) > 1:
            raise TemplateError(f"row label {label!r} appears {len(found)} times (rows {found}); it must be unique")
        return found[0]

    header = row_of(tmap.month_header)
    columns = {}
    for ref, c in cells.items():
        letters, row = _split(ref)
        if row == header and (text := _text(c, strings)) and text.strip() in tmap.months:
            if text.strip() in columns:
                raise TemplateError(f"month header {text.strip()!r} appears twice in row {header}")
            columns[text.strip()] = letters
    missing = [m for m in tmap.months if m not in columns]
    if missing:
        raise TemplateError(f"month headers {missing} are missing from row {header}")

    rows = {key: row_of(spec.label) for key, spec in tmap.inputs.items()}
    rows.update({key: row_of(label) for key, label in tmap.outputs.items()})
    fields = {}
    for key, label in tmap.fields.items():
        fields[key] = f"{col_letters(col_index(tmap.label_column) + 1)}{row_of(label)}"
    layout = Layout(tmap.sheet, rows, columns, fields,
                    {k for k, spec in tmap.inputs.items() if spec.first_month_only})

    def is_formula(ref: str) -> bool:
        c = cells.get(ref)
        return c is not None and c.find(f"{Q}f") is not None

    for key in tmap.inputs:
        months = tmap.months[:1] if key in layout.first_month_only else tmap.months
        for m in months:
            if is_formula(layout.cell(key, m)):
                raise TemplateError(f"input cell {layout.cell(key, m)} ({tmap.inputs[key].label}, {m}) holds a "
                                    f"formula; the automation never writes over a formula")
    for key in tmap.outputs:
        for m in tmap.months:
            if not is_formula(layout.cell(key, m)):
                raise TemplateError(f"output cell {layout.cell(key, m)} ({tmap.outputs[key]}, {m}) should be a "
                                    f"formula but is not; someone may have typed over it")
    for key, ref in fields.items():
        if is_formula(ref):
            raise TemplateError(f"field cell {ref} ({tmap.fields[key]}) holds a formula")
    return layout


# -- patching -----------------------------------------------------------------------------------------

def _number(value) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f") if value == value.to_integral() else format(value, "f")
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _find_or_create(root: etree._Element, ref: str) -> etree._Element:
    letters, row_no = _split(ref)
    data = root.find(f"{Q}sheetData")
    row = None
    for r in data.iter(f"{Q}row"):
        n = int(r.get("r"))
        if n == row_no:
            row = r
            break
        if n > row_no:
            row = etree.Element(f"{Q}row", r=str(row_no))
            r.addprevious(row)
            break
    if row is None:
        row = etree.SubElement(data, f"{Q}row", r=str(row_no))
    for c in row.iter(f"{Q}c"):
        c_letters, _ = _split(c.get("r"))
        if c_letters == letters:
            return c
        if col_index(c_letters) > col_index(letters):
            new = etree.Element(f"{Q}c", r=ref)
            c.addprevious(new)
            return new
    return etree.SubElement(row, f"{Q}c", r=ref)


def _set_input(c: etree._Element, value) -> None:
    if c.find(f"{Q}f") is not None:
        raise TemplateError(f"refusing to write into {c.get('r')}: it holds a formula")
    for child in list(c):
        c.remove(child)
    c.attrib.pop("t", None)
    if value is None:
        return
    if isinstance(value, str):
        c.set("t", "inlineStr")
        t = etree.SubElement(etree.SubElement(c, f"{Q}is"), f"{Q}t")
        t.text = value
        if value != value.strip():
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        return
    etree.SubElement(c, f"{Q}v").text = _number(value)


def _set_cached(c: etree._Element, value) -> None:
    f = c.find(f"{Q}f")
    if f is None:
        raise TemplateError(f"{c.get('r')} is not a formula cell")
    old = c.find(f"{Q}v")
    if old is not None:
        c.remove(old)
    c.attrib.pop("t", None)
    v = etree.Element(f"{Q}v")
    if isinstance(value, ExcelError):
        c.set("t", "e")
        v.text = value.code
    elif isinstance(value, bool):
        c.set("t", "b")
        v.text = "1" if value else "0"
    elif isinstance(value, str):
        c.set("t", "str")
        v.text = value
    else:
        v.text = _number(value)
    f.addnext(v)


def _full_calc_on_load(xml: bytes) -> bytes | None:
    root = etree.fromstring(xml)
    calc = root.find(f"{Q}calcPr")
    if calc is not None and calc.get("fullCalcOnLoad") == "1":
        return None
    if calc is None:
        calc = etree.Element(f"{Q}calcPr")
        anchor = root.find(f"{Q}definedNames")
        (anchor if anchor is not None else root.find(f"{Q}sheets")).addnext(calc)
    calc.set("fullCalcOnLoad", "1")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def patch(xlsx: bytes, inputs: dict[str, dict[str, object]], cached: dict[str, dict[str, object]] | None = None
          ) -> bytes:
    """Write `inputs` (sheet -> ref -> value) into non-formula cells and `cached` results into formula cells."""
    zin = zipfile.ZipFile(io.BytesIO(xlsx))
    parts = sheet_parts(zin)
    changed: dict[str, bytes] = {}
    for sheet in set(inputs) | set(cached or {}):
        root = etree.fromstring(zin.read(parts[sheet]))
        for ref, value in inputs.get(sheet, {}).items():
            _set_input(_find_or_create(root, ref), value)
        existing = _cells(root)
        for ref, value in (cached or {}).get(sheet, {}).items():
            if ref not in existing:
                raise TemplateError(f"{sheet}!{ref} is not a formula cell")
            _set_cached(existing[ref], value)
        changed[parts[sheet]] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    workbook_xml = _full_calc_on_load(zin.read("xl/workbook.xml"))
    if workbook_xml is not None:
        changed["xl/workbook.xml"] = workbook_xml
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info, changed.get(info.filename, zin.read(info.filename)))
    return out.getvalue()


# -- recalculation --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ExcelError:
    code: str

    def __str__(self) -> str:
        return self.code


def _plain(value):
    import numpy as np

    if isinstance(value, np.generic):
        value = value.item()
    name = type(value).__name__
    if name == "XlError":
        return ExcelError(str(value))
    if name in {"_Empty", "Empty"} or value is None:
        return 0
    if isinstance(value, float):
        return round(value, 9)  # binary float noise (101602.73000000001) would show in previews
    return value


def recalculate(xlsx: bytes) -> dict[str, dict[str, object]]:
    """Evaluate every formula in the workbook. Returns sheet name -> cell ref -> value."""
    os.environ.setdefault("TQDM_DISABLE", "1")
    import formulas

    names = list(sheet_parts(zipfile.ZipFile(io.BytesIO(xlsx))))
    upper = {n.upper(): n for n in names}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "workbook.xlsx"
        path.write_bytes(xlsx)
        solution = formulas.ExcelModel().loads(str(path)).finish().calculate()
    out: dict[str, dict[str, object]] = defaultdict(dict)
    for key, ranges in solution.items():
        m = CALC_KEY.match(key)
        if not m or m.group(1) not in upper:
            continue
        out[upper[m.group(1)]][m.group(2)] = _plain(ranges.value[0][0])
    return dict(out)


# -- verification ---------------------------------------------------------------------------------------

def formula_inventory(xlsx: bytes) -> dict[str, str]:
    """Every formula in the workbook, as the exact XML of its <f> element, keyed 'Sheet!A1'."""
    zf = zipfile.ZipFile(io.BytesIO(xlsx))
    out = {}
    for sheet, part in sheet_parts(zf).items():
        for ref, c in _cells(etree.fromstring(zf.read(part))).items():
            f = c.find(f"{Q}f")
            if f is not None:
                out[f"{sheet}!{ref}"] = etree.tostring(f).decode()
    return out


def _cell_fingerprints(xlsx: bytes, skip: dict[str, set[str]]) -> dict[str, str]:
    """Every cell's XML except allowed inputs, with formula cells' cached values removed."""
    zf = zipfile.ZipFile(io.BytesIO(xlsx))
    out = {}
    for sheet, part in sheet_parts(zf).items():
        root = etree.fromstring(zf.read(part))
        for ref, c in _cells(root).items():
            if ref in skip.get(sheet, set()):
                continue
            copy = etree.fromstring(etree.tostring(c))
            if copy.find(f"{Q}f") is not None:
                for v in copy.findall(f"{Q}v"):
                    copy.remove(v)
                copy.attrib.pop("t", None)
            out[f"{sheet}!{ref}"] = etree.tostring(copy).decode()
        data = root.find(f"{Q}sheetData")
        root.remove(data)
        out[f"{sheet}!<sheet>"] = etree.tostring(root).decode()
    return out


@dataclass
class Verification:
    formula_count: int
    formulas_unchanged: bool
    changed_formulas: list[str]
    untouched_parts: list[str]
    changed_parts: list[str]
    unexpected_parts: list[str]
    unexpected_cells: list[str]

    @property
    def ok(self) -> bool:
        return self.formulas_unchanged and not self.unexpected_parts and not self.unexpected_cells


def verify(template: bytes, output: bytes, written: dict[str, set[str]]) -> Verification:
    before, after = formula_inventory(template), formula_inventory(output)
    changed_formulas = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    zt, zo = zipfile.ZipFile(io.BytesIO(template)), zipfile.ZipFile(io.BytesIO(output))
    sheets = set(sheet_parts(zt).values())
    untouched, changed, unexpected = [], [], []
    names = zt.namelist()
    if names != zo.namelist():
        unexpected.append("package part list")
    for name in names:
        if name in zo.namelist() and zt.read(name) == zo.read(name):
            untouched.append(name)
        else:
            changed.append(name)
            if name not in sheets and name != "xl/workbook.xml":
                unexpected.append(name)
    fb, fa = _cell_fingerprints(template, written), _cell_fingerprints(output, written)
    unexpected_cells = sorted(k for k in fb.keys() | fa.keys() if fb.get(k) != fa.get(k))
    return Verification(len(before), not changed_formulas, changed_formulas, untouched, changed, unexpected,
                        unexpected_cells)


# -- the whole fill -------------------------------------------------------------------------------------

@dataclass
class Filled:
    workbook: bytes
    layout: Layout
    values: dict[str, dict[str, object]]
    verification: Verification

    def value(self, key: str, month: str):
        return self.values.get(self.layout.sheet, {}).get(self.layout.cell(key, month))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.workbook).hexdigest()


def fill(template: bytes, layout: Layout, inputs: dict[str, object]) -> Filled:
    """inputs: cell ref on the mapped sheet -> value. Returns the finished workbook and its proof."""
    sheet = layout.sheet
    staged = patch(template, {sheet: inputs})
    values = recalculate(staged)
    formula_refs: dict[str, list[str]] = defaultdict(list)
    for key in formula_inventory(template):
        s, ref = key.rsplit("!", 1)
        formula_refs[s].append(ref)
    cached = {s: {r: values[s][r] for r in refs if r in values.get(s, {})} for s, refs in formula_refs.items()}
    final = patch(staged, {}, cached)
    return Filled(final, layout, values, verify(template, final, {sheet: set(inputs)}))
