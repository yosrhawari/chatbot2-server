import logging

logger = logging.getLogger(__name__)

import ast
import difflib
import json
import re
import time
import textwrap
import threading
import warnings
import builtins
from pathlib import Path
from datetime import date, datetime
from typing import Any

import pandas as pd
import numpy as np
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from config import CACHE_DIR
from models import llm_client


# ── Qwen2.5:1.5b compatibility note ──────────────────────────────────────────
# with_structured_output() relies on tool/function calling which Qwen2.5:1.5b
# does not support reliably. We instead ask the model to return a raw JSON block
# and parse it ourselves, with a safe fallback on parse failure.
# The model also hallucinates column names frequently — we validate every column
# reference in generated code against the actual DataFrame schema BEFORE exec().
# ─────────────────────────────────────────────────────────────────────────────

DATAFRAMES: dict = {}
_CACHE_DIR = Path(CACHE_DIR)
# Guards (re)building of the global DATAFRAMES dict. The HYBRID path runs
# analyze_query and retrieve_context on separate threads, and web requests are
# concurrent, so an unguarded load_dataframes() could let one thread observe a
# half-built (or momentarily empty) dict while another rebuilds it.
_DF_LOCK = threading.Lock()

_DATE_HINTS = ("date", "time", "period", "month", "year")
_DATE_FORMATS = (
    "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d",
    "%d/%m/%Y", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y",
    "%d-%m-%Y", "%d.%m.%Y", "%Y%m%d",
)


# ── Data loading (unchanged logic, kept as-is) ────────────────────────────────

def _maybe_parse_dates(df: pd.DataFrame, threshold: float = 0.7) -> pd.DataFrame:
    for col in df.columns:
        if not any(kw in col for kw in _DATE_HINTS):
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        non_null = df[col].dropna()
        if non_null.empty:
            continue
        sample = non_null.astype(str).head(200)
        chosen_fmt = None
        for fmt in _DATE_FORMATS:
            if pd.to_datetime(sample, format=fmt, errors="coerce").notna().mean() >= threshold:
                chosen_fmt = fmt
                break
        if chosen_fmt is not None:
            parsed = pd.to_datetime(df[col].astype(str), format=chosen_fmt, errors="coerce")
        else:
            parsed = pd.to_datetime(df[col].astype(str), format="ISO8601", errors="coerce")
            if parsed.loc[non_null.index].notna().mean() < threshold:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    parsed = pd.to_datetime(df[col].astype(str), errors="coerce")
        if parsed.loc[non_null.index].notna().mean() >= threshold:
            df[col] = parsed
    return df


def _normalize_df_key(raw: str) -> str:
    """'DATA VENTE' / 'DATA_VENTE.pkl' → 'data_vente'."""
    stem = Path(str(raw)).stem if str(raw).lower().endswith((".pkl", ".csv", ".xlsx", ".xls")) else str(raw)
    key = stem.strip().lower().replace("-", "_").replace(" ", "_")
    while "__" in key:
        key = key.replace("__", "_")
    return key.strip("_")


def _load_alias_sidecar(df_key: str) -> dict:
    """Read aliases written by ingest ({df_key}.meta.json) if present."""
    meta_path = _CACHE_DIR / f"{df_key}.meta.json"
    if not meta_path.is_file():
        # Also try the raw stem form for older caches.
        for p in _CACHE_DIR.glob("*.meta.json"):
            if _normalize_df_key(p.stem.replace(".meta", "")) == df_key or p.stem == df_key:
                meta_path = p
                break
        else:
            return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        aliases = data.get("aliases") or {}
        return {str(k): str(v) for k, v in aliases.items()}
    except Exception as e:
        logger.warning(f"[WARN] Could not read alias sidecar for {df_key}: {e}")
        return {}


# Built-in French ERP abbreviations — used when no sidecar is present yet
# (e.g. cache produced before the ingest upgrade). Keep in sync with ingest.py.
_BUILTIN_COLUMN_ALIASES: dict[str, str] = {
    "cod_client": "Code client",
    "nom_client": "Nom du client",
    "cod_client_fac": "Code client facturé",
    "cod_client_liv": "Code client livré",
    "cod_adr": "Code adresse",
    "des_adr": "Désignation adresse",
    "cod_pays": "Code pays",
    "cod_act": "Code activité",
    "new_rep": "Code représentant",
    "new_rep_client": "Code représentant client",
    "nom_rep": "Nom du représentant",
    "num_fac": "Numéro de facture",
    "num_liv": "Numéro de livraison",
    "num_cmd": "Numéro de commande",
    "num_lin_ven": "Numéro de ligne de vente",
    "typ_lin_ven": "Type de ligne de vente",
    "sta": "Statut",
    "sens": "Sens (debit/credit)",
    "cod_soc": "Code société",
    "cod_fcy": "Code établissement",
    "date_fac": "Date de facture",
    "mois_date_fac": "Mois de facture",
    "annee_date_fac": "Année de facture",
    "date_liv": "Date de livraison",
    "date_exp": "Date d'expédition",
    "cod_art": "Code article / produit",
    "des_art": "Désignation article / produit",
    "qte_uv": "Quantité UV",
    "qte_uvc": "Quantité UVC",
    "qte_kg": "Quantité en kg",
    "uv": "Unité de vente",
    "p_brut": "Prix / montant brut",
    "p_net": "Prix / montant net",
}


def load_dataframes(force: bool = False) -> dict:
    global DATAFRAMES
    with _DF_LOCK:
        # Another thread may have finished loading while we waited on the lock.
        if DATAFRAMES and not force:
            return DATAFRAMES

        # Build into a LOCAL dict and swap it in atomically at the end, so any
        # reader touching the global DATAFRAMES sees either the old fully-built
        # dict or the new one -- never a partially-populated dict.
        new_frames: dict = {}
        if not _CACHE_DIR.exists():
            DATAFRAMES = new_frames
            return DATAFRAMES

        total_start = time.perf_counter()
        for file_path in _CACHE_DIR.glob("*.pkl"):
            try:
                file_start = time.perf_counter()
                df = pd.read_pickle(file_path)
                df.columns = (
                    df.columns.astype(str)
                    .str.strip()
                    .str.lower()
                    .str.replace(" ", "_", regex=False)
                    .str.replace("-", "_", regex=False)
                )
                for col in df.select_dtypes(include="object").columns:
                    df[col] = df[col].map(lambda x: x.strip() if isinstance(x, str) else x)
                df = _maybe_parse_dates(df)

                # Always index under a snake_case key so "DATA VENTE" and
                # "DATA_VENTE" resolve to the same frame.
                df_key = _normalize_df_key(file_path.stem)
                # Prefer sidecar aliases (from ingest), fall back to built-ins.
                aliases = _load_alias_sidecar(df_key) or {
                    c: _BUILTIN_COLUMN_ALIASES[c]
                    for c in df.columns
                    if c in _BUILTIN_COLUMN_ALIASES
                }
                df.attrs["column_aliases"] = aliases
                df.attrs["df_key"] = df_key

                # If two files collapse to the same key, keep the larger one.
                if df_key in new_frames and len(new_frames[df_key]) >= len(df):
                    logger.warning(
                        f"[WARN] Duplicate df key {df_key!r} from {file_path.name}; "
                        "keeping the larger frame already loaded."
                    )
                else:
                    new_frames[df_key] = df

                elapsed = time.perf_counter() - file_start
                logger.info(
                    f"[INFO] Loaded & Normalized {file_path.stem} → {df_key!r} "
                    f"({len(df):,} rows x {len(df.columns)} cols, "
                    f"{len(aliases)} aliases) in {elapsed:.2f}s"
                )
            except Exception as e:
                logger.error(f"[ERROR] {file_path.name}: {e}")
        logger.info(f"[STARTUP] All dataframes ready in {time.perf_counter() - total_start:.2f}s")
        DATAFRAMES = new_frames  # atomic swap
        return DATAFRAMES


def get_available_datasets() -> list:
    if not DATAFRAMES:
        load_dataframes()
    return list(DATAFRAMES.keys())


