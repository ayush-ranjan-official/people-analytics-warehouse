"""Offline checks for the LookML project, since there is no Looker instance to run its validator.

    python scripts/validate_lookml.py                # validate lookml/
    python scripts/validate_lookml.py path/to/lookml
    python scripts/validate_lookml.py --strict       # warnings fail too
    python scripts/validate_lookml.py --marts ''     # skip the column check against dbt/models/marts

What it checks:
- every *.lkml file parses (lkml) and every *.dashboard.lookml file parses (YAML); no sql: block runs
  on into the next field for want of its ';;', and list parameters (fields, drill_fields, timeframes,
  ...) are [...] lists;
- every manifest constant used as @{name} is defined;
- views: unique names, a table (sql_table_name or derived_table), exactly one primary key, unique
  field names (dimension groups expanded into their timeframe and interval fields); field types,
  timeframes, intervals and value_format_names are ones Looker knows;
- sql_table_name names a dbt mart, and every ${TABLE}.column is a column its schema file lists;
- every ${...} and Liquid field reference resolves, and a field that reads another view is only used
  in explores that join that view;
- every measure has a description (every dimension too, as a warning); type: number measures build on
  measures, and aggregate measures (sum, average, median, ...) don't reference measures;
- model includes match files; explores and joins name real views; every join has a relationship, a
  known type and a join condition that references the joined view; explore SQL other than
  sql_always_having doesn't use measures; a field an explore keeps doesn't use (in its SQL or
  filters) a field that a fields: parameter leaves out; median, percentile and list measures, and
  raw SQL aggregates in type: number measures, aren't computed over fanned-out rows; access filters
  and always filters use fields in the explore; access grants and datagroups exist;
- dashboards: every tile's model, explore, fields, sorts, pivots, filters and listeners resolve,
  listen maps filter names to fields, and pivots, sorts and fill_fields are among the tile's fields.

Exits 1 on any error (with --strict, on any warning too).
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

import lkml
import yaml

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "lookml"
MARTS = Path(__file__).resolve().parent.parent / "dbt" / "models" / "marts"   # for column checks

# Each check and what its count is a count of.
CHECKS = {
    "parse": "files",
    "constants": "uses",
    "views": "views",
    "references": "references",
    "descriptions": "fields",
    "measures": "measures",
    "model": "includes",
    "explores": "explores",
    "fanout": "explores",
    "dashboards": "tiles",
}

# type: time with no timeframes: Looker's default set (raw kept so ${x_raw} still resolves).
TIMEFRAMES = ["raw", "date", "day_of_month", "day_of_week", "day_of_week_index", "day_of_year", "hour",
              "hour_of_day", "minute", "month", "month_name", "month_num", "quarter", "quarter_of_year", "time",
              "time_of_day", "week", "week_of_year", "year"]
TIME_TIMEFRAMES = set(TIMEFRAMES) | {"yesno", "second", "millisecond", "microsecond", "fiscal_month_num",
                                     "fiscal_quarter", "fiscal_quarter_of_year", "fiscal_year"}
TIMEFRAME_STEP = re.compile(r"(?:hour(?:2|3|4|6|8|12)|minute(?:2|3|4|5|6|10|12|15|20|30)"
                            r"|millisecond(?:2|4|5|8|10|20|25|40|50|100|125|200|250|500))\Z")
INTERVALS = ["day", "hour", "minute", "month", "quarter", "second", "week", "year"]  # type: duration
RELATIONSHIPS = {"many_to_one", "one_to_many", "one_to_one", "many_to_many"}
REVERSED = {"many_to_one": "one_to_many", "one_to_many": "many_to_one",
            "one_to_one": "one_to_one", "many_to_many": "many_to_many"}
TO_MANY = {"one_to_many", "many_to_many"}
JOIN_TYPES = {"left_outer", "full_outer", "inner", "cross"}
MEASURE_TYPES = {"average", "average_distinct", "count", "count_distinct", "date", "list", "max", "median",
                 "median_distinct", "min", "number", "percent_of_previous", "percent_of_total", "percentile",
                 "percentile_distinct", "period_over_period", "running_total", "string", "sum", "sum_distinct",
                 "yesno"}
# Plus the single-timeframe date_* and duration_* types.
DIMENSION_TYPES = {"bin", "custom_calendar", "date", "date_time", "distance", "duration", "location", "number",
                   "string", "tier", "time", "unquoted", "yesno", "zipcode"}
VALUE_FORMAT_NAMES = {f"decimal_{i}" for i in range(5)} | {f"percent_{i}" for i in range(5)} | {
    "id", "usd", "usd_0", "gbp", "gbp_0", "eur", "eur_0"}
# Measure types that aggregate a dimension themselves, so can't take another measure as input.
AGGREGATE_TYPES = {"sum", "average", "median", "percentile", "min", "max", "count_distinct", "list",
                   "sum_distinct", "average_distinct", "median_distinct", "percentile_distinct"}
# No symmetric aggregate exists for these: over a fanout they silently count repeated rows.
NON_SYMMETRIC = {"median", "percentile", "list"}
# Measure types that do no aggregation of their own, so should only combine measures.
COMBINING_TYPES = {"number", "string", "date", "yesno"}
SQL_AGGREGATES = {"sum", "count", "avg", "min", "max", "any_value", "array_agg", "string_agg",
                  "countif", "logical_and", "logical_or", "approx_count_distinct", "approx_quantiles",
                  "approx_top_count", "stddev", "stddev_samp", "stddev_pop", "variance", "var_samp",
                  "var_pop", "corr", "covar_pop", "covar_samp", "bit_and", "bit_or", "bit_xor", "array_concat_agg",
                  "max_by", "min_by", "approx_top_sum", "grouping", "merge", "merge_partial", "init",
                  "st_union_agg", "st_centroid_agg", "st_extent"}
# Raw SQL aggregates in a type: number measure bypass symmetric aggregates; these add up repeated rows.
RAW_FANOUT_AGGREGATE = re.compile(
    r"\b(?:sum|avg|countif|array_agg|string_agg|array_concat_agg|approx_quantiles|approx_top_count|approx_top_sum"
    r"|stddev(?:_samp|_pop)?|var_samp|var_pop|variance|corr|covar_pop|covar_samp)\s*\(|\bcount\s*\((?!\s*distinct\b)",
    re.I)
# A sql/html block missing its ';;' runs on to the next one and swallows the LookML in between.
SWALLOWED = re.compile(r"^\s*(?:(?:dimension|dimension_group|measure|filter|parameter|set|join|explore|view)\s*:"
                       r"\s*\+?\w+\s*\{|(?:description|label|type|primary_key|hidden|value_format_name)\s*:)", re.M)
LIQUID_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
LIQUID_VAR = re.compile(r"\b(\w+\.\w+)\._(?:value|rendered_value|filterable_value|linked_value|link|model|view|name"
                        r"|sql|in_query|is_selected|is_filtered|parameter_value)\b")
TEXT_TILES = {"text", "button"}
UNDOCUMENTED_OK = {"description", "label", "group_label", "group_item_label", "view_label"}

REF = re.compile(r"\$\{([^}]*)\}")
TABLE_COLUMN = re.compile(r"\$\{TABLE\}\.(\w+)")
# `project.dataset.table` -> (dataset, table)
TABLE_NAME = re.compile(r"`?(?:[\w-]+\.)?(\w+)\.(\w+)`?\Z")
CONSTANT = re.compile(r"@\{(\w+)\}")
LIQUID_FIELD = re.compile(r"\{%-?\s*(?:condition|parameter|date_start|date_end)\s+([\w.]+)\s*-?%\}")
USER_ATTRIBUTE = re.compile(r"_user_attributes\[\s*['\"](\w+)['\"]\s*\]")
SQL_TOKEN = re.compile(r"\$\{[^}]*\}|'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|--[^\n]*|/\*.*?\*/|\w+\s*\(|[()]",
                       re.S)
SORT = re.compile(r"^\s*(\S+?)(?:\s+(?:asc|desc))?(?:\s+\d+)?\s*$", re.I)
# Strings, SQL/HTML blocks (up to ';;') and comments, so that only structural braces are counted.
LKML_SCAN = re.compile(r'"(?:[^"\\]|\\.)*"|\b(?:sql\w*|html|expression\w*)\s*:.*?;;|#[^\n]*|[{}]', re.S)
# Blocks whose children must be named objects: `measure: x { ... }`.
NAMED_BLOCKS = {"views": ("dimensions", "dimension_groups", "measures", "filters", "parameters", "sets"),
                "explores": ("joins",)}
# Parameters that take a [...] list.
LIST_PARAMS = {"drill_fields", "required_fields", "fields", "timeframes", "intervals", "suggestions",
               "required_access_grants", "allowed_values", "always_join", "required_joins", "unless"}


# ---------------------------------------------------------------- report

@dataclass(frozen=True)
class Problem:
    level: str          # "error" or "warning"
    check: str
    file: str
    where: str
    message: str


class Report:
    def __init__(self) -> None:
        self.problems: dict[Problem, None] = {}            # ordered set: the same problem prints once
        self.checked: dict[str, int] = defaultdict(int)
        self.user_attributes: dict[str, set[str]] = defaultdict(set)

    def error(self, check: str, file: str, where: str, message: str) -> None:
        self.problems[Problem("error", check, file, where, message)] = None

    def warn(self, check: str, file: str, where: str, message: str) -> None:
        self.problems[Problem("warning", check, file, where, message)] = None

    def count(self, level: str, check: str | None = None) -> int:
        return sum(p.level == level and check in (None, p.check) for p in self.problems)


# ---------------------------------------------------------------- objects

@dataclass
class Field:
    name: str
    kind: str                   # dimension, measure, filter or parameter
    node: dict
    group: str | None = None    # set for the fields a dimension_group expands into

    @property
    def label(self) -> str:
        return f"dimension_group {self.group}" if self.group else f"{self.kind} {self.name}"


@dataclass
class Deps:
    same: set[str] = field(default_factory=set)                 # fields of the same view
    cross: set[tuple[str, str]] = field(default_factory=set)    # (view or join alias, field)


@dataclass
class View:
    name: str
    file: str
    node: dict
    fields: dict[str, Field] = field(default_factory=dict)
    groups: dict[str, dict] = field(default_factory=dict)
    sets: dict[str, list[str]] = field(default_factory=dict)
    deps: dict[str, Deps] = field(default_factory=dict)


@dataclass
class Explore:
    name: str
    label: str
    file: str
    node: dict
    base: str                                   # alias of the base view
    aliases: dict[str, str]                     # alias -> view name, base included
    joins: dict[str, dict]
    include: set[tuple[str, str]] | None = None  # the explore's fields: parameter, None = everything
    exclude: set[tuple[str, str]] = field(default_factory=set)
    join_fields: dict[str, set[str]] = field(default_factory=dict)


@dataclass
class Model:
    name: str
    file: str
    files: set[str]
    views: set[str]
    explores: dict[str, Explore] = field(default_factory=dict)


# ---------------------------------------------------------------- helpers

def strings(obj, skip: set[str] = frozenset()):
    """Every string inside a parsed LookML or YAML value."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if key not in skip:
                yield from strings(value, skip)
    elif isinstance(obj, list):
        for value in obj:
            yield from strings(value, skip)


