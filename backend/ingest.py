import logging

logger = logging.getLogger(__name__)

import json
import hashlib
import re
import unicodedata
from pathlib import Path

import pandas as pd

from langchain_core.documents import Document
from langchain_chroma import Chroma
from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
    Docx2txtLoader,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import (
    PDF_DIR,
    DOCX_DIR,
    TXT_DIR,
    CSV_DIR,
    CHROMA_DIR,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    CACHE_DIR,
    MIN_CHUNK_CHARS,
)

from models import embedding_function


# ── DataFrame key + column alias helpers ────────────────────────────────────
# DF keys used to be the raw file stem (e.g. "DATA VENTE"). Small models then
# hallucinate "DATA_VENTE" / "data_vente" and burn retries. We normalise once
# at ingest so every downstream consumer sees a single snake_case key.
# Column aliases give human-readable meanings for cryptic French abbreviations
# (p_brut, qte_kg, …) so the analytics schema prompt grounds the LLM.

def normalize_df_key(raw: str) -> str:
    """'DATA VENTE.csv' / 'DATA VENTE' → 'data_vente'."""
    stem = Path(str(raw)).stem if "." in Path(str(raw)).name else str(raw)
    key = stem.strip().lower()
    key = key.replace("-", "_").replace(" ", "_")
    while "__" in key:
        key = key.replace("__", "_")
    return key.strip("_")


# Built-in French ERP / sales abbreviations. Extended at runtime by any
# ``column_aliases.yaml`` sitting next to the CSV (optional).
_DEFAULT_COLUMN_ALIASES: dict[str, str] = {
    # identity / client
    "cod_client": "Code client",
    "nom_client": "Nom du client",
    "cod_client_fac": "Code client facturé",
    "cod_client_liv": "Code client livré",
    "cod_adr": "Code adresse",
    "des_adr": "Désignation adresse",
    "cod_pays": "Code pays",
    "cod_act": "Code activité",
    # reps
    "new_rep": "Code représentant",
    "new_rep_client": "Code représentant client",
    "nom_rep": "Nom du représentant",
    # document / invoice
    "num_fac": "Numéro de facture",
    "num_liv": "Numéro de livraison",
    "num_cmd": "Numéro de commande",
    "num_lin_ven": "Numéro de ligne de vente",
    "typ_lin_ven": "Type de ligne de vente",
    "sta": "Statut",
    "sens": "Sens (debit/credit)",
    "cod_soc": "Code société",
    "cod_fcy": "Code établissement",
    # dates
    "date_fac": "Date de facture",
    "mois_date_fac": "Mois de facture",
    "annee_date_fac": "Année de facture",
    "date_liv": "Date de livraison",
    "date_exp": "Date d'expédition",
    # product
    "cod_art": "Code article / produit",
    "des_art": "Désignation article / produit",
    # quantities & amounts
    "qte_uv": "Quantité UV",
    "qte_uvc": "Quantité UVC",
    "qte_kg": "Quantité en kg",
    "uv": "Unité de vente",
    "p_brut": "Prix / montant brut",
    "p_net": "Prix / montant net",
    # english common
    "unit_price": "Prix unitaire",
    "price": "Prix",
    "amount": "Montant",
    "quantity": "Quantité",
    "product": "Produit",
    "customer": "Client",
    "email": "Email",
    "first_name": "Prénom",
    "last_name": "Nom",
}