def get_schema_description(scope: str = "") -> str:
    """Return a compact schema description.

    Qwen 1.5B has a small context window, so we cap each column's sample
    display and limit the overall schema output to avoid prompt overflow.

    When `scope` names a loaded dataframe (e.g. during a correction retry, once
    we already know the target_df), we describe ONLY that dataframe with a
    larger budget. Otherwise the multi-df dump gets truncated to 1500 chars and
    the relevant columns can fall off the end -- exactly the situation that
    makes the model hallucinate column names in the first place.

    Each column line includes dtype + human label (when known) so the model
    stops guessing what ``p_brut`` / ``qte_kg`` mean.
    """
    if not DATAFRAMES:
        load_dataframes()

    # Resolve scope through the DF-name aliaser so "DATA VENTE" still works.
    resolved_scope = resolve_df_name(scope) if scope else ""
    if resolved_scope and resolved_scope in DATAFRAMES:
        items = [(resolved_scope, DATAFRAMES[resolved_scope])]
        char_cap = 3000  # single focused df -> allow a bigger budget
    else:
        items = list(DATAFRAMES.items())
        char_cap = 1800

    lines: list[str] = []
    for name, df in items:
        lines.append(f'DataFrame key (use EXACTLY this string in dfs["..."]): "{name}"')
        lines.append(f"Rows: {len(df):,}")
        lines.append("Columns (name | dtype | label | samples):")
        aliases = df.attrs.get("column_aliases") or {}
        for col in df.columns:
            samples = df[col].dropna().unique()[:2].tolist()
            samples = [str(s)[:40] + "…" if len(str(s)) > 40 else str(s) for s in samples]
            label = aliases.get(col, "")
            label_bit = f" # {label}" if label else ""
            dtype = str(df[col].dtype)
            lines.append(f"  - `{col}` ({dtype}){label_bit}  samples={samples}")
        lines.append("")
    schema = "\n".join(lines)
    # Hard cap to leave room for rules + query.
    if len(schema) > char_cap:
        schema = schema[:char_cap] + "\n...(schema truncated)"
    return schema


def resolve_df_name(name: str) -> str:
    """Map any near-miss DF name to the real loaded key.

    Handles spaces vs underscores, case, and close fuzzy matches so that
    ``DATA_VENTE`` / ``Data Vente`` / ``data vente`` all resolve to
    ``data_vente`` when that is the loaded key.
    """
    if not name:
        return ""
    if not DATAFRAMES:
        load_dataframes()
    if name in DATAFRAMES:
        return name
    norm = _normalize_df_key(name)
    if norm in DATAFRAMES:
        return norm
    # Fuzzy: closest loaded key
    keys = list(DATAFRAMES.keys())
    matches = difflib.get_close_matches(norm, keys, n=1, cutoff=0.6)
    if matches:
        return matches[0]
    # Substring containment either way
    for k in keys:
        if norm in k or k in norm:
            return k
    return name  # leave unchanged; caller will surface the error


# ── Column validation helper ──────────────────────────────────────────────────

def _get_df_columns(df_name: str) -> list[str]:
    """Return the real column list for a loaded dataframe."""
    df = DATAFRAMES.get(df_name)
    if df is None:
        return []
    return list(df.columns)


# Every public attribute/method a DataFrame, Series or Index exposes. Attribute
# access whose name is in here (e.g. df.groupby, s.str, df.index) is pandas API,
# NOT a column, so we must never flag it as a hallucinated column.
_PANDAS_API = set(dir(pd.DataFrame)) | set(dir(pd.Series)) | set(dir(pd.Index))


def _extract_string_subscripts(code: str) -> list[str]:
    """Walk the AST and collect every string used in a READ subscript.

    e.g.  df['revenue_usd']  →  'revenue_usd'
          result['date_fac'] →  'date_fac'
    This lets us spot hallucinated column names before exec().

    We only collect subscripts in a Load context. Assignment targets such as
    ``result['price_z_score'] = ...`` create NEW columns and must not be
    validated against the source schema (doing so wrongly flagged every derived
    column, e.g. in the outlier template, as 'hallucinated').
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return []  # syntax errors are caught later in _validate_code
    keys: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            # Skip write/aug targets: only validate columns that are read.
            if not isinstance(getattr(node, "ctx", None), ast.Load):
                continue
            # e.g. df['col']  or  dfs['df']['col']
            slice_node = node.slice
            if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                keys.append(slice_node.value)
            # Python 3.8 compat: ast.Index wrapper
            elif isinstance(slice_node, ast.Index):
                inner = getattr(slice_node, "value", None)
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    keys.append(inner.value)
    return keys


def _extract_attribute_columns(code: str) -> list[str]:
    """Collect column names referenced via attribute access, e.g. df.revenue_usd.

    Small models frequently reach for a column with dot-access instead of the
    df['col'] form; those hallucinated names previously slipped past validation
    and blew up at exec() with an opaque AttributeError. We only look at
    attribute reads on a bare ``df`` or a ``dfs["..."]`` subscript (the access
    pattern the rules mandate) and we ignore any name that is real pandas API
    (methods, accessors like .str/.dt, properties like .index/.columns). What
    remains is treated as a candidate column and validated against the schema.
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return []
    attrs: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.attr, str):
            continue
        if not isinstance(getattr(node, "ctx", None), ast.Load):
            continue  # attribute assignment target, not a read
        attr = node.attr
        if attr.startswith("_") or attr in _PANDAS_API:
            continue
        base = node.value
        base_is_df = (
            (isinstance(base, ast.Name) and base.id == "df")
            or (
                isinstance(base, ast.Subscript)
                and isinstance(base.value, ast.Name)
                and base.value.id == "dfs"
            )
        )
        if base_is_df:
            attrs.append(attr)
    return attrs