def as_list(value) -> list:
    """A YAML value that should be a list: `fields: x.y` comes back as a plain string."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def flatten(value) -> list[str]:
    """extends__all and filters__all come back as lists of lists."""
    if isinstance(value, list):
        return [x for v in value for x in flatten(v)]
    return [value]


def filter_fields(node: dict) -> list[str]:
    """Field names used as filter keys, from either `filters: [a: "x"]` or `filters: {field: a ...}`."""
    names = []
    for item in flatten(node.get("filters__all", [])) + flatten(node.get("filters", [])):
        if isinstance(item, dict):
            names += [item["field"]] if "field" in item else list(item)
    return names


def split_ref(ref: str) -> list[str]:
    """`view.field._value` -> [view, field]: liquid-style suffixes don't change what is referenced."""
    parts = ref.strip().split(".")
    while len(parts) > 1 and parts[-1].startswith("_"):
        parts.pop()
    return parts


def group_fields(group: dict) -> list[str]:
    if group.get("type", "time") == "duration":
        return [f"{i}s_{group['name']}" for i in group.get("intervals", INTERVALS)]
    return [f"{group['name']}_{t}" for t in group.get("timeframes", TIMEFRAMES)]


def is_named_list(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(i, dict) and "name" in i for i in value)


def merge_nodes(base: dict, over: dict) -> dict:
    """Overlay one LookML object on another as extends and refinements do: named children (fields,
    joins, sets) merge by name, every other parameter is replaced."""
    out = dict(base)
    for key, value in over.items():
        if is_named_list(value) and is_named_list(out.get(key)):
            items = {i["name"]: i for i in out[key]}
            for item in value:
                items[item["name"]] = merge_nodes(items[item["name"]], item) if item["name"] in items else item
            out[key] = list(items.values())
        else:
            out[key] = value
    return out


def missing_field(view: View, name: str) -> str:
    if name in view.groups:
        example = group_fields(view.groups[name])[:1]
        return (f"{name} is a dimension_group in view {view.name}; reference one of its fields"
                + (f", e.g. {example[0]}" if example else ""))
    return f"view {view.name} has no field {name}"


def refs_outside_aggregates(sql: str) -> list[str]:
    """The ${...} references in `sql` that aren't inside an aggregate function call."""
    stack, outside = [], []
    for match in SQL_TOKEN.finditer(sql):
        token = match.group()
        if token.startswith("${"):
            if not any(stack):
                outside.append(token[2:-1])
        elif token.endswith("("):
            stack.append(token[:-1].strip().lower() in SQL_AGGREGATES)
        elif token == ")" and stack:
            stack.pop()
    return outside


# ---------------------------------------------------------------- parse and constants

def stray_brace(text: str) -> str | None:
    """lkml ignores a '}' with nothing to close, which Looker rejects."""
    depth = 0
    for m in LKML_SCAN.finditer(text):
        depth += {"{": 1, "}": -1}.get(m.group(), 0)
        if depth < 0:
            return f"unmatched '}}' on line {text.count(chr(10), 0, m.start()) + 1}"
    return None


def malformed(tree: dict) -> str | None:
    """lkml also accepts `measure: x` with no block, and returns a bare string for it."""
    for key, children in NAMED_BLOCKS.items():
        nodes = tree.get(key, [])
        for node in nodes if isinstance(nodes, list) else [nodes]:
            if not isinstance(node, dict) or not node.get("name"):
                return f"{key[:-1]} {node if isinstance(node, str) else ''} has no name or no {{ }} block"
            for child in children:
                items = node.get(child, [])
                for item in items if isinstance(items, list) else [items]:
                    if not isinstance(item, dict) or not item.get("name"):
                        name = item if isinstance(item, str) else ""
                        return f"{key[:-1]} {node['name']} > {child[:-1]} {name} has no name or no {{ }} block"
    return bare_list(tree)