def load_column_aliases(df_key: str | None = None) -> dict[str, str]:
    """Merge built-in aliases with optional YAML/JSON override files.

    Looks for, in order:
      - CACHE_DIR/column_aliases.yaml  (or .yml / .json)
      - CSV_DIR/column_aliases.yaml
    File format (YAML or JSON)::

        data_vente:
          p_brut: "Prix brut HT"
        # OR a flat map applied to every dataset:
        p_brut: "Prix brut HT"
    """
    aliases = dict(_DEFAULT_COLUMN_ALIASES)
    search_dirs = [Path(CACHE_DIR), Path(CSV_DIR)]
    candidates = []
    for d in search_dirs:
        for name in ("column_aliases.yaml", "column_aliases.yml", "column_aliases.json"):
            p = d / name
            if p.is_file():
                candidates.append(p)

    for path in candidates:
        try:
            raw_text = path.read_text(encoding="utf-8")
            data = None
            if path.suffix.lower() == ".json":
                data = json.loads(raw_text)
            else:
                try:
                    import yaml  # optional dependency
                    data = yaml.safe_load(raw_text)
                except Exception:
                    # Minimal YAML-ish fallback: "key: value" lines only
                    data = {}
                    for line in raw_text.splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or ":" not in line:
                            continue
                        k, v = line.split(":", 1)
                        data[k.strip()] = v.strip().strip("\"'" )
            if not isinstance(data, dict):
                continue
            # Nested by df_key OR flat
            if df_key and isinstance(data.get(df_key), dict):
                aliases.update({str(k): str(v) for k, v in data[df_key].items()})
            else:
                # flat map, or merge every nested map
                for k, v in data.items():
                    if isinstance(v, dict):
                        if df_key is None or k == df_key:
                            aliases.update({str(kk): str(vv) for kk, vv in v.items()})
                    else:
                        aliases[str(k)] = str(v)
            logger.info(f"[INFO] Loaded column aliases from {path.name}")
        except Exception as e:
            logger.warning(f"[WARN] Could not load aliases from {path}: {e}")
    return aliases


def enrich_dataframe(df: pd.DataFrame, df_key: str) -> pd.DataFrame:
    """Normalise column names and attach alias metadata for analytics."""
    # Column names: strip + lower + spaces→underscores (matches analytics load).
    df = df.copy()
    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.lower()
        .str.replace(" ", "_", regex=False)
        .str.replace("-", "_", regex=False)
    )
    aliases = load_column_aliases(df_key)
    # Only keep aliases that match real columns (plus keep full map for schema).
    matched = {c: aliases[c] for c in df.columns if c in aliases}
    df.attrs["column_aliases"] = matched
    df.attrs["df_key"] = df_key
    return df



text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " ", ""],
)

_vectorstore = None


def get_vectorstore():
    global _vectorstore
    if _vectorstore is None:
        _vectorstore = Chroma(
            persist_directory=str(CHROMA_DIR),
            embedding_function=embedding_function,
        )
    return _vectorstore


_arabic_lower = 0x0600
_arabic_upper = 0x06FF

_ARABIC_DIACRITICS = re.compile(r"[\u064B-\u0652\u0670\u0640]")
_ALEF_VARIANTS = str.maketrans("أإآٱ", "اااا")
# NFKC keeps Arabic-Indic digits (٠-٩) as-is; map them to ASCII explicitly.
_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_PRESENTATION_FORMS = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")

_CHAPTER_PATTERN = re.compile(
    r"(الفصل|فصل|المادة|باب|chapitre|article|section)\s*(ال)?\s*(\d{1,3})",
    re.IGNORECASE,
)


def _is_arabic_char(c: str) -> bool:
    """Base Arabic block plus the presentation-form ranges that PDF
    extraction substitutes for shaped letters."""
    return (
        _arabic_lower <= ord(c) <= _arabic_upper
        or "\uFB50" <= c <= "\uFDFF"
        or "\uFE70" <= c <= "\uFEFF"
    )


def _arabic_ratio(line: str) -> float:
    """Share of non-whitespace characters that are Arabic."""
    chars = [c for c in line if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if _is_arabic_char(c)) / len(chars)


def has_arabic(text: str) -> bool:
    """True if the text contains any Arabic character (base or shaped)."""
    return any(_is_arabic_char(c) for c in text)


def normalize_arabic_query(query: str) -> str:
    """Normalise Arabic in a query to match indexed content.

    Strips diacritics and tatweel, unifies alef/hamza variants (أإآ → ا),
    ة → ه and ى → ي, converts Arabic-Indic digits (٠-٩) to ASCII and
    collapses whitespace. Non-Arabic text passes through essentially unchanged.
    """
    s = unicodedata.normalize("NFKC", query or "")
    s = _ARABIC_DIACRITICS.sub("", s)
    s = s.translate(_ALEF_VARIANTS)
    s = s.translate(_ARABIC_INDIC_DIGITS)
    s = s.replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"\s+", " ", s).strip()