def _extract_created_columns(code: str) -> set[str]:
    """Column names created by rename() and similar constructive calls.

    Recognised patterns:
      - Series/DataFrame ``.rename('newname')``
      - DataFrame ``.rename(columns={'old': 'new', ...})``
      - Dict/DataFrame literal keys used as new column labels (e.g. inside
        ``pd.DataFrame({'x': ...})``).

    These names are legitimately readable later in the same code block.
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return set()
    created: set = set()
    for node in ast.walk(tree):
        # .rename(...) calls
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "rename"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    created.add(arg.value)
            for kw in node.keywords:
                if kw.arg == "columns" and isinstance(kw.value, ast.Dict):
                    for v in kw.value.values:
                        if isinstance(v, ast.Constant) and isinstance(v.value, str):
                            created.add(v.value)
        # pd.DataFrame({'x': ..., 'y': ...}) — literal keys become columns.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "DataFrame"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    for k in arg.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            created.add(k.value)
        # result['newcol'] = ...  (Store subscript with string const).
        if (
            isinstance(node, ast.Subscript)
            and isinstance(getattr(node, "ctx", None), ast.Store)
        ):
            slice_node = node.slice
            if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                created.add(slice_node.value)
    return created


def _validate_columns(code: str, target_df: str) -> None:
    """Raise a descriptive ColumnError when the code references a non-existent column.

    The error message includes the *real* column list AND -- crucially for a
    small model -- a "did you mean" suggestion for each bad name. Without the
    suggestion, Qwen tends to keep re-emitting the same wrong name across all
    retries; with it, the correction succeeds far more often.
    """
    real_cols = _get_df_columns(target_df)
    if not real_cols:
        return  # can't validate if df unknown; let exec() surface the error

    # dfs["<name>"] keys are intentional — exclude the df-name key itself.
    df_keys = set(DATAFRAMES.keys())
    candidate_cols = _extract_string_subscripts(code) + _extract_attribute_columns(code)
    created_cols = _extract_created_columns(code)  # names produced by rename/DataFrame/assign
    # Preserve order while de-duplicating for a clean error message.
    _AGG_SUFFIXES = (
        "_mean", "_sum", "_count", "_min", "_max", "_std", "_median",
        "_size", "_nunique", "_first", "_last",
    )
    seen: set = set()
    bad = []
    for k in candidate_cols:
        if k in real_cols or k in df_keys or k in created_cols or k in seen:
            continue
        # Allow derived agg column names the model will create in the same
        # script (e.g. after .agg({'qte_kg': 'mean'}) → 'qte_kg_mean').
        if any(
            k.endswith(suf) and k[: -len(suf)] in real_cols
            for suf in _AGG_SUFFIXES
        ):
            continue
        seen.add(k)
        bad.append(k)

    if bad:
        hints = []
        for c in bad:
            suggestions = _suggest_column(c, real_cols)
            if suggestions:
                hints.append(f"'{c}' -> did you mean {suggestions}?")
            else:
                hints.append(f"'{c}' (no close match)")
        cols_str = ", ".join(real_cols)
        raise KeyError(
            f"Hallucinated column(s) in '{target_df}': "
            + "; ".join(hints)
            + f". Real columns are: [{cols_str}]"
        )


# ── Column suggestion helper ────────────────────────────────────────────────
# When the model invents a column name we try two strategies:
#   1. String similarity via difflib.get_close_matches (catches typos and near-
#      variants like ``date_fac`` vs ``date_facture``).
#   2. Concept-based aliasing (catches semantically-close but textually-distant
#      pairs like ``unit_price`` vs ``revenue_usd``). We look for any real
#      column whose name CONTAINS one of the alias tokens.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    # price / money concepts -- small models default to "unit_price" for any
    # pricing query, but real schemas usually expose revenue/amount/total instead
    "unit_price":  ("price", "revenue", "amount", "montant", "prix", "cost", "total", "tarif"),
    "price":       ("price", "revenue", "amount", "montant", "prix", "cost", "total", "tarif"),
    "prix":        ("price", "revenue", "amount", "montant", "prix", "cost", "total", "tarif"),
    "amount":      ("amount", "revenue", "montant", "price", "total"),
    "montant":     ("montant", "revenue", "amount", "price", "total"),
    "revenue":     ("revenue", "amount", "montant", "price", "total"),
    "total":       ("total", "revenue", "amount", "montant"),
    "cost":        ("cost", "revenue", "montant", "amount"),
    # dates
    "date":        ("date", "created", "signup", "time", "at"),
    "created_at":  ("created", "signup", "date"),
    "signup":      ("signup", "created", "date"),
    # identity
    "name":        ("name", "first", "last", "customer", "product"),
    "customer":    ("customer", "user", "client", "id", "email", "name"),
    "product":     ("product", "item", "sku", "article", "libelle", "category"),
    "category":    ("category", "type", "group", "product"),
}


def _suggest_column(bad: str, real_cols: list[str]) -> list[str]:
    """Suggest the closest real column names for a hallucinated one."""
    matches = list(difflib.get_close_matches(bad, real_cols, n=2, cutoff=0.6))
    # Concept-based fallback: match by substring against alias hints.
    aliases = _COLUMN_ALIASES.get(bad.lower(), ())
    for hint in aliases:
        for c in real_cols:
            if hint in c and c not in matches:
                matches.append(c)
                if len(matches) >= 3:
                    return matches
    return matches


# ── Automatic df assignment injection ───────────────────────────────────────
# The model very often forgets Rule 1 (``df = dfs["<KEY>"]``) and jumps straight
# to using ``df``. Without a guard that just NameErrors and burns a retry. When
# we already know which df is the target, we can silently prepend the missing
# assignment and let the code run.

def _needs_df_injection(code: str) -> bool:
    """True iff the code READS ``df`` but never assigns it."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    reads = False
    writes = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "df":
            if isinstance(node.ctx, ast.Load):
                reads = True
            elif isinstance(node.ctx, ast.Store):
                writes = True
    return reads and not writes


def _infer_target_df(code: str) -> str:
    """Best-effort guess of the target dataframe from the code's column references.

    Used when the model forgot to include target_dataframe in the JSON output.
    Falls back to the sole loaded df when there is only one.
    """
    if not DATAFRAMES:
        return ""
    if len(DATAFRAMES) == 1:
        return next(iter(DATAFRAMES))
    keys = set(_extract_string_subscripts(code)) | set(_extract_attribute_columns(code))
    if not keys:
        return ""
    best_name, best_score = "", 0
    for name, df in DATAFRAMES.items():
        score = len(keys & set(df.columns))
        if score > best_score:
            best_score, best_name = score, name
    return best_name