def bare_list(obj, key: str = "", where: str = "") -> str | None:
    """lkml reads `drill_fields: x` (no brackets) as a string; Looker wants a list."""
    if isinstance(obj, list):
        for item in obj:
            if problem := bare_list(item, key, where):
                return problem
    elif isinstance(obj, dict):
        if isinstance(obj.get("name"), str):
            here = f"{key.removesuffix('s')} {obj['name']}"
            where = f"{where} > {here}" if where else here
        for k, value in obj.items():
            if k in LIST_PARAMS and not isinstance(value, list):
                return f"{where + ': ' if where else ''}{k} must be a [...] list"
            if problem := bare_list(value, k, where):
                return problem
    return None


def malformed_dashboard(doc) -> str | None:
    if not isinstance(doc, list) or not all(isinstance(d, dict) and "dashboard" in d for d in doc):
        return "not a LookML dashboard (expected a list of '- dashboard:' items)"
    for dash in doc:
        for key in ("elements", "filters"):
            items = dash.get(key) or []
            if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
                return f"dashboard {dash['dashboard']}: {key} must be a list of mappings"
    return None


def swallowed(tree) -> str | None:
    def walk(obj, key=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield from walk(v, k)
        elif isinstance(obj, list):
            for v in obj:
                yield from walk(v, key)
        elif isinstance(obj, str) and (key.startswith("sql") or key in ("html", "expression")):
            yield key, obj
    for key, text in walk(tree):
        if m := SWALLOWED.search(text):
            return f"a {key}: block runs on into '{m.group().strip()}' (missing ';;'?)"
    return None


def parse(root: Path, report: Report) -> tuple[dict[str, dict], dict[str, list]]:
    files = sorted(p for p in root.rglob("*") if p.is_file()
                   and not any(part.startswith(".") for part in p.relative_to(root).parts))
    trees, dashboards = {}, {}
    for path in files:
        rel = path.relative_to(root).as_posix()
        if path.suffix == ".lkml":
            report.checked["parse"] += 1
            try:
                text = path.read_text(encoding="utf-8")
                tree = lkml.load(text) or {}
            except Exception as e:      # lkml raises SyntaxError, and the odd KeyError on bad input
                report.error("parse", rel, "", f"does not parse: {e}")
                continue
            if problem := stray_brace(text) or malformed(tree) or swallowed(tree):
                report.error("parse", rel, "", f"does not parse: {problem}")
            else:
                trees[rel] = tree
        elif path.name.endswith(".dashboard.lookml"):
            report.checked["parse"] += 1
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (yaml.YAMLError, UnicodeDecodeError) as e:
                report.error("parse", rel, "", f"does not parse: {' '.join(str(e).split())}")
                continue
            if problem := malformed_dashboard(doc):
                report.error("parse", rel, "", problem)
            else:
                dashboards[rel] = doc
    return trees, dashboards


def resolve_constants(tree, constants: dict[str, str], file: str, report: Report):
    """Substitute @{name} everywhere in a parsed file, reporting the names the manifest lacks."""
    def label(obj: dict, key: str | None, where: str) -> str:
        if "dashboard" in obj and key is None:
            return f"dashboard {obj['dashboard']}"
        if "name" in obj and key:
            kind = key.removesuffix("s")
            return f"{where} > {kind} {obj['name']}" if where else f"{kind} {obj['name']}"
        return where

    def sub(text: str, where: str) -> str:
        def repl(m: re.Match) -> str:
            report.checked["constants"] += 1
            if m.group(1) in constants:
                return constants[m.group(1)]
            report.error("constants", file, where, f"@{{{m.group(1)}}} is not defined in manifest.lkml")
            return m.group(0)
        return CONSTANT.sub(repl, text)

    def walk(obj, key: str | None, where: str):
        if isinstance(obj, str):
            return sub(obj, where)
        if isinstance(obj, list):
            return [walk(x, key, where) for x in obj]
        if isinstance(obj, dict):
            here = label(obj, key, where)
            return {k: walk(v, k, here) for k, v in obj.items()}
        return obj

    return walk(tree, None, "")


# ---------------------------------------------------------------- views

def collect_views(trees: dict[str, dict], report: Report) -> dict[str, View]:
    nodes, files, refinements = {}, {}, []
    for file, tree in trees.items():
        for node in tree.get("views", []):
            name = node["name"]
            if name.startswith("+"):
                refinements.append((file, node))
            elif name in nodes:
                report.error("views", file, f"view {name}", f"view name already used in {files[name]}")
            else:
                nodes[name], files[name] = node, file
    for file, node in refinements:
        name = node["name"][1:]
        if name not in nodes:
            report.error("views", file, f"view {node['name']}", f"refines view {name}, which doesn't exist")
            continue
        nodes[name] = {**merge_nodes(nodes[name], node), "name": name}

    resolved: dict[str, dict] = {}

    def effective(name: str, chain: tuple[str, ...]) -> dict:
        if name not in resolved:
            node, base = nodes[name], {}
            for parent in flatten(node.get("extends__all", [])):
                if parent not in nodes or parent in chain:
                    report.error("views", files[name], f"view {name}", f"extends {parent}, which "
                                 + ("extends it back" if parent in chain else "doesn't exist"))
                    continue
                inherited = {k: v for k, v in effective(parent, chain + (name,)).items()
                             if k not in ("extension", "extends__all", "name")}
                base = merge_nodes(base, inherited)
            resolved[name] = merge_nodes(base, node) if base else node
        return resolved[name]

    return {name: build_view(name, files[name], effective(name, ()), report) for name in nodes}


def build_view(name: str, file: str, node: dict, report: Report) -> View:
    view = View(name, file, node)
    entries = [Field(f["name"], kind, f) for kind, key in (("dimension", "dimensions"), ("measure", "measures"),
                                                           ("filter", "filters"), ("parameter", "parameters"))
               for f in node.get(key, [])]
    for group in node.get("dimension_groups", []):
        view.groups[group["name"]] = group
        entries += [Field(f, "dimension", group, group["name"]) for f in group_fields(group)]
    for f in entries:
        if f.name in view.fields:
            report.error("views", file, f"view {name}",
                         f"field name {f.name} is used twice ({view.fields[f.name].label} and {f.label})")
        else:
            view.fields[f.name] = f
    view.sets = {s["name"]: s.get("fields", []) for s in node.get("sets", [])}
    return view


def check_view(view: View, included: bool, report: Report, named_formats: set[str] = frozenset()) -> None:
    report.checked["views"] += 1
    where = f"view {view.name}"
    if not included:
        report.warn("views", view.file, where, "no model includes this file, so Looker won't load it")
    for kind, node in field_nodes(view):
        fwhere = f"{where} > {kind} {node['name']}"
        ftype = node.get("type")
        if kind == "measure" and ftype and ftype not in MEASURE_TYPES:
            report.error("views", view.file, fwhere, f"type: {ftype} is not a measure type")
        elif (kind == "dimension" and ftype and ftype not in DIMENSION_TYPES
              and not ftype.startswith(("date_", "duration_"))):
            report.error("views", view.file, fwhere, f"type: {ftype} is not a dimension type")
        elif kind == "dimension_group":
            if ftype not in (None, "time", "duration"):
                report.error("views", view.file, fwhere, f"type: {ftype} is not time or duration")
            for tf in node.get("timeframes", []):
                if tf not in TIME_TIMEFRAMES and not TIMEFRAME_STEP.match(tf):
                    report.error("views", view.file, fwhere, f"timeframes: {tf} is not a timeframe")
            for iv in node.get("intervals", []):
                if iv not in INTERVALS:
                    report.error("views", view.file, fwhere, f"intervals: {iv} is not an interval")
        vfn = node.get("value_format_name")
        if vfn and vfn not in VALUE_FORMAT_NAMES | named_formats:
            report.error("views", view.file, fwhere,
                         f"value_format_name: {vfn} is not built in or a named_value_format")
    if view.node.get("extension") == "required":
        return
    if not view.node.get("sql_table_name") and not view.node.get("derived_table"):
        report.error("views", view.file, where, "no sql_table_name or derived_table")
    keys = [d["name"] for d in view.node.get("dimensions", []) if d.get("primary_key") == "yes"]
    if not keys:
        report.error("views", view.file, where, "no primary key (one dimension needs primary_key: yes)")
    elif len(keys) > 1:
        report.error("views", view.file, where, f"{len(keys)} primary keys ({', '.join(keys)}); a view has exactly one")


def field_nodes(view: View):
    """(kind, node) for each field as written: a dimension_group once, not once per timeframe."""
    for kind, key in (("dimension", "dimensions"), ("dimension_group", "dimension_groups"),
                      ("measure", "measures"), ("filter", "filters"), ("parameter", "parameters")):
        for node in view.node.get(key, []):
            yield kind, node


def resolve_view_ref(view: View, ref: str, views: dict[str, View], aliases: set[str], deps: Deps) -> str | None:
    """Check a ${...} written inside a view; records what it depends on in `deps`."""
    parts = split_ref(ref)
    if parts in (["TABLE"], ["EXTENDED"]):
        return None
    if len(parts) == 1:
        if parts[0] in view.fields:
            deps.same.add(parts[0])
            return None
        return f"${{{ref}}}: {missing_field(view, parts[0])}"
    if len(parts) != 2:
        return f"${{{ref}}} is not a field reference"
    target, name = parts
    if name == "SQL_TABLE_NAME":
        return None if target in views else f"${{{ref}}}: no view named {target}"
    if target == view.name:
        return resolve_view_ref(view, name, views, aliases, deps)
    if target in views:
        if name not in views[target].fields:
            return f"${{{ref}}}: {missing_field(views[target], name)}"
        deps.cross.add((target, name))
        return None
    if target in aliases:               # a join alias: resolved per explore later
        deps.cross.add((target, name))
        return None
    return f"${{{ref}}}: no view or join named {target}"


def resolve_view_name(view: View, name: str, views: dict[str, View], aliases: set[str]) -> str | None:
    """Check a plain field name (drill_fields, sets, measure filters): field, set*, or view.field."""
    is_set, name = name.endswith("*"), name.rstrip("*")
    target, _, fname = name.rpartition(".")
    owner = views.get(target or view.name)
    if owner is None:
        return None if target in aliases else f"{name}: no view or join named {target}"
    if is_set:
        found = fname in owner.sets or fname == "ALL_FIELDS"
        return None if found else f"{name}*: view {owner.name} has no set {fname}"
    return None if fname in owner.fields or fname in owner.groups else f"{name}: {missing_field(owner, fname)}"


def check_view_references(view: View, views: dict[str, View], aliases: set[str], report: Report) -> None:
    for kind, node in field_nodes(view):
        where = f"view {view.name} > {kind} {node['name']}"
        deps = Deps()
        for text in strings(node, UNDOCUMENTED_OK):
            for ref in REF.findall(text):
                report.checked["references"] += 1
                if problem := resolve_view_ref(view, ref, views, aliases, deps):
                    report.error("references", view.file, where, problem)
            for name in LIQUID_FIELD.findall(text) + [v for tag in LIQUID_TAG.findall(text)
                                                       for v in LIQUID_VAR.findall(tag)]:
                report.checked["references"] += 1
                if problem := resolve_view_name(view, name, views, aliases):
                    report.error("references", view.file, where, f"liquid {problem}")
        names = (node.get("drill_fields", []) + node.get("required_fields", []) + filter_fields(node)
                 + ([node["order_by_field"]] if node.get("order_by_field") else []))
        for name in names:
            report.checked["references"] += 1
            if problem := resolve_view_name(view, name, views, aliases):
                report.error("references", view.file, where, problem)
        # A measure's filters are dependencies too: a fields: parameter can't leave them out.
        for name in filter_fields(node):
            owner, _, short = name.rpartition(".")
            if owner in ("", view.name):
                if short in view.fields:
                    deps.same.add(short)
            elif (owner in views and short in views[owner].fields) or (owner not in views and owner in aliases):
                deps.cross.add((owner, short))
        for fname in group_fields(node) if kind == "dimension_group" else [node["name"]]:
            view.deps[fname] = deps
        if kind in ("dimension", "dimension_group"):
            for ref in deps.same:
                if view.fields[ref].kind == "measure":
                    report.error("references", view.file, where, f"a dimension can't reference measure {ref}")

    for set_name, names in view.sets.items():
        for name in names:
            report.checked["references"] += 1
            if problem := resolve_view_name(view, name, views, aliases):
                report.error("references", view.file, f"view {view.name} > set {set_name}", problem)
    for name in view.node.get("drill_fields", []):
        report.checked["references"] += 1
        if problem := resolve_view_name(view, name, views, aliases):
            report.error("references", view.file, f"view {view.name}", f"drill_fields: {problem}")
    for ref in REF.findall(" ".join(strings(view.node.get("derived_table", {})))):
        report.checked["references"] += 1
        parts = split_ref(ref)
        if len(parts) != 2 or parts[1] != "SQL_TABLE_NAME":
            report.error("references", view.file, f"view {view.name} > derived_table",
                         f"${{{ref}}}: derived table SQL can only reference ${{view.SQL_TABLE_NAME}}")
        elif parts[0] not in views:
            report.error("references", view.file, f"view {view.name} > derived_table",
                         f"${{{ref}}}: no view named {parts[0]}")


def cross_refs(view: View, name: str, seen: frozenset = frozenset()) -> set[tuple[str, str]]:
    """Other-view references of a field, including those reached through same-view fields."""
    deps = view.deps.get(name)
    if deps is None or name in seen:
        return set()
    out = set(deps.cross)
    for other in deps.same:
        out |= cross_refs(view, other, seen | {name})
    return out


def mart_columns(folder: Path) -> dict[str, set[str]]:
    """Mart model -> documented columns, from the dbt schema files (check_doc_coverage keeps them complete)."""
    out = {}
    for path in sorted(folder.glob("*.yml")) if folder.is_dir() else []:
        for model in (yaml.safe_load(path.read_text()) or {}).get("models", []):
            out[model["name"]] = {c["name"].lower() for c in model.get("columns", [])}
    return out


def check_columns(view: View, marts: dict[str, set[str]], report: Report) -> None:
    """sql_table_name names a mart, and every ${TABLE}.column is one of its columns. Looker's own
    validator doesn't look at the database, so without this a typo only shows up when queried."""
    m = TABLE_NAME.match(str(view.node.get("sql_table_name", "")).strip())
    if not marts or not m:
        return
    table, where = m.group(2), f"view {view.name}"
    if table not in marts:
        report.error("views", view.file, where, f"sql_table_name: {table} is not a model in {MARTS.name}/")
        return
    for kind, node in field_nodes(view):
        for text in strings(node, UNDOCUMENTED_OK):
            for column in TABLE_COLUMN.findall(text):
                report.checked["references"] += 1
                if column.lower() not in marts[table]:
                    report.error("views", view.file, f"{where} > {kind} {node['name']}",
                                 f"${{TABLE}}.{column}: {table} has no column {column}")


# ---------------------------------------------------------------- descriptions and measure rules

def lookup(view: View, ref: str, views: dict[str, View]) -> Field | None:
    parts = split_ref(ref)
    if len(parts) == 1:
        return view.fields.get(parts[0])
    owner = views.get(parts[0]) if len(parts) == 2 else None
    return owner.fields.get(parts[1]) if owner else None


def check_descriptions(view: View, report: Report) -> None:
    for kind, node in field_nodes(view):
        if kind in ("filter", "parameter"):
            continue
        report.checked["descriptions"] += 1
        if not str(node.get("description", "")).strip():
            where = f"view {view.name} > {kind} {node['name']}"
            if kind == "measure":
                report.error("descriptions", view.file, where, "measure has no description")
            else:
                report.warn("descriptions", view.file, where, f"{kind} has no description")


def check_measures(view: View, views: dict[str, View], report: Report) -> None:
    for node in view.node.get("measures", []):
        report.checked["measures"] += 1
        mtype, sql = node.get("type", "count"), node.get("sql", "")
        where = f"view {view.name} > measure {node['name']}"
        if mtype in ("percentile", "percentile_distinct") and "percentile" not in node:
            report.error("measures", view.file, where, f"type: {mtype} needs a percentile: value")
        if mtype in COMBINING_TYPES:
            for ref in refs_outside_aggregates(sql):
                target = lookup(view, ref, views)
                if split_ref(ref) == ["TABLE"]:
                    report.error("measures", view.file, where,
                                 f"type: {mtype} measure uses a raw column (${{TABLE}}) outside an aggregate; "
                                 "reference measures, or aggregate the column")
                elif target and target.kind == "dimension":
                    report.warn("measures", view.file, where,
                                f"type: {mtype} measure uses dimension {ref} outside an aggregate; "
                                "it should only combine measures")
        if mtype in AGGREGATE_TYPES:
            for ref in REF.findall(sql):
                target = lookup(view, ref, views)
                if target and target.kind == "measure":
                    report.error("measures", view.file, where,
                                 f"type: {mtype} can't aggregate measure {ref}; use type: number to combine measures")


# ---------------------------------------------------------------- model, includes, explores

def include_regex(pattern: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        else:
            out, i = out + ("[^/]*" if pattern[i] == "*" else re.escape(pattern[i])), i + 1
    return re.compile(out + r"\Z")


def match_include(pattern: str, model_file: str, files: list[str]) -> set[str]:
    """Files an include matches: '/x' is relative to the project root, 'x' to the model's folder."""
    folder = str(Path(model_file).parent.as_posix())
    path = pattern[1:] if pattern.startswith("/") else posixpath.normpath(f"{folder}/{pattern}")
    candidates = [path] if path.endswith((".lkml", ".lookml")) else [path, path + ".lkml", path + ".lookml"]
    for candidate in candidates:            # include: "*.view" is the older spelling of "*.view.lkml"
        regex = include_regex(candidate)
        if matched := {f for f in files if regex.match(f)}:
            return matched
    return set()


def collect_models(root: Path, trees: dict[str, dict], views: dict[str, View], report: Report) -> dict[str, Model]:
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    models = {}
    for file, tree in trees.items():
        if not file.endswith(".model.lkml"):
            continue
        name = Path(file).name.removesuffix(".model.lkml")
        included = {file}
        for pattern in tree.get("includes", []):
            report.checked["model"] += 1
            if pattern.startswith("//"):
                report.warn("model", file, f"model {name}", f'include "{pattern}" is another project; not checked')
            elif matched := match_include(pattern, file, files):
                included |= matched
            else:
                report.error("model", file, f"model {name}", f'include "{pattern}" matches no file')
        if not tree.get("connection"):
            report.error("model", file, f"model {name}", "no connection")
        models[name] = Model(name, file, included, {v.name for v in views.values() if v.file in included})
    if not models:
        report.error("model", "", "", "no *.model.lkml file")
    labelled = len(models) > 1
    for model in models.values():
        nodes = explore_nodes(model, trees, report)
        for node in nodes.values():
            if node.get("extension") != "required":
                label = f"{model.name}.{node['name']}" if labelled else node["name"]
                model.explores[node["name"]] = build_explore(node, label, model, views, report)
    return models


def explore_nodes(model: Model, trees: dict[str, dict], report: Report) -> dict[str, dict]:
    """The model's explores with refinements and extends applied."""
    nodes, files, refinements = {}, {}, []
    for file in sorted(model.files & trees.keys()):
        for node in trees[file].get("explores", []):
            if node["name"].startswith("+"):
                refinements.append((file, node))
            elif node["name"] in nodes:
                report.error("explores", file, f"explore {node['name']}",
                             f"explore name already used in {files[node['name']]}")
            else:
                nodes[node["name"]], files[node["name"]] = {**node, "_file": file}, file
    for file, node in refinements:
        name = node["name"][1:]
        if name in nodes:
            nodes[name] = {**merge_nodes(nodes[name], node), "name": name}
        else:
            report.error("explores", file, f"explore {node['name']}", f"refines explore {name}, which doesn't exist")
    for name, node in nodes.items():
        for parent in flatten(node.get("extends__all", [])):
            if parent in nodes:
                inherited = {k: v for k, v in nodes[parent].items()
                             if k not in ("extension", "extends__all", "name", "_file")}
                nodes[name] = merge_nodes(inherited, nodes[name])
            else:
                report.error("explores", files[name], f"explore {name}", f"extends {parent}, which doesn't exist")
    return nodes


def check_grants_and_datagroups(model: Model, trees: dict[str, dict], views: dict[str, View], report: Report) -> None:
    """access_grant user attributes, and every required_access_grants / persist_with names one the model has."""
    files = sorted(model.files & trees.keys())
    grants = {g["name"]: g for f in files for g in trees[f].get("access_grants", [])}
    datagroups = {d["name"] for f in files for d in trees[f].get("datagroups", [])}
    for name, grant in grants.items():
        if grant.get("user_attribute"):
            report.user_attributes[grant["user_attribute"]].add(f"access_grant {name} in model {model.name}")
        else:
            report.error("model", model.file, f"access_grant {name}", "no user_attribute")

    def check(node: dict, file: str, where: str) -> None:
        for grant in node.get("required_access_grants", []):
            if grant not in grants:
                report.error("model", file, where,
                             f"required_access_grants: model {model.name} has no access_grant {grant}")
        if node.get("persist_with") and node["persist_with"] not in datagroups:
            report.error("model", file, where,
                         f"persist_with: model {model.name} has no datagroup {node['persist_with']}")

    check(trees[model.file], model.file, f"model {model.name}")
    for explore in model.explores.values():
        check(explore.node, explore.file, f"explore {explore.name}")
        for alias, join in explore.joins.items():
            check(join, explore.file, f"explore {explore.name} > join {alias}")
    for view in (views[v] for v in sorted(model.views)):
        check(view.node, view.file, f"view {view.name}")
        for kind, node in field_nodes(view):
            check(node, view.file, f"view {view.name} > {kind} {node['name']}")


def check_view_in_model(name: str, model: Model, views: dict[str, View]) -> str | None:
    if name not in views:
        return f"no view named {name}"
    if name not in model.views:
        return f"view {name} is not included by model {model.name}"
    return None


def build_explore(node: dict, label: str, model: Model, views: dict[str, View], report: Report) -> Explore:
    file, name = node["_file"], node["name"]
    report.checked["explores"] += 1
    where = f"explore {name}"
    base_view = node.get("from") or node.get("view_name") or name
    base = node.get("view_name") or name
    if problem := check_view_in_model(base_view, model, views):
        report.error("explores", file, where, f"base view: {problem}")
    explore = Explore(name, label, file, node, base, {base: base_view}, {})
    for join in node.get("joins", []):
        alias, jwhere = join["name"], f"{where} > join {join['name']}"
        if alias in explore.aliases:
            report.error("explores", file, jwhere, f"{alias} is already used in this explore")
            continue
        view = join.get("from") or alias
        if problem := check_view_in_model(view, model, views):
            report.error("explores", file, jwhere, problem)
        explore.aliases[alias], explore.joins[alias] = view, join
        relationship = join.get("relationship")
        if relationship is None:
            report.error("explores", file, jwhere, "no relationship (Looker would assume many_to_one)")
        elif relationship not in RELATIONSHIPS:
            report.error("explores", file, jwhere, f"relationship {relationship} is not one of {sorted(RELATIONSHIPS)}")
        if join.get("type") and join["type"] not in JOIN_TYPES:
            report.error("explores", file, jwhere, f"type: {join['type']} is not one of {sorted(JOIN_TYPES)}")
        if sql_on := join.get("sql_on"):
            # A constant condition (1 = 1) or a raw alias (dim_org.x) is legal, so only ${} refs count.
            named = {parts[0] for r in REF.findall(sql_on) if len(parts := split_ref(r)) == 2}
            raw = re.search(rf"(?<![\w$]){re.escape(alias)}\.\w", REF.sub("", sql_on))
            if named and alias not in named and not raw:
                report.error("explores", file, jwhere, f"sql_on never references {alias}, so every {alias} row matches")
            elif named and not named - {alias}:
                report.warn("explores", file, jwhere, f"sql_on references only {alias}, not the view it joins to")
        if not any(join.get(k) for k in ("sql_on", "foreign_key", "sql")) and join.get("type") != "cross":
            report.error("explores", file, jwhere, "no sql_on or foreign_key")
    return explore


def resolve_in_scope(explore: Explore, alias: str, name: str, views: dict[str, View]) -> str | None:
    if alias not in explore.aliases:
        joined = "is a view that isn't joined" if alias in views else "is not a view or join"
        return f"{alias}.{name}: {alias} {joined} in explore {explore.label}"
    view = views.get(explore.aliases[alias])
    if view is None or name == "SQL_TABLE_NAME" or name in view.fields:
        return None                     # a missing view is reported once, at the join
    return f"{alias}.{name}: {missing_field(view, name)}"


def resolve_explore_ref(explore: Explore, ref: str, views: dict[str, View]) -> str | None:
    parts = split_ref(ref)
    if len(parts) != 2:
        return f"${{{ref}}}: explore SQL must reference ${{view.field}}"
    problem = resolve_in_scope(explore, *parts, views)
    return f"${{{ref}}}: {problem.split(': ', 1)[1]}" if problem else None


def expand_set(view: View, set_name: str, seen: frozenset = frozenset()) -> set[str]:
    out = set()
    for item in view.sets.get(set_name, []):
        owner, _, name = item.rpartition(".")
        if owner and owner != view.name:
            continue
        if name.endswith("*"):
            if name[:-1] not in seen:
                out |= expand_set(view, name[:-1], seen | {set_name})
        elif name in view.groups:
            out |= set(group_fields(view.groups[name]))
        else:
            out.add(name)
    return out


def expand_field_list(explore: Explore, items: list[str], default_alias: str | None,
                      views: dict[str, View]) -> tuple[set, set, list[str]]:
    """A fields: parameter as (included, excluded) sets of (alias, field), plus any bad entries."""
    include, exclude, problems = set(), set(), []
    for raw in items:
        target = exclude if raw.startswith("-") else include
        item = raw.lstrip("-").strip()
        if item == "ALL_FIELDS*":
            for alias in [default_alias] if default_alias else list(explore.aliases):
                view = views.get(explore.aliases[alias])
                target |= {(alias, f) for f in (view.fields if view else [])}
            continue
        alias, _, name = item.rpartition(".")
        alias = alias or default_alias or explore.base
        if alias not in explore.aliases:
            problems.append(f"{raw}: {alias} is not joined in explore {explore.label}")
            continue
        view = views.get(explore.aliases[alias])
        if view is None:
            continue
        if name.endswith("*"):
            if name[:-1] not in view.sets:
                problems.append(f"{raw}: view {view.name} has no set {name[:-1]}")
            target |= {(alias, f) for f in expand_set(view, name[:-1])}
        elif name in view.groups:
            target |= {(alias, f) for f in group_fields(view.groups[name])}
        elif name in view.fields:
            target.add((alias, name))
        else:
            problems.append(f"{raw}: {missing_field(view, name)}")
    return include, exclude, problems


def exposed(explore: Explore, alias: str, name: str) -> bool:
    """False when the explore's or the join's fields: parameter leaves the field out."""
    if alias in explore.join_fields and name not in explore.join_fields[alias]:
        return False
    if (alias, name) in explore.exclude:
        return False
    return explore.include is None or (alias, name) in explore.include


def check_explore_scope(explore: Explore, views: dict[str, View], report: Report) -> None:
    file, node = explore.file, explore.node
    where = f"explore {explore.name}"

    def refs(text: str, context: str, measures_ok: bool = False) -> None:
        for ref in REF.findall(text or ""):
            report.checked["references"] += 1
            if problem := resolve_explore_ref(explore, ref, views):
                report.error("explores", file, context, problem)
            elif not measures_ok:
                alias, name = split_ref(ref)
                target = views.get(explore.aliases[alias])
                if target and name in target.fields and target.fields[name].kind == "measure":
                    report.error("explores", file, context,
                                 f"${{{ref}}} is a measure; only sql_always_having can use one")

    def names(items: list[str], context: str) -> None:
        for name in items:
            report.checked["references"] += 1
            alias, _, fname = name.rpartition(".")
            if problem := resolve_in_scope(explore, alias or explore.base, fname, views):
                report.error("explores", file, context, problem)

    for key in ("sql_always_where", "sql_always_having"):
        refs(node.get(key), f"{where} > {key}", measures_ok=key == "sql_always_having")
    for access in node.get("access_filters", []):
        if access.get("field"):
            names([access["field"]], f"{where} > access_filter")
        else:
            report.error("explores", file, f"{where} > access_filter", "no field")
        if attribute := access.get("user_attribute"):
            report.user_attributes[attribute].add(f"access_filter on explore {explore.label}")
        else:
            report.error("explores", file, f"{where} > access_filter", "no user_attribute")
    for key in ("always_filter", "conditionally_filter"):
        if spec := node.get(key):
            names(filter_fields(spec) + spec.get("unless", []), f"{where} > {key}")
    for key in ("always_join", "required_joins"):
        for alias in node.get(key, []):
            if alias not in explore.joins:
                report.error("explores", file, f"{where} > {key}", f"{alias} is not a join in this explore")
    if "fields" in node:
        include, exclude, problems = expand_field_list(explore, node["fields"], None, views)
        explore.include = include if any(not f.startswith("-") for f in node["fields"]) else None
        explore.exclude = exclude
        for problem in problems:
            report.error("explores", file, f"{where} > fields", problem)

    for alias, join in explore.joins.items():
        jwhere = f"{where} > join {alias}"
        for key in ("sql_on", "sql_where", "sql"):
            refs(join.get(key), jwhere)
        if fk := join.get("foreign_key"):
            names([fk], f"{jwhere} > foreign_key")
        for other in join.get("required_joins", []):
            if other not in explore.aliases:
                report.error("explores", file, jwhere, f"required_joins: {other} is not a join in this explore")
        if "fields" in join:
            include, exclude, problems = expand_field_list(explore, join["fields"], alias, views)
            explore.join_fields[alias] = {f for a, f in include - exclude if a == alias}
            for problem in problems:
                report.error("explores", file, f"{jwhere} > fields", problem)


def same_view_deps(view: View, name: str, seen: frozenset = frozenset()) -> set[str]:
    """Same-view fields a field uses in its SQL or filters, directly or through other fields."""
    deps = view.deps.get(name)
    if deps is None or name in seen:
        return set()
    out = set(deps.same)
    for other in deps.same:
        out |= same_view_deps(view, other, seen | {name})
    return out


def check_hidden_dependencies(explore: Explore, views: dict[str, View], report: Report) -> None:
    """Looker rejects a field whose sql or filters use a field that a fields: parameter leaves out,
    whether that field is in the same view or in another join."""
    for alias, view_name in explore.aliases.items():
        view = views.get(view_name)
        if view is None:
            continue
        for name in sorted(view.fields):
            if not exposed(explore, alias, name):
                continue
            missing = sorted(d for d in same_view_deps(view, name) if not exposed(explore, alias, d))
            missing += sorted(f"{t}.{f}" for t, f in cross_refs(view, name)
                              if t in explore.aliases and not exposed(explore, t, f))
            if missing:
                report.error("explores", explore.file, f"explore {explore.name} > {alias}.{name}",
                             f"uses {', '.join(missing)}, which a fields: parameter leaves out of this explore")


def check_cross_view_fields(views: dict[str, View], models: dict[str, Model], report: Report) -> None:
    """A field that reads another view only works in explores that join that view."""
    uses = defaultdict(list)
    for model in models.values():
        for explore in model.explores.values():
            for view_name in explore.aliases.values():
                uses[view_name].append(explore)
    for view in views.values():
        for kind, node in field_nodes(view):
            name = (group_fields(node) or [""])[0] if kind == "dimension_group" else node["name"]
            refs = cross_refs(view, name)
            if not refs or not uses[view.name]:
                continue
            ok, bad = [], {}
            for explore in uses[view.name]:
                if not any(exposed(explore, a, name) for a, v in explore.aliases.items() if v == view.name):
                    continue                # the explore's fields: parameter leaves this field out
                problems = [p for t, f in sorted(refs) if (p := resolve_in_scope(explore, t, f, views))]
                if problems:
                    bad[explore.label] = problems[0]
                else:
                    ok.append(explore.label)
            if not bad:
                continue
            where = f"view {view.name} > {kind} {node['name']}"
            refs_text = ", ".join(f"{t}.{f}" for t, f in sorted(refs))
            reasons = "; ".join(bad.values())
            if ok:
                report.warn("references", view.file, where,
                            f"uses {refs_text}: works in explores {', '.join(sorted(set(ok)))} but not in "
                            f"{', '.join(sorted(bad))} ({reasons})")
            else:
                report.error("references", view.file, where,
                             f"uses {refs_text}, which no explore with view {view.name} can resolve ({reasons})")


# ---------------------------------------------------------------- fanout

def join_parent(explore: Explore, alias: str, join: dict) -> str:
    """The view a join hangs off: the first other view its sql_on mentions, else the base view."""
    for ref in REF.findall(f"{join.get('sql_on', '')} {join.get('sql', '')}"):
        parts = split_ref(ref)
        if len(parts) == 2 and parts[0] != alias and parts[0] in explore.aliases:
            return parts[0]
    fk = join.get("foreign_key", "")
    return fk.split(".")[0] if "." in fk and fk.split(".")[0] in explore.aliases else explore.base


def fanout_cause(explore: Explore, start: str) -> str | None:
    """Why rows of `start` repeat in this explore, or None if they don't.

    Walking away from `start`, a join that is to-many in the walking direction multiplies its rows:
    a one_to_many join, or a many_to_one join walked back from the 'one' side."""
    edges = defaultdict(list)
    for alias, join in explore.joins.items():
        relationship = join.get("relationship")
        if relationship in RELATIONSHIPS:
            parent = join_parent(explore, alias, join)
            edges[parent].append((alias, relationship, alias))
            edges[alias].append((parent, REVERSED[relationship], alias))
    seen, queue = {start}, deque([start])
    while queue:
        node = queue.popleft()
        for other, direction, join in edges[node]:
            if other in seen:
                continue
            if direction in TO_MANY:
                via = f" via {node}" if node != start else ""
                return (f"{start} rows repeat once per {other} row{via} "
                        f"(join {join}: relationship {explore.joins[join]['relationship']})")
            seen.add(other)
            queue.append(other)
    return None


def fanout_unsafe(node: dict) -> str | None:
    mtype = node.get("type", "count")
    if mtype in NON_SYMMETRIC:
        return f"a type: {mtype} measure has no symmetric aggregate"
    if mtype in COMBINING_TYPES and (m := RAW_FANOUT_AGGREGATE.search(node.get("sql", ""))):
        return f"its SQL calls {m.group().split('(')[0].strip().lower()}() itself, which has no symmetric aggregate"
    return None


def check_fanout(explore: Explore, views: dict[str, View], report: Report) -> None:
    report.checked["fanout"] += 1
    for alias, view_name in explore.aliases.items():
        view = views.get(view_name)
        measures = [(m, why) for m in (view.node.get("measures", []) if view else [])
                    if exposed(explore, alias, m["name"]) and (why := fanout_unsafe(m))]
        if not measures or not (cause := fanout_cause(explore, alias)):
            continue
        for m, why in measures:
            report.warn("fanout", view.file, f"view {view.name} > measure {m['name']}",
                        f"in explore {explore.label}, {cause}; {why}, so it is computed over the repeated rows")


# ---------------------------------------------------------------- dashboards

def dynamic_field_names(value, report: Report, file: str, where: str) -> set[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            report.error("dashboards", file, where, "dynamic_fields is not valid JSON")
            return set()
    keys = ("table_calculation", "measure", "dimension")
    return {item[k] for item in value or [] if isinstance(item, dict) for k in keys if k in item}


def find_explore(models: dict[str, Model], model: str | None, explore: str | None) -> tuple[Explore | None, str | None]:
    if not model:
        return None, "no model"
    if model not in models:
        return None, f"no model named {model}"
    if not explore:
        return None, "no explore"
    if explore not in models[model].explores:
        return None, f"model {model} has no explore {explore}"
    return models[model].explores[explore], None


def check_query_field(explore: Explore, name: str, dynamic: set[str], views: dict[str, View]) -> str | None:
    if name in dynamic:
        return None
    alias, dot, fname = name.partition(".")
    if not dot:
        return f"{name} is not a view.field name"
    if problem := resolve_in_scope(explore, alias, fname, views):
        return problem
    if not exposed(explore, alias, fname):
        return f"{name}: left out of explore {explore.label} by a fields: parameter"
    return None


def check_query(query: dict, filter_names: set[str], models: dict[str, Model], views: dict[str, View],
                report: Report, file: str, where: str) -> None:
    explore, problem = find_explore(models, query.get("model"), query.get("explore"))
    if problem:
        report.error("dashboards", file, where, problem)
        return
    dynamic = dynamic_field_names(query.get("dynamic_fields"), report, file, where)
    checks = [(key, str(name)) for key in ("fields", "pivots", "fill_fields", "hidden_fields")
              for name in as_list(query.get(key))]
    checks += [("sorts", m.group(1)) for s in as_list(query.get("sorts")) if (m := SORT.match(str(s)))]
    filters = query.get("filters") or {}
    if not isinstance(filters, dict):
        report.error("dashboards", file, where, "filters must be a mapping of field: value")
        filters = {}
    checks += [("filters", str(name)) for name in filters]
    for listen in as_list(query.get("listen")):
        if not isinstance(listen, dict):
            report.error("dashboards", file, where, "listen must map dashboard filter names to fields")
            continue
        for filter_name, name in listen.items():
            if filter_name not in filter_names:
                report.error("dashboards", file, where, f"listen: no dashboard filter named {filter_name}")
            checks.append((f"listen {filter_name}", str(name)))
    # A tile can only pivot, sort or fill on a field its query selects.
    selected = {str(name) for name in as_list(query.get("fields"))} | dynamic
    for key, name in checks:
        if problem := check_query_field(explore, name, dynamic, views):
            report.error("dashboards", file, where, f"{key}: {problem}")
        elif key in ("pivots", "sorts", "fill_fields") and name not in selected:
            report.error("dashboards", file, where, f"{key}: {name} is not one of the tile's fields")


def check_dashboard_filter(flt: dict, models: dict[str, Model], views: dict[str, View], report: Report,
                           file: str, where: str) -> None:
    if not flt.get("field"):
        if flt.get("type") == "field_filter":
            report.error("dashboards", file, where, "field_filter with no field")
        return
    if flt.get("model") or flt.get("explore"):
        explore, problem = find_explore(models, flt.get("model"), flt.get("explore"))
        problem = problem or check_query_field(explore, flt["field"], set(), views)
    else:
        view_name, _, name = flt["field"].partition(".")
        problem = (None if view_name in views and name in views[view_name].fields
                   else f"{flt['field']} is not a view.field that exists")
    if problem:
        report.error("dashboards", file, where, f"field: {problem}")


def check_dashboards(dashboards: dict[str, list], models: dict[str, Model], views: dict[str, View],
                     report: Report) -> int:
    seen, tiles = {}, 0
    for file, docs in dashboards.items():
        included = any(file in m.files for m in models.values())
        for dash in docs:
            name, where = dash["dashboard"], f"dashboard {dash['dashboard']}"
            if name in seen:
                report.error("dashboards", file, where, f"dashboard name already used in {seen[name]}")
            seen[name] = file
            if not included:
                report.warn("dashboards", file, where, "no model includes this file, so Looker won't load it")
            filters = dash.get("filters") or []
            filter_names = {f.get("name") for f in filters}
            if len(filter_names) < len(filters):
                report.error("dashboards", file, where, "two filters share a name")
            for flt in filters:
                fwhere = f"{where} > filter {flt.get('name')}"
                check_dashboard_filter(flt, models, views, report, file, fwhere)
                for other in as_list(flt.get("listens_to_filters")):
                    if other not in filter_names:
                        report.error("dashboards", file, fwhere, f"listens_to_filters: no filter named {other}")
            element_names = set()
            for element in dash.get("elements") or []:
                tiles += 1
                report.checked["dashboards"] += 1
                ename = element.get("name") or element.get("title")
                ewhere = f"{where} > element {ename}"
                if ename in element_names:
                    report.error("dashboards", file, ewhere, "element name already used in this dashboard")
                element_names.add(ename)
                if element.get("type") in TEXT_TILES:
                    continue
                if not element.get("title"):
                    report.error("dashboards", file, ewhere, "no title (only text tiles may go without one)")
                if not element.get("type"):
                    report.error("dashboards", file, ewhere, "no type")
                check_query(element, filter_names, models, views, report, file, ewhere)
                for i, query in enumerate(element.get("merged_queries") or []):
                    check_query(query, filter_names, models, views, report, file, f"{ewhere} > merged query {i + 1}")
    return tiles


# ---------------------------------------------------------------- main

def print_report(report: Report, root: str, views: int, explores: int, tiles: int, strict: bool) -> bool:
    """Print the report; returns whether it failed."""
    print(f"LookML in {root}:")
    for check, noun in CHECKS.items():
        print(f"  {check:<13}{report.checked[check]:>5} {noun:<11} "
              f"{report.count('error', check)} errors, {report.count('warning', check)} warnings")
    if report.user_attributes:
        print("User attributes referenced:")
        for name, uses in sorted(report.user_attributes.items()):
            print(f"  {name}: {'; '.join(sorted(uses))}")
    for level, title in (("error", "Errors"), ("warning", "Warnings")):
        problems = [p for p in report.problems if p.level == level]
        if problems:
            print(f"{title} ({len(problems)}):")
            for p in problems:
                print(f"  [{p.check}] " + ": ".join(x for x in (p.file, p.where, p.message) if x))
    errors, warnings = report.count("error"), report.count("warning")
    failed = bool(errors or (strict and warnings))
    if failed:
        print(f"LookML FAILED: {errors} errors ({warnings} warnings{', --strict' if strict else ''}) in "
              f"{views} views, {explores} explores, {tiles} dashboard tiles")
    else:
        print(f"LookML OK: {views} views, {explores} explores, {tiles} dashboard tiles, "
              f"0 errors ({warnings} warnings)")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", default=str(DEFAULT_ROOT), help="LookML project folder (default: lookml/)")
    ap.add_argument("--strict", action="store_true", help="fail on warnings too")
    ap.add_argument("--marts", default=str(MARTS), help="dbt marts folder for column checks ('' to skip)")
    args = ap.parse_args()
    root = Path(args.path)
    if not root.is_dir():
        print(f"No LookML folder at {root}")
        return 1
    report = Report()

    trees, dashboards = parse(root, report)
    if not trees and not dashboards and not report.count("error"):
        print(f"No .lkml or .dashboard.lookml files under {root}")
        return 1
    constants = {c["name"]: c.get("value", "") for c in trees.get("manifest.lkml", {}).get("constants", [])}
    manifest_broken = any(p.file == "manifest.lkml" and p.check == "parse" for p in report.problems)
    if not manifest_broken:             # otherwise every constant would look undefined
        trees = {f: resolve_constants(t, constants, f, report) for f, t in trees.items()}
        dashboards = {f: resolve_constants(d, constants, f, report) for f, d in dashboards.items()}
    for file, tree in list(trees.items()) + list(dashboards.items()):
        for text in strings(tree):
            for name in USER_ATTRIBUTE.findall(text):
                report.user_attributes[name].add(f"liquid in {file}")

    views = collect_views(trees, report)
    models = collect_models(root, trees, views, report)
    for model in models.values():
        check_grants_and_datagroups(model, trees, views, report)
    explores = [e for m in models.values() for e in m.explores.values()]
    aliases = {a for e in explores for a in e.aliases}
    included = set().union(*(m.views for m in models.values()))
    marts = mart_columns(Path(args.marts)) if args.marts else {}
    named_formats = {f["name"] for t in trees.values() for f in t.get("named_value_formats", [])}
    for view in views.values():
        check_view(view, view.name in included, report, named_formats)
        check_columns(view, marts, report)
        check_view_references(view, views, aliases, report)
        check_descriptions(view, report)
        check_measures(view, views, report)
    for explore in explores:
        check_explore_scope(explore, views, report)
        check_hidden_dependencies(explore, views, report)
        check_fanout(explore, views, report)
    check_cross_view_fields(views, models, report)
    tiles = check_dashboards(dashboards, models, views, report)

    shown = os.path.relpath(root)
    shown = root.as_posix() if shown.startswith("..") else shown
    return 1 if print_report(report, shown, len(views), len(explores), tiles, args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