def chapter_reference(query: str) -> str:
    """Canonical Arabic chapter/article reference of a query, e.g. «الفصل 9».

    Normalises glued forms like «الفصل9» to «الفصل 9» so the semantic search
    term is clean. Returns "" when the query is not Arabic or has no numbered
    chapter/article reference (French queries retrieve the French copy
    directly).
    """
    m = _CHAPTER_PATTERN.search(query or "")
    if not m or not has_arabic(query):
        return ""
    label, _article, num = m.groups()
    return f"{label} {num}"


def chapter_hint(query: str) -> str:
    """Return a French retrieval hint for a numbered chapter/article reference.

    E.g. «الفصل 9» produces "chapitre 9 article 9" so the French copy of the
    contract is retrieved alongside the Arabic one. Returns "" for queries
    without a chapter/article reference or already in French.
    """
    hits = _CHAPTER_PATTERN.findall(query or "")
    if not hits or not has_arabic(query):
        return ""
    parts = []
    for _label, _article, num in hits:
        parts.append(f"chapitre {num}")
        parts.append(f"article {num}")
    return " ".join(parts)


def _in_visual_order(line: str, tokens: list) -> bool:
    """Heuristic: is an Arabic line stored in reversed (visual) order?

    Shaped presentation forms (FB50-FDFF / FE70-FEFF) only survive PDF
    extraction of RTL text, so their presence is a strong signal. Otherwise
    check for a numeral token that appears before any Arabic token, which is
    how visual order prints «9 الفصل» for the logical «الفصل 9».
    """
    if _PRESENTATION_FORMS.search(line):
        return True
    for token in tokens:
        if token and token[0].isdigit():
            if not has_arabic(token):
                return True
        if has_arabic(token):
            return False
    return False


def fix_arabic_text(text: str, threshold: float = 0.40) -> str:
    """Rebuild logical-order Arabic from reversed visual-order PDF extraction.

    PyPDF emits Arabic in display (visual) order: words appear left-to-right
    in reverse logical order and often in presentation (shaped) forms, e.g.
    ``العقد على تسبيقات. 9 الفصل`` for «الفصل 9. تسبيقات على العقد». Reversing
    the token order and NFKC-normalising the shaped forms restores readable
    logical text that embeds correctly. Lines already in logical order and
    non-Arabic lines pass through untouched; Arabic-Indic digits are mapped to
    ASCII so chapter references match query-side normalisation.
    """
    if not text:
        return text
    lines = []
    for line in text.splitlines():
        if _arabic_ratio(line) < threshold:
            lines.append(line)
            continue
        tokens = line.split()
        if _in_visual_order(line, tokens):
            source = " ".join(reversed(tokens))
        else:
            source = line
        normalized = unicodedata.normalize("NFKC", source)
        normalized = normalized.translate(_ARABIC_INDIC_DIGITS)
        lines.append(normalized)
    return "\n".join(lines)


def load_pdf(file_path):
    docs = PyPDFLoader(str(file_path)).load()
    for doc in docs:
        doc.page_content = fix_arabic_text(doc.page_content)
    return docs


def load_docx(file_path):
    return Docx2txtLoader(str(file_path)).load()


def load_txt(file_path):
    try:
        return TextLoader(str(file_path), encoding="utf-8").load()
    except (UnicodeDecodeError, RuntimeError):
        return TextLoader(str(file_path), encoding="latin-1").load()


def load_document(file_path):
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return load_pdf(file_path)
    if suffix == ".docx":
        return load_docx(file_path)
    if suffix == ".txt":
        return load_txt(file_path)
    return []

#CHUNK DOCUMENTS
def chunk_documents(documents):
    chunks = text_splitter.split_documents(documents)
    cleaned = []
    for chunk in chunks:
        content = (chunk.page_content or "").strip()
        if len(content) < MIN_CHUNK_CHARS:
            continue
        chunk.page_content = content
        src = chunk.metadata.get("source", "")
        if src:
            chunk.metadata["source"] = Path(src).name
        cleaned.append(chunk)
    return cleaned