def _fix_dangling_expression(code: str) -> str:
    """Rebind a bare final expression to `result` via the AST.

    Qwen2.5:1.5b often emits a trailing bare expression like:
        result.head(1)['col'].values[0], (...)
    which is valid Python but assigns nothing, leaving `result` as the
    intermediate DataFrame instead of the intended scalar/tuple.

    A very common Qwen idiom is to tack a "print the result" bare Name onto the
    SAME line as the assignment, using a semicolon:
        result = df[...].head(50); result
    A line-based rewrite would clobber the whole line (rewriting it to
    ``result = result``) and lose the real assignment -- causing the exec to
    complete with ``result == None`` and the outer check to complain that
    ``result`` was never assigned. We use character offsets from the AST so we
    only replace the exact source span of the trailing expression, and if the
    prefix ends with ``;`` we split it onto its own line for clarity.

    The previous string-based implementation was also fooled by comparisons
    (``df[df['x'] == 5]`` looked like an assignment because it contains ``=``)
    and by multi-line trailing expressions; both are handled correctly here.
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return code  # syntax errors are surfaced later in _validate_code
    if not tree.body:
        return code

    last = tree.body[-1]
    # Only a standalone expression statement needs rebinding. Assignments,
    # loops, ifs, returns, function defs, etc. are left untouched.
    if not isinstance(last, ast.Expr):
        return code

    segment = ast.get_source_segment(code, last)
    if segment is None:
        return code

    # Compute absolute character offsets from (lineno, col_offset).
    end_lineno = getattr(last, "end_lineno", None)
    end_col = getattr(last, "end_col_offset", None)
    if end_lineno is None or end_col is None:
        return code

    lines_with_endings = code.splitlines(keepends=True)
    if last.lineno - 1 >= len(lines_with_endings):
        return code
    start_pos = sum(len(l) for l in lines_with_endings[:last.lineno - 1]) + last.col_offset
    end_pos = sum(len(l) for l in lines_with_endings[:end_lineno - 1]) + end_col

    prefix = code[:start_pos]
    suffix = code[end_pos:]

    # If the trailing expression sits on the same statement line separated by a
    # semicolon, drop the semicolon and put the rebind on its own line so the
    # earlier assignment on that line is preserved verbatim.
    stripped = prefix.rstrip()
    if stripped.endswith(";"):
        prefix = stripped[:-1].rstrip() + "\n"

    return prefix + "result = " + segment + suffix


# ── Prompt templates ─────────────────────────────────────────────────────────
# Condensed from the original 16-rule prompt to the essential rules that a small
# model can actually follow. Verbose rules are collapsed or removed.

_RULES_SHORT = """Rules:
RULE 0 — ABSOLUTE: Do NOT write any import statement. NEVER write "import pandas", "import numpy", or any other import. pandas is already available as `pd` and numpy as `np`. Writing an import will cause an immediate security error and waste a retry.
1. Load the dataframe: df = dfs["<DATAFRAME KEY>"]
2. CRITICAL — only use column names that appear EXACTLY in the schema above. Never invent or guess a column name. If a column does not appear in the schema, say it is unavailable instead of fabricating it.
3. Before using any column, check it with: if 'col' in df.columns — do NOT access a column without this guard when you are not 100% certain it exists.
4. Search ALL relevant text columns with str.contains (case=False, na=False).
5. Store the final result in a variable named `result`. The last line MUST be an assignment: `result = ...`
6. For dates: use pd.to_datetime(df[col], errors='coerce', format='mixed'). Today: {today}.
7. No markdown fences in the code. Return raw Python only.
8. When grouping then filtering aggregates (e.g. top revenue customers), group first, then filter.
9. Return all matching rows for "how many / list all" queries; use .head(50) for exploration only.
RULE 10 — ABSOLUTE: NEVER use df.query() or df.eval(). They cannot access local variables and will always fail. Use boolean masks instead: df[df['col'] > value]."""

_SYSTEM_TEMPLATE = (
    "You are a data analyst. Answer by writing Pandas Python code.\n"
    "Today: {today}\n\n"
    "Available DataFrames:\n{schema_desc}\n\n"
    "{rules}\n\n"
    "Reply with ONLY a JSON object — no prose, no markdown fences:\n"
    '{{"explanation": "<one French sentence: HOW it was computed>", '
    '"code": "<raw Python code>", '
    '"target_dataframe": "<dataframe name>"}}'
)

_CORRECTION_SYSTEM_TEMPLATE = (
    "You are a data analyst. Fix the broken Pandas code.\n"
    "Today: {today}\n\n"
    "DataFrames (these are the ONLY columns that exist — do not use any other):\n{schema_desc}\n\n"
    "Original query: {original_query}\n"
    "Broken code:\n{original_code}\n"
    "Execution error: {error_msg}\n\n"
    "IMPORTANT rules for the fix:\n"
    "- If the error mentions a KeyError for a column name, that column does NOT exist — use the correct name from the schema. If the error contains 'did you mean X?', USE that suggested column name.\n"
    "- If the error says 'imports are not allowed' or 'Security', remove ALL import/from lines completely. pd and np are already available.\n"
    "- If the error says 'result was never assigned', make sure the last line is: result = <expression>\n"
    "- If the error says \"name '<X>' is not defined\" and X is a dataframe name from the schema, START the code with: df = dfs['X']  and then use df.\n"
    "- If the error mentions .query() or .eval() or UndefinedVariableError, replace df.query('...') with a boolean mask: df[df['col'] > value]\n\n"
    "{rules}\n\n"
    "Reply with ONLY a JSON object:\n"
    '{{"explanation": "<one French sentence>", '
    '"code": "<fixed Python code>", '
    '"target_dataframe": "<dataframe name>"}}'
)


# ── JSON parsing helper ───────────────────────────────────────────────────────

def _extract_json(raw: str) -> dict:
    """Robustly extract the first JSON object from model output.

    Qwen2.5:1.5b frequently:
    - Wraps output in ```json ... ``` fences
    - Embeds raw newlines inside JSON string values (causing JSONDecodeError)
    - Adds prose before/after the JSON object

    Strategy (in order):
    1. Strip fences, try direct json.loads
    2. Brace-match to isolate the outermost { } block and parse that
    3. Escape raw newlines/tabs inside JSON string values and retry
    4. Regex-extract individual fields as last resort
    """
    text = raw.strip()

    # 0. Replace triple-quoted strings with single-quoted equivalents.
    # The model sometimes wraps the code value in """...""" which is not
    # valid JSON. Convert them to escaped single-line strings first.
    def _flatten_triple_quotes(s: str) -> str:
        # Replace all triple-quoted spans with a JSON-safe escaped string.
        result_parts = []
        i = 0
        while i < len(s):
            if s[i:i+3] == '"""' :
                end = s.find('"""', i + 3)
                if end == -1:
                    result_parts.append(s[i:])
                    break
                inner = s[i+3:end]
                # Escape for JSON string embedding
                inner = inner.replace("\\", "\\\\").replace('"', '\\\"')
                inner = inner.replace("\n", "\\n").replace("\t", "\\t")
                result_parts.append('"' + inner + '"')
                i = end + 3
            else:
                result_parts.append(s[i])
                i += 1
        return "".join(result_parts)

    text = _flatten_triple_quotes(text)

    # 1. Strip markdown fences.
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text).strip()

    # 2. Direct parse.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Helper: find outermost { } by brace-counting.
    def _brace_extract(s: str):
        depth, start = 0, None
        for i, ch in enumerate(s):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    return s[start: i + 1]
        return None

    # Helper: escape raw newlines/tabs that appear inside JSON string values.
    def _escape_inner_newlines(s: str) -> str:
        out: list[str] = []
        in_str = False
        skip = False
        for ch in s:
            if skip:
                out.append(ch)
                skip = False
                continue
            if ch == "\\" and in_str:
                out.append(ch)
                skip = True
                continue
            if ch == '"':
                in_str = not in_str
                out.append(ch)
                continue
            if in_str and ch == "\n":
                out.append("\\n")
                continue
            if in_str and ch == "\t":
                out.append("\\t")
                continue
            out.append(ch)
        return "".join(out)

    # 3. Brace-match + optional newline-escaping.
    candidate = _brace_extract(text)
    if candidate:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        fixed = _escape_inner_newlines(candidate)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    # 4. Regex field extraction — last resort when JSON is too broken.
    def _re_field(field: str, src: str) -> str:
        m = re.search(
            rf'"{re.escape(field)}"\s*:\s*"((?:[^"\\]|\\.)*)"',
            src, re.DOTALL,
        )
        return m.group(1) if m else ""

    explanation = _re_field("explanation", text)
    code = _re_field("code", text)
    target_dataframe = _re_field("target_dataframe", text)

    if code:
        code = code.replace("\\n", "\n").replace("\\t", "\t")
        return {"explanation": explanation, "code": code, "target_dataframe": target_dataframe}

    raise ValueError(f"No valid JSON found in model output: {text[:300]!r}")


class _PandasCodeOutput:
    """Simple dataclass replacing the Pydantic model (no tool calling needed)."""
    __slots__ = ("explanation", "code", "target_dataframe")

    def __init__(self, explanation: str, code: str, target_dataframe: str):
        self.explanation = explanation
        self.code = code
        self.target_dataframe = target_dataframe


def _parse_pandas_output(raw: str) -> _PandasCodeOutput:
    data = _extract_json(raw)
    explanation = str(data.get("explanation", "")).strip()
    code = str(data.get("code", "")).strip()
    target_dataframe = str(data.get("target_dataframe", "")).strip()

    # Strip any residual markdown fences from the code field itself.
    code = re.sub(r"^```(?:python)?\s*", "", code, flags=re.IGNORECASE)
    code = re.sub(r"\s*```$", "", code).strip()

    if not code:
        raise ValueError("Model returned empty code field.")
    return _PandasCodeOutput(explanation=explanation, code=code, target_dataframe=target_dataframe)


# ── LLM chains (plain StrOutputParser instead of structured_output) ───────────

_plain_chain = (
    ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_TEMPLATE),
        ("user", "{query}"),
    ])
    | llm_client
    | StrOutputParser()
)

_correction_chain = (
    ChatPromptTemplate.from_messages([
        ("system", _CORRECTION_SYSTEM_TEMPLATE),
        ("user", "Fix the code above."),
    ])
    | llm_client
    | StrOutputParser()
)


def generate_pandas_query(query: str) -> _PandasCodeOutput:
    today_str = date.today().isoformat()
    schema_desc = get_schema_description()
    raw = _plain_chain.invoke({
        "query": query,
        "schema_desc": schema_desc,
        "rules": _RULES_SHORT.format(today=today_str),
        "today": today_str,
    })
    return _parse_pandas_output(raw)


def generate_pandas_query_correction(
    query: str, original_code: str, error_msg: str, target_df: str = ""
) -> _PandasCodeOutput:
    today_str = date.today().isoformat()
    # Scope the schema to the target df once we know it, so the correction
    # prompt shows the full, focused column list of the df being fixed.
    schema_desc = get_schema_description(scope=target_df)
    raw = _correction_chain.invoke({
        "today": today_str,
        "schema_desc": schema_desc,
        "original_query": query,
        "original_code": original_code,
        "error_msg": error_msg,
        "rules": _RULES_SHORT.format(today=today_str),
    })
    return _parse_pandas_output(raw)


# ── Safe code execution (unchanged) ──────────────────────────────────────────

def _strip_imports(code: str) -> str:
    """Silently remove any import/from-import lines the model emitted.

    Qwen2.5:1.5b ignores the no-import rule regularly. Rather than burning
    a retry on a security error for something we can trivially fix, we strip
    the offending lines here and let the real logic execute.
    """
    clean = []
    for line in code.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            continue
        clean.append(line)
    return "\n".join(clean)


_ALLOWED_BUILTINS = {
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float",
    "int", "len", "list", "map", "max", "min", "range", "reversed", "round",
    "set", "sorted", "str", "sum", "tuple", "zip", "print",
}

_FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "open", "__import__", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "input", "exit", "quit",
    "breakpoint", "memoryview", "object", "type", "super",
}