def _deterministic_ids(chunks):
    ids = []
    seen = {}
    for chunk in chunks:
        source = chunk.metadata.get("source", "unknown")
        idx = seen.get(source, 0)
        seen[source] = idx + 1
        # Stable id per (source, chunk-index). We deliberately EXCLUDE
        # chunk.page_content from the digest: including it made the id change
        # whenever a document was edited, so re-ingestion produced brand-new
        # ids and the stale chunks were never overwritten -- the vector store
        # accumulated duplicates on every re-ingest. Keying on (source, idx)
        # makes re-ingestion overwrite the same slots (delete + add).
        digest = hashlib.sha1(
            f"{source}:{idx}".encode("utf-8")
        ).hexdigest()
        ids.append(digest)
    return ids

def ingest_documents():
    vectorstore = get_vectorstore()
    documents = []

    for folder in [PDF_DIR, DOCX_DIR, TXT_DIR]:
        for file_path in Path(folder).glob("*"):
            if not file_path.is_file():
                continue
            logger.info(f"[INFO] Loading: {file_path.name}")
            try:
                docs = load_document(file_path)
            except Exception as e:
                # One unreadable / corrupt file must not abort the whole run.
                logger.error(f"[ERROR] Skipping {file_path.name}: {e}")
                continue
            documents.extend(docs)

    if not documents:
        logger.info("[INFO] No documents found.")
        return

    chunks = chunk_documents(documents)
    if not chunks:
        logger.info("[INFO] No non-empty chunks to index.")
        return

    ids = _deterministic_ids(chunks)
    logger.info(f"[INFO] Generated {len(chunks)} chunks")
    # Delete existing entries with the same stable ids first so re-ingestion
    # does not raise DuplicateIDError.  Chroma's delete is idempotent.
    vectorstore.delete(ids=ids)
    vectorstore.add_documents(chunks, ids=ids)
    logger.info("[INFO] Documents indexed.")


def dataframe_summary(df, df_key: str = ""):
    """Schema-only summary of a structured dataset.

    We deliberately do NOT embed full row values here (that let RAG answer
    analytical questions from a 5-row sample). We DO include:
      - normalised df_key
      - dtype per column
      - human-readable alias when known (p_brut → "Prix brut")
      - up to 2 short sample values for grounding
    Real figures must still come from the analytics (pandas) path.
    """
    aliases = df.attrs.get("column_aliases") or load_column_aliases(df_key)
    columns = []
    for col in df.columns:
        samples = df[col].dropna().unique()[:2].tolist()
        samples = [
            (str(s)[:40] + "…") if len(str(s)) > 40 else str(s)
            for s in samples
        ]
        entry = {
            "name": str(col),
            "dtype": str(df[col].dtype),
            "samples": samples,
        }
        if col in aliases:
            entry["label"] = aliases[col]
        columns.append(entry)
    return {
        "summary": {
            "dataset_key": df_key or df.attrs.get("df_key", ""),
            "rows": int(len(df)),
            "columns": columns,
        }
    }

def _detect_separator(file_path):

    candidates = (";", ",", "\t", "|")
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = []
            for raw in f:
                if raw.strip():
                    lines.append(raw)
                if len(lines) >= 20:
                    break
    except OSError:
        return ","
    if not lines:
        return ","

    best_sep, best_key = ",", (-1, -1.0)
    for sep in candidates:
        counts = [len(line.split(sep)) for line in lines]
        header_fields = counts[0]
        if header_fields < 2:
            continue
        consistency = sum(1 for c in counts if c == header_fields) / len(counts)
        # Prefer a delimiter that is consistent first, then yields more columns.
        key = (1 if consistency >= 0.6 else 0, header_fields * consistency)
        if key > best_key:
            best_key, best_sep = key, sep
    return best_sep