def _validate_code(code: str) -> None:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        raise ValueError(f"Generated code has a syntax error: {e}")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("Security: imports are not allowed.")
        if isinstance(node, ast.Attribute) and isinstance(node.attr, str) and node.attr.startswith("__"):
            raise ValueError(f"Security: dunder attribute '{node.attr}' is not allowed.")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError(f"Security: name '{node.id}' is not allowed.")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _FORBIDDEN_CALLS):
            raise ValueError(f"Security: call to '{node.func.id}' is not allowed.")
        # Ban .query() and .eval() — they run in a separate scope that
        # cannot see local variables, causing UndefinedVariableError.
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("query", "eval")):
            raise ValueError(
                f"Use of .{node.func.attr}() is forbidden. "
                "Use a boolean mask instead: df[df['col'] > value]"
            )


def _rewrite_df_keys_in_code(code: str, old_key: str, new_key: str) -> str:
    """Replace dfs['old'] / dfs[\"old\"] occurrences with the resolved key."""
    if not old_key or not new_key or old_key == new_key:
        return code
    for quote in ("'", '"'):
        code = code.replace(f"dfs[{quote}{old_key}{quote}]", f"dfs[{quote}{new_key}{quote}]")
    return code


def _flatten_multiindex_columns(obj: Any) -> Any:
    """Flatten MultiIndex columns produced by groupby.agg(list) to col_stat.

    Turns ('qte_kg', 'mean') → 'qte_kg_mean' so downstream code (and the
    model) can reference a single string key.
    """
    if not isinstance(obj, pd.DataFrame):
        return obj
    if not isinstance(obj.columns, pd.MultiIndex):
        return obj
    flat = []
    for col in obj.columns:
        parts = [str(p) for p in col if p is not None and str(p) != ""]
        flat.append("_".join(parts) if parts else "col")
    out = obj.copy()
    out.columns = flat
    return out


def execute_pandas_code(
    code: str,
    dfs: dict,
    target_df: str = "",
    trusted: bool = False,
) -> Any:
    """Execute pandas code inside a sandbox.

    ``trusted=True`` skips column-hallucination validation. Use ONLY for
    code you generated deterministically (intent templates) — never for
    raw LLM output.
    """
    code = textwrap.dedent(code).strip()
    # Strip any import lines the model emitted despite instructions.
    # We do this silently rather than erroring, so retries focus on logic.
    code = _strip_imports(code)
    # Fix bare final expressions before any other validation.
    code = _fix_dangling_expression(code)
    # Resolve DF name aliases (spaces / case / underscores).
    if target_df:
        resolved = resolve_df_name(target_df)
        if resolved != target_df:
            code = _rewrite_df_keys_in_code(code, target_df, resolved)
            target_df = resolved
    # Auto-inject `df = dfs["<target_df>"]` when the model forgot Rule 1 but
    # we already know which dataframe is the target. Without this, a trivial
    # NameError burns an entire correction retry on something we can fix
    # deterministically here.
    if target_df and target_df in dfs and _needs_df_injection(code):
        logger.info(f"[ANALYTICS] Auto-injecting: df = dfs['{target_df}']")
        code = f"df = dfs['{target_df}']\n" + code
    # Structural / security checks.
    _validate_code(code)
    # Ensure `result` is assigned somewhere in the code.
    if not re.search(r"\bresult\s*=", code):
        raise ValueError(
            "Generated code never assigns `result`. "
            "The code must contain: result = <expression>."
        )
    # Column existence check — catches hallucinated names before exec().
    # Trusted (template) code is validated by us at design time; skip.
    if target_df and not trusted:
        _validate_columns(code, target_df)
    safe_builtins = {
        name: getattr(builtins, name)
        for name in _ALLOWED_BUILTINS
        if hasattr(builtins, name)
    }
    safe_globals = {"__builtins__": safe_builtins, "pd": pd, "np": np}
    # Expose each loaded dataframe under its bare key too. The small model
    # regularly writes ``chroma_dataset[...]`` as if the dataframe NAME were a
    # Python variable, and it re-emits the same wrong code on every correction
    # retry because that prior is very sticky. Making both access patterns
    # valid at exec time (``dfs['name']`` AND ``name`` directly) turns what was
    # a hard failure loop into a working query -- while ``df = dfs['name']``
    # keeps working for the correct pattern.
    local_vars = {"dfs": dfs, "result": None}
    for _name, _df in dfs.items():
        # Only expose keys that are valid Python identifiers so we don't
        # accidentally shadow builtins or introduce weird syntax edge cases.
        if _name.isidentifier() and _name not in local_vars and _name not in safe_globals:
            local_vars[_name] = _df
    exec(compile(code, "<analytics>", "exec"), safe_globals, local_vars)
    result = local_vars.get("result")
    if result is None:
        raise ValueError(
            "Code executed successfully but `result` was never assigned. "
            "The last line of the code MUST be: result = <expression>."
        )
    # Flatten MultiIndex columns so 'qte_kg_mean' style names work.
    result = _flatten_multiindex_columns(result)
    return result


# ── Result formatting (unchanged) ─────────────────────────────────────────────

def _df_to_records(df: pd.DataFrame) -> list:
    safe = df.copy()
    for col in safe.columns:
        if pd.api.types.is_datetime64_any_dtype(safe[col]):
            safe[col] = safe[col].dt.strftime("%Y-%m-%d").where(safe[col].notna(), None)
    safe = safe.astype(object).where(pd.notna(safe), None)
    return safe.to_dict(orient="records")