def load_csv(file_path):
    sep = _detect_separator(file_path)

    read_kwargs = dict(
        low_memory=False,
        na_values=["NULL", "null", ""],
        on_bad_lines="warn",      # warn (not silently skip) so data loss is visible
    )

    def _read(separator):
        try:
            return pd.read_csv(file_path, encoding="utf-8", sep=separator, **read_kwargs)
        except UnicodeDecodeError:
            return pd.read_csv(file_path, encoding="latin-1", sep=separator, **read_kwargs)

    df = _read(sep)
    if df.shape[1] == 1:
        only_col = str(df.columns[0])
        for alt in (";", "\t", "|", ","):
            if alt != sep and alt in only_col:
                alt_df = _read(alt)
                if alt_df.shape[1] > df.shape[1]:
                    df, sep = alt_df, alt
                    break

    logger.info(f"[INFO] {Path(file_path).name}: delimiter={sep!r} -> {df.shape[1]} columns")

    # Clean trailing commas from column names.
    df.columns = df.columns.astype(str).str.rstrip(",").str.strip()

    for col in df.select_dtypes(include="string").columns:
        df[col] = df[col].apply(lambda x: x.rstrip(",").strip() if isinstance(x, str) else x)
        df[col] = df[col].replace({"NULL": pd.NA, "null": pd.NA, "": pd.NA})
        converted = pd.to_numeric(df[col], errors="coerce")
        non_null = df[col].notna().sum()
        if non_null == 0:
            continue
        if converted.notna().sum() == non_null:
            df[col] = converted
            continue
        # European number format fallback: thousands '.' + decimal ',' e.g.
        # "1.234,56" or "1234,56". Only applied when the WHOLE column parses
        # cleanly this way, so US-style decimals are never corrupted.
        euro = pd.to_numeric(
            df[col].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
            errors="coerce",
        )
        if euro.notna().sum() == non_null:
            df[col] = euro

    return df


def load_excel(file_path):
    return pd.read_excel(file_path)


def ingest_structured_files():
    vectorstore = get_vectorstore()
    cache_dir = Path(CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)

    documents = []
    ids = []

    for file_path in Path(CSV_DIR).glob("*"):
        if not file_path.is_file():
            continue
        suffix = file_path.suffix.lower()
        try:
            if suffix == ".csv":
                df = load_csv(file_path)
            elif suffix in [".xlsx", ".xls"]:
                df = load_excel(file_path)
            else:
                continue

            # Normalised key (snake_case) — this is the ONLY name analytics sees.
            df_key = normalize_df_key(file_path.stem)
            df = enrich_dataframe(df, df_key)

            # Cache under the normalised key so load_dataframes never sees spaces.
            cache_file = cache_dir / f"{df_key}.pkl"
            df.to_pickle(cache_file)
            # Also write a tiny sidecar with the original filename for traceability.
            meta_file = cache_dir / f"{df_key}.meta.json"
            meta_file.write_text(
                json.dumps(
                    {
                        "df_key": df_key,
                        "source_file": file_path.name,
                        "rows": int(len(df)),
                        "columns": list(map(str, df.columns)),
                        "aliases": df.attrs.get("column_aliases", {}),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            summary = dataframe_summary(df, df_key=df_key)
            text = json.dumps(summary, indent=2, ensure_ascii=False, default=str)

            documents.append(
                Document(
                    page_content=text,
                    metadata={
                        "source": file_path.name,
                        "dataset_key": df_key,
                        "type": "structured",
                        "cache": str(cache_file),
                    },
                )
            )
            # Stable id per structured source for idempotent re-ingestion.
            ids.append(
                hashlib.sha1(f"structured:{file_path.name}".encode("utf-8")).hexdigest()
            )
            logger.info(
                f"[INFO] Loaded {file_path.name} → key={df_key!r} "
                f"({len(df):,} rows × {len(df.columns)} cols)"
            )

        except Exception as e:
            logger.error(f"[ERROR] {file_path.name}: {e}")

    if documents:
        vectorstore.delete(ids=ids)
        vectorstore.add_documents(documents, ids=ids)
        logger.info("[INFO] Structured datasets indexed.")


def ingest_all():
    logger.info("\n[INFO] Starting ingestion\n")
    ingest_documents()
    ingest_structured_files()
    logger.info("\n[INFO] Ingestion complete.\n")


def get_collection_count():
    vectorstore = get_vectorstore()
    try:
        return vectorstore._collection.count()
    except Exception:
        # Public-API fallback if the private attribute changes.
        return len(vectorstore.get().get("ids", []))

# ==========================================================
# TEST
# ==========================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ingest_all()
    logger.info("")
    logger.info("Total Chunks:")
    logger.info(get_collection_count())