def convert_numpy(obj: Any) -> Any:
    if obj is None or obj is pd.NaT:
        return None
    if isinstance(obj, dict):
        return {k: convert_numpy(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [convert_numpy(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return obj.isoformat()
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        val = float(obj)
        return None if np.isnan(val) else val
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return [convert_numpy(v) for v in obj.tolist()]
    if isinstance(obj, (pd.DataFrame, pd.Series)):
        return obj
    if pd.api.types.is_scalar(obj) and pd.isna(obj):
        return None
    return obj


def format_result(result: Any) -> dict:
    if result is None:
        return {"answer": "Aucun résultat n'a été renvoyé.", "data": []}
    if isinstance(result, pd.DataFrame):
        n = len(result)
        return {
            "answer": (
                f"{n} enregistrement correspondant à la requête."
                if n == 1
                else f"{n} enregistrements correspondant à la requête."
            ),
            "data": _df_to_records(result),
        }
    if isinstance(result, pd.Series):
        return {
            "answer": "Résultat calculé (série).",
            "data": _df_to_records(result.reset_index()),
        }
    if isinstance(result, dict):
        n = len(result)
        return {
            "answer": (
                f"{n} valeur calculée."
                if n == 1
                else f"{n} valeurs calculées."
            ),
            "data": [result],
        }
    if isinstance(result, list):
        data = result if all(isinstance(i, dict) for i in result) else [{"value": i} for i in result]
        n = len(result)
        return {
            "answer": (
                f"{n} enregistrement trouvé."
                if n == 1
                else f"{n} enregistrements trouvés."
            ),
            "data": data,
        }
    return {"answer": f"Résultat : {result}", "data": [{"value": result}]}


# ── Answer composer ──────────────────────────────────────────────────────────
# Shortened system prompt to respect the small model's context budget.
# The small model often echoes the user prompt (e.g. "Résultat (JSON): [...]").
# We forbid that explicitly and fall back to a deterministic French summary
# whenever the model output still looks like raw JSON / prompt echo.

_COMPOSER_SYSTEM = (
    "Tu es un analyste BI. Tu rédiges TOUJOURS la réponse finale en français, "
    "même si la question est en anglais.\n"
    "Utilise UNIQUEMENT les chiffres et valeurs des DONNÉES fournies. "
    "Ne jamais inventer.\n"
    "Format OBLIGATOIRE:\n"
    "- Un titre court en gras (**Titre**)\n"
    "- Puis des puces avec les chiffres concrets (noms, montants, ids)\n"
    "Règles ABSOLUES:\n"
    "- N'affiche JAMAIS de JSON brut, de crochets {{ }}, ni de blocs de code.\n"
    "- N'écris JAMAIS les libellés « Résultat (JSON) », « Comment calculé », "
    "« Question: » ou tout autre texte du prompt.\n"
    "- Ne répète pas les données sous forme de liste d'objets JSON.\n"
    "- Si beaucoup de lignes : résume le nombre total et cite 3 à 5 exemples "
    "représentatifs en prose/puces.\n"
    "Sois concis et professionnel."
)

_composer_chain = (
    ChatPromptTemplate.from_messages([
        ("system", _COMPOSER_SYSTEM),
        (
            "user",
            "Question utilisateur:\n{query}\n\n"
            "Méthode de calcul (contexte interne, ne pas recopier tel quel):\n"
            "{explanation}\n\n"
            "DONNÉES à interpréter (ne pas coller ce bloc dans la réponse):\n"
            "{data}\n\n"
            "Rédige maintenant la réponse finale en français, sans JSON.",
        ),
    ])
    | llm_client
    | StrOutputParser()
)


def _looks_like_raw_json_or_prompt_echo(text: str) -> bool:
    """True when the model dumped JSON or echoed the composer prompt."""
    if not text or not text.strip():
        return True
    s = text.strip()
    low = s.lower()
    # Prompt-echo markers (including older wording).
    echo_markers = (
        "résultat (json)",
        "resultat (json)",
        "comment calculé",
        "comment calcule",
        "données à interpréter",
        "donnees a interpreter",
        "méthode de calcul",
        "methode de calcul",
        "question utilisateur:",
        "question:\n",
    )
    if any(m in low for m in echo_markers):
        return True
    # Starts like a JSON array/object dump.
    if s[0] in "{[":
        return True
    # Dense JSON-ish payload (many quoted keys).
    if s.count('":') >= 3 or s.count("':") >= 3:
        return True
    # Code fence wrapping JSON.
    if low.startswith("```") and ("{" in s or "[" in s):
        return True
    return False


def _format_value_fr(value: Any) -> str:
    """Human-readable French-friendly scalar."""
    if value is None:
        return "—"
    if isinstance(value, float):
        if abs(value - round(value)) < 1e-9:
            return f"{int(round(value))}"
        return f"{value:.2f}".replace(".", ",")
    if isinstance(value, bool):
        return "oui" if value else "non"
    return str(value)


def _record_label(record: dict, max_fields: int = 6) -> str:
    """One-line French summary of a dict row, preferring identity-like keys."""
    if not isinstance(record, dict) or not record:
        return _format_value_fr(record)

    preferred = (
        "id", "user_id", "customer_id", "transaction_id", "order_id",
        "first_name", "last_name", "name", "email", "product", "product_name",
        "unit_price", "price", "prix", "amount", "montant", "quantity",
        "price_z_score", "price_mean_for_product", "price_mean_global",
    )
    parts: list[str] = []
    used = set()
    for key in preferred:
        for rk, rv in record.items():
            if rk.lower() == key or key in rk.lower():
                if rk in used:
                    continue
                parts.append(f"{rk}={_format_value_fr(rv)}")
                used.add(rk)
                break
        if len(parts) >= max_fields:
            break
    if not parts:
        for rk, rv in list(record.items())[:max_fields]:
            parts.append(f"{rk}={_format_value_fr(rv)}")
    return " · ".join(parts)


def fallback_french_summary(query: str, explanation: str, data: Any) -> str:
    """Deterministic French prose — never exposes raw JSON to the user."""
    data = convert_numpy(data)
    title = "**Résultat de l'analyse**"
    lines_out: list[str] = [title, ""]

    if explanation:
        lines_out.append(explanation.rstrip(".") + ".")
        lines_out.append("")

    if data is None or data == [] or data == {}:
        lines_out.append("Aucun enregistrement ne correspond à cette requête.")
        return "\n".join(lines_out).strip()

    if isinstance(data, list):
        n = len(data)
        if n == 0:
            lines_out.append("Aucun enregistrement ne correspond à cette requête.")
            return "\n".join(lines_out).strip()
        lines_out.append(
            f"**{n} enregistrement trouvé.**"
            if n == 1
            else f"**{n} enregistrements trouvés.**"
        )
        preview = data[:5]
        lines_out.append("")
        lines_out.append("Exemples :" if n > 1 else "Détail :")
        for row in preview:
            if isinstance(row, dict):
                lines_out.append(f"- {_record_label(row)}")
            else:
                lines_out.append(f"- {_format_value_fr(row)}")
        if n > len(preview):
            lines_out.append(f"- … et {n - len(preview)} autre(s).")
        return "\n".join(lines_out).strip()

    if isinstance(data, dict):
        if all(not isinstance(v, (list, dict)) for v in data.values()):
            lines_out.append("Valeurs calculées :")
            for k, v in data.items():
                lines_out.append(f"- **{k}** : {_format_value_fr(v)}")
            return "\n".join(lines_out).strip()
        lines_out.append("Résultat :")
        lines_out.append(f"- {_record_label(data)}")
        return "\n".join(lines_out).strip()

    lines_out.append(f"Résultat : {_format_value_fr(data)}")
    return "\n".join(lines_out).strip()


def compose_analytics_answer(query: str, explanation: str, data: Any) -> str:
    fallback = fallback_french_summary(query, explanation, data)
    try:
        data_json = json.dumps(convert_numpy(data), ensure_ascii=False, default=str)
        # Cap JSON payload: small model + large tables = truncated output.
        if len(data_json) > 4000:
            data_json = data_json[:4000] + " ...(truncated)"
        answer = _composer_chain.invoke({
            "query": query,
            "explanation": explanation,
            "data": data_json,
        }).strip()
        if not answer or _looks_like_raw_json_or_prompt_echo(answer):
            logger.info(
                "[ANALYTICS] Composer output looked like JSON/prompt echo; "
                "using deterministic French summary."
            )
            return fallback
        return answer
    except Exception as e:
        logger.warning(f"[ANALYTICS] Composer failed, using French fallback: {e}")
        return fallback


# ── Main entry point ──────────────────────────────────────────────────────────

# ── Intent-based code templates ─────────────────────────────────────────────
# Qwen2.5:1.5b cannot reliably generate correct code for multi-step patterns
# (outlier detection, avg-above-multiple, top-N, group-by sums). We detect
# these intents via keywords and inject pre-validated, parameterised code,
# bypassing the LLM code-generation step entirely for these cases.

import re as _re  # already imported above but alias for clarity in this block


def _find_col(df: pd.DataFrame, hints: tuple[str, ...] | list[str]) -> str | None:
    """Return the first column whose name contains any of the hint tokens."""
    cols = list(df.columns)
    lower_map = {c.lower(): c for c in cols}
    for h in hints:
        if h.lower() in lower_map:
            return lower_map[h.lower()]
    for h in hints:
        for c in cols:
            if h.lower() in c.lower():
                return c
    return None


def _extract_number(query: str, default: float = 2.0) -> float:
    """Pull a multiplier / top-N from the query text."""
    q = query.lower()
    word_map = {
        "twice": 2, "double": 2, "deux fois": 2, "2 fois": 2,
        "three times": 3, "triple": 3, "trois fois": 3,
        "four times": 4, "quatre fois": 4,
        "five times": 5, "cinq fois": 5,
        "ten times": 10, "dix fois": 10,
    }
    for w, n in word_map.items():
        if w in q:
            return float(n)
    m = _re.search(r"\btop[\s\-]*(\d+)\b", q)
    if m:
        return float(m.group(1))
    m = _re.search(r"\b(\d+(?:[.,]\d+)?)\s*(?:x|×|times|fois)\b", q)
    if m:
        return float(m.group(1).replace(",", "."))
    m = _re.search(r"\b(\d+(?:[.,]\d+)?)\b", q)
    if m:
        val = float(m.group(1).replace(",", "."))
        if 1900 <= val <= 2100:
            return default
        return val
    return default


def _pick_sales_df() -> str:
    """Prefer a sales/transactions dataframe; else the largest one."""
    sales_hints = (
        "vente", "sale", "transaction", "order", "commande",
        "invoice", "facture", "data_vente",
    )
    for name in DATAFRAMES:
        if any(h in name.lower() for h in sales_hints):
            return name
    if DATAFRAMES:
        return max(DATAFRAMES, key=lambda k: len(DATAFRAMES[k]))
    return ""


def _pick_best_df_for_intent(intent: str = "") -> str:
    return _pick_sales_df()


def _build_outlier_code(df_name: str, query: str = "") -> tuple[str, str]:
    """Price-outlier detection: mean ± 2 std (z-score) per product group."""
    df = DATAFRAMES.get(df_name)
    if df is None:
        return "", "DataFrame introuvable."

    price_col = _find_col(df, (
        "unit_price", "price", "prix", "montant", "amount",
        "revenue_usd", "total", "cost", "tarif", "p_net", "p_brut",
    ))
    product_col = _find_col(df, (
        "product", "produit", "article", "item", "libelle",
        "designation", "ref", "sku", "name", "cod_art", "des_art",
    ))
    if price_col is None:
        return "", "Aucune colonne de prix trouvée dans le DataFrame."

    explanation = (
        f"Identification des transactions dont le prix unitaire ('{price_col}') "
        f"s'écarte de plus de 2 écarts-types de la moyenne"
        + (f" par produit ('{product_col}')." if product_col else " globale.")
    )
    if product_col:
        code = f"""df = dfs['{df_name}']
grp = df.groupby('{product_col}')['{price_col}']
mean = grp.transform('mean')
std  = grp.transform('std').fillna(0)
z    = (df['{price_col}'] - mean) / std.replace(0, 1)
result = df[z.abs() > 2].copy()
result['price_mean_for_product'] = mean[result.index].round(2)
result['price_z_score']          = z[result.index].round(2)
result = result.sort_values('price_z_score', key=lambda s: s.abs(), ascending=False)
"""
    else:
        code = f"""df = dfs['{df_name}']
mean = df['{price_col}'].mean()
std  = df['{price_col}'].std()
z    = (df['{price_col}'] - mean) / (std if std else 1)
result = df[z.abs() > 2].copy()
result['price_mean_global'] = round(mean, 2)
result['price_z_score']     = z[result.index].round(2)
result = result.sort_values('price_z_score', key=lambda s: s.abs(), ascending=False)
"""
    return code.strip(), explanation


def _build_avg_above_multiple_code(df_name: str, query: str = "") -> tuple[str, str]:
    """Customers whose average order value > N × overall customer average."""
    df = DATAFRAMES.get(df_name)
    if df is None:
        return "", "DataFrame introuvable."

    group_col = _find_col(df, (
        "cod_client", "nom_client", "client", "customer", "user_id",
        "customer_id", "user", "email", "name",
    ))
    value_col = _find_col(df, (
        "p_net", "p_brut", "montant", "amount", "total", "revenue",
        "price", "prix", "unit_price", "order_value",
    ))
    if group_col is None or value_col is None:
        missing = []
        if group_col is None:
            missing.append("client/customer")
        if value_col is None:
            missing.append("montant/prix")
        return "", f"Colonnes introuvables pour ce calcul: {', '.join(missing)}."

    mult = _extract_number(query, default=2.0)
    if mult < 1.1 or mult > 100:
        mult = 2.0

    explanation = (
        f"Clients ('{group_col}') dont la moyenne de '{value_col}' dépasse "
        f"{mult:g}× la moyenne globale de tous les clients."
    )
    code = f"""df = dfs['{df_name}']
per_group = df.groupby('{group_col}')['{value_col}'].mean()
overall = per_group.mean()
mask = per_group > ({mult:g} * overall)
result = (
    per_group[mask]
    .reset_index()
    .rename(columns={{'{value_col}': 'avg_{value_col}'}})
)
result['overall_customer_avg'] = round(float(overall), 2)
result['threshold'] = round(float({mult:g} * overall), 2)
result['ratio_vs_overall'] = (result['avg_{value_col}'] / overall).round(2)
result = result.sort_values('avg_{value_col}', ascending=False)
"""
    return code.strip(), explanation


def _build_top_n_code(df_name: str, query: str = "") -> tuple[str, str]:
    """Top-N groups by a summed metric."""
    df = DATAFRAMES.get(df_name)
    if df is None:
        return "", "DataFrame introuvable."

    group_col = _find_col(df, (
        "cod_client", "nom_client", "client", "customer", "product",
        "cod_art", "des_art", "article", "nom_rep", "new_rep",
        "category", "country", "cod_pays",
    ))
    value_col = _find_col(df, (
        "p_net", "p_brut", "montant", "amount", "total", "revenue",
        "qte_kg", "quantity", "qte_uv",
    ))
    if group_col is None or value_col is None:
        return "", "Colonnes de regroupement / métrique introuvables."

    n = int(_extract_number(query, default=10))
    n = max(1, min(n, 100))

    explanation = f"Top {n} '{group_col}' classés par somme de '{value_col}'."
    code = f"""df = dfs['{df_name}']
result = (
    df.groupby('{group_col}', dropna=False)['{value_col}']
    .sum()
    .reset_index()
    .rename(columns={{'{value_col}': 'total_{value_col}'}})
    .sort_values('total_{value_col}', ascending=False)
    .head({n})
)
"""
    return code.strip(), explanation


def _build_group_sum_code(df_name: str, query: str = "") -> tuple[str, str]:
    """Generic group-by sum (no top-N cut)."""
    df = DATAFRAMES.get(df_name)
    if df is None:
        return "", "DataFrame introuvable."

    group_col = _find_col(df, (
        "cod_client", "nom_client", "client", "customer", "product",
        "cod_art", "des_art", "mois_date_fac", "annee_date_fac",
        "nom_rep", "cod_pays", "category",
    ))
    value_col = _find_col(df, (
        "p_net", "p_brut", "montant", "amount", "total", "revenue",
        "qte_kg", "quantity", "qte_uv",
    ))
    if group_col is None or value_col is None:
        return "", "Colonnes de regroupement / métrique introuvables."

    explanation = f"Somme de '{value_col}' regroupée par '{group_col}'."
    code = f"""df = dfs['{df_name}']
result = (
    df.groupby('{group_col}', dropna=False)['{value_col}']
    .sum()
    .reset_index()
    .rename(columns={{'{value_col}': 'total_{value_col}'}})
    .sort_values('total_{value_col}', ascending=False)
)
"""
    return code.strip(), explanation


def _build_invoice_multi_line_code(df_name: str, query: str = "") -> tuple[str, str]:
    """Invoices with multiple product lines + total value per invoice."""
    df = DATAFRAMES.get(df_name)
    if df is None:
        return "", "DataFrame introuvable."

    inv_col = _find_col(df, (
        "num_fac", "invoice", "facture", "order_id", "num_cmd", "invoice_id",
    ))
    value_col = _find_col(df, (
        "p_net", "p_brut", "montant", "amount", "total", "revenue", "price",
    ))
    if inv_col is None:
        return "", "Aucune colonne de numéro de facture trouvée."
    if value_col is None:
        return "", "Aucune colonne de montant trouvée."

    explanation = (
        f"Factures ('{inv_col}') contenant plusieurs lignes produit, "
        f"avec le total de '{value_col}' par facture."
    )
    code = f"""df = dfs['{df_name}']
line_counts = df.groupby('{inv_col}').size().rename('n_lines')
totals = df.groupby('{inv_col}')['{value_col}'].sum().rename('total_{value_col}')
result = pd.concat([line_counts, totals], axis=1).reset_index()
result = result[result['n_lines'] > 1].sort_values('total_{value_col}', ascending=False)
"""
    return code.strip(), explanation


_TEMPLATES: list[dict] = [
    {
        "name": "outlier_price",
        "keywords": (
            "differs significantly", "price anomaly", "anomalies", "outlier",
            "unusual price", "abnormal price", "significant difference",
            "écart significatif", "prix anormal", "prix aberrant",
            "differ significantly", "price outlier",
            "prices differ", "prices vary", "price varies", "differ across",
            "products whose price", "product prices", "inconsistent price",
            "inconsistent prices", "price inconsistency",
            "prix qui varie", "prix qui varient", "prix incohérent", "prix incohérents",
            "prix différent", "prix différents", "significativement",
        ),
        "builder": _build_outlier_code,
    },
    {
        "name": "avg_above_multiple",
        "keywords": (
            "more than twice", "twice the", "two times", "2 times",
            "more than double", "double the average",
            "deux fois", "2 fois", "plus de deux fois",
            "times the overall", "times the average", "fois la moyenne",
            "supérieur à la moyenne", "superieur a la moyenne",
            "above the overall", "above average", "above the average",
            "average order value", "panier moyen",
            "moyenne globale", "overall customer average",
            "more than thrice", "three times the",
        ),
        "builder": _build_avg_above_multiple_code,
    },
    {
        "name": "top_n",
        "keywords": (
            "top ", "top-", "topn", "meilleurs", "meilleur",
            "highest", "classement", "ranking", "rank ",
            "plus grands", "plus grandes", "plus élevés", "plus eleves",
            "largest", "biggest",
        ),
        "builder": _build_top_n_code,
    },
    {
        "name": "invoice_multi_line",
        "keywords": (
            "multiple product lines", "multi-line", "multiline",
            "plusieurs lignes", "plusieurs produits",
            "invoices that contain multiple", "factures avec plusieurs",
            "total value of each invoice", "total par facture",
            "invoice total", "montant par facture",
        ),
        "builder": _build_invoice_multi_line_code,
    },
    {
        "name": "group_sum",
        "keywords": (
            "sum by", "total by", "grouped by", "group by",
            "somme par", "total par", "par client", "par produit",
            "par mois", "par année", "par annee", "par représentant",
            "revenue by", "sales by", "ca par",
        ),
        "builder": _build_group_sum_code,
    },
]


def _match_template(query: str) -> dict | None:
    """Return the first matching template, or None."""
    q = query.lower()
    for tmpl in _TEMPLATES:
        if any(kw in q for kw in tmpl["keywords"]):
            return tmpl
    return None


def _run_template(tmpl: dict, query: str, compose: bool) -> dict | None:
    """Execute a template end-to-end. Return formatted result or None on failure."""
    df_name = _pick_best_df_for_intent(tmpl["name"])
    if not df_name:
        return None
    try:
        code, expl = tmpl["builder"](df_name, query)
    except Exception as e:
        logger.warning(f"[ANALYTICS] Template {tmpl['name']} builder failed: {e}")
        return None
    if not code:
        logger.info(f"[ANALYTICS] Template {tmpl['name']} produced no code: {expl}")
        return None
    logger.info(f"[ANALYTICS] Using template '{tmpl['name']}' on '{df_name}'")
    try:
        result = execute_pandas_code(code, DATAFRAMES, target_df=df_name, trusted=True)
        formatted = format_result(result)
        formatted["explanation"] = expl
        formatted["answer"] = (
            compose_analytics_answer(query, expl, formatted.get("data"))
            if compose else expl
        )
        return convert_numpy(formatted)
    except Exception as e:
        logger.warning(
            f"[ANALYTICS] Template {tmpl['name']} execution failed ({e}); "
            "falling through to LLM."
        )
        return None


def analyze_query(query: str, compose: bool = True) -> dict:
    """Answer an analytics query.

    When ``compose`` is False we skip the answer-composer LLM call and return
    the (cheap) explanation as the answer. The HYBRID path passes compose=False
    because it feeds the raw ``data`` into its own composer -- composing here
    too would be a wasted generation on the latency-sensitive hot path.
    """
    logger.info(f"[ANALYTICS] Query: {query}")
    if not DATAFRAMES:
        load_dataframes()
    if not DATAFRAMES:
        return {"answer": "Aucun jeu de données structuré n'est chargé.", "data": []}

    # ── Template shortcut: deterministic code for known intents ──────────────
    tmpl = _match_template(query)
    if tmpl is not None:
        templated = _run_template(tmpl, query, compose=compose)
        if templated is not None:
            return templated


    code = None
    explanation = ""
    error_msg = ""
    max_retries = 3  # keep 3 — small models need more correction attempts

    target_df = ""  # populated after first successful parse

    for attempt in range(max_retries):
        try:
            if attempt == 0:
                pandas_output = generate_pandas_query(query)
            else:
                logger.info(f"[ANALYTICS] Attempt {attempt + 1}: correcting previous failure…")
                pandas_output = generate_pandas_query_correction(
                    query, code, error_msg, target_df=target_df
                )

            code = pandas_output.code
            explanation = pandas_output.explanation
            target_df = pandas_output.target_dataframe or target_df
            # The model sometimes omits target_dataframe on the first attempt.
            # Infer it from the code's column references so we can (a) validate
            # columns and (b) auto-inject the df assignment below.
            if not target_df:
                target_df = _infer_target_df(code)
                if target_df:
                    logger.info(f"[ANALYTICS] Inferred target_df: {target_df}")
            # Normalise "DATA VENTE" / "DATA_VENTE" → real loaded key.
            if target_df:
                resolved = resolve_df_name(target_df)
                if resolved != target_df:
                    logger.info(
                        f"[ANALYTICS] Resolved target_df {target_df!r} → {resolved!r}"
                    )
                    # Also rewrite dfs['OLD'] → dfs['NEW'] inside the code so
                    # exec() hits the real key.
                    code = _rewrite_df_keys_in_code(code, target_df, resolved)
                    target_df = resolved
            logger.info(f"[ANALYTICS] Target DF: {target_df}")
            logger.info(f"[ANALYTICS] Explanation: {explanation}")
            logger.info(f"[ANALYTICS] Code:\n{code}")

            result = execute_pandas_code(code, DATAFRAMES, target_df=target_df)
            formatted = format_result(result)
            formatted["explanation"] = explanation
            formatted["answer"] = (
                compose_analytics_answer(query, explanation, formatted.get("data"))
                if compose else explanation
            )
            return convert_numpy(formatted)

        except KeyError as e:
            # Enrich the error with the real column list AND (via the KeyError
            # message itself) 'did you mean' hints so the correction prompt can
            # steer the model away from hallucinated names on the next attempt.
            bad_col = str(e).strip("'\"")
            real_cols = _get_df_columns(target_df) if target_df else []
            cols_hint = (
                f" Real columns in '{target_df}': [{', '.join(real_cols)}]"
                if real_cols else ""
            )
            error_msg = f"KeyError: {bad_col}.{cols_hint}"
            logger.error(f"[ANALYTICS ERROR] Attempt {attempt + 1} failed: {error_msg}")
            if attempt == max_retries - 1:
                logger.exception("Analytics failed after %s attempts", max_retries)
                # User-friendly final message: list the real columns so the end
                # user can rephrase, instead of surfacing a raw retry counter.
                cols_preview = ", ".join(real_cols[:12]) + ("…" if len(real_cols) > 12 else "")
                if target_df and cols_preview:
                    answer = (
                        "Je ne peux pas répondre à cette question avec les colonnes "
                        f"disponibles du dataset **{target_df}**.\n\n"
                        f"Colonnes disponibles : {cols_preview}\n\n"
                        "Reformulez votre question en utilisant ces colonnes."
                    )
                else:
                    answer = (
                        f"Le calcul a échoué après {max_retries} tentatives. "
                        f"Colonne introuvable : '{bad_col}'."
                    )
                return {"answer": answer, "data": []}

        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            logger.error(f"[ANALYTICS ERROR] Attempt {attempt + 1} failed: {error_msg}")
            if attempt == max_retries - 1:
                logger.exception("Analytics failed after %s attempts", max_retries)
                return {
                    "answer": (
                        f"Le calcul a échoué après {max_retries} tentatives. "
                        f"Erreur: {str(e)}"
                    ),
                    "data": [],
                }

    # Defensive final fallback: the loop always returns on the last attempt,
    # but if control ever reaches here (e.g. max_retries <= 0) we must still
    # return a dict. Returning None would crash callers doing result.get(...).
    return {
        "answer": f"Le calcul a échoué après {max_retries} tentatives.",
        "data": [],
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Testing Analytics Code Generator…")
    load_dataframes()
    if DATAFRAMES:
        q = "show me the user's country with the signup date before 2022 and show the score"
        res = analyze_query(q)
        logger.info(json.dumps(res, indent=2, default=str))
    else:
        logger.info("No cache dataframes found. Run ingestion first.")
