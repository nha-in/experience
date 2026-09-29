"""Choosing and storing the evidence files of a legacy exit."""

import re
from dataclasses import dataclass

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from . import clean
from .source import INLINE_FILES

#: `FileType` in legacy's enums. Its host file is a column on the exit row, not a
#: document, so it has no place here.
WASA = 1
SUPPORTING = 3
TESTING_CERTIFICATE = 4
TESTING_REPORT = 5
UNDERTAKING = 6
#: What the portal's own supporting evidence field takes, and Django's default
#: length for the column a file name is stored in.
MAX_SUPPORTING = 8
NAME_LIMIT = 100
LEGACY_FOLDER = "experience-attachments/legacy"


@dataclass(frozen=True)
class FileSource:
    table: str
    row_id: int
    column: str
    original_name: str
    created_at: object
    supporting_type: str = ""


@dataclass(frozen=True)
class StoredFile:
    name: str
    original_name: str
    size: int
    content_type: str


def _newest_first(documents):
    return sorted(
        documents,
        key=lambda row: (clean.when(row["created_at"]), row["id"]),
        reverse=True,
    )


def _distinct(documents):
    seen, kept = set(), []
    for row in _newest_first(documents):
        if row["digest"] in seen:
            continue
        seen.add(row["digest"])
        kept.append(row)
    return kept


def _document(row):
    return FileSource(
        table="sd_exit_docs",
        row_id=row["id"],
        column="files",
        original_name=row["file_name"]
        or f"document-{row['id']}.{(row['file_ext'] or 'pdf').lower()}",
        created_at=row["created_at"],
        supporting_type=clean.text(row["supporting_doc_type"]).upper(),
    )


def _inline(exit_row, column, label):
    extension = clean.text(exit_row.get(INLINE_FILES[column])).lower() or "pdf"
    return FileSource(
        table=exit_row["source"],
        row_id=exit_row["id"],
        column=column,
        original_name=f"{label}.{extension}",
        created_at=exit_row["created_date"] or exit_row["created_at"],
        supporting_type=clean.text(exit_row.get("suporting_doc_name")).upper()
        if column == "suporting_doc"
        else "",
    )


#: How a file name says which milestone it is for.
_PHR_NAME = re.compile(r"(?<![a-z])phr")
_NAMED = {
    "m1": re.compile(r"(?<![a-z0-9])(?:m|milestone)\s*-?\s*1(?![0-9])"),
    "m2": re.compile(r"(?<![a-z0-9])(?:m|milestone)\s*-?\s*2(?![0-9])"),
    "m3": re.compile(r"(?<![a-z0-9])(?:m|milestone)\s*-?\s*3(?![0-9])"),
    "m4": re.compile(r"(?<![a-z0-9])(?:m|milestone)\s*-?\s*4(?![0-9])"),
    "p1": _PHR_NAME,
    "p2": _PHR_NAME,
    "p3": _PHR_NAME,
    "p4": re.compile(r"locker"),
    "nhcx1": re.compile(r"(?<![a-z])n?hcx"),
    "uhi1": re.compile(r"(?<![a-z])uhi"),
}
_M_KEYS = {"m1", "m2", "m3", "m4"}


def names_milestone(file_name, key):
    """Whether a file's name says it is for this milestone, and no other M one."""
    name = (file_name or "").lower()
    named = {other for other, pattern in _NAMED.items() if pattern.search(name)}
    return key in named and len(named & _M_KEYS) <= 1


def _single_evidence(by_type, exit_row, key, doc_type, inline=None):
    """The one document a field shows, or the file the exit row carried inline.

    An exit claiming several milestones often carried a file for each, so the
    one named for this milestone wins over the newest.
    """
    rows = by_type.get(doc_type, [])
    if rows:
        own = [row for row in rows if names_milestone(row["file_name"], key)]
        return [_document((own or rows)[0])]
    if inline and exit_row.get(inline[0]):
        return [_inline(exit_row, inline[1], inline[2])]
    return []


#: field name, legacy doc type, and the exit row's own column as a fallback.
SINGLE_EVIDENCE = (
    ("wasa_certificate", WASA, ("has_wasa_file", "wasa_file", "wasa-certificate")),
    ("functional_certificate", TESTING_CERTIFICATE),
    (
        "functional_report",
        TESTING_REPORT,
        ("has_function_testing_file", "function_testing_file", "functional-testing"),
    ),
    ("undertaking_form", UNDERTAKING),
)


def exit_evidence(exit_row, documents, key):
    """The files each evidence field shows for one exit and milestone."""
    by_type = {}
    for row in _distinct(documents):
        by_type.setdefault(row["doc_type_id"], []).append(row)
    fields = {}
    for name, *lookup in SINGLE_EVIDENCE:
        found = _single_evidence(by_type, exit_row, key, *lookup)
        if found:
            fields[name] = found
    supporting = [_document(row) for row in _newest_first(by_type.get(SUPPORTING, []))]
    if exit_row.get("has_suporting_doc"):
        supporting.append(_inline(exit_row, "suporting_doc", "supporting-document"))
    if exit_row.get("has_host_file"):
        supporting.append(_inline(exit_row, "host_file", "hosting-certificate"))
    if supporting:
        fields["supporting_evidence"] = supporting[:MAX_SUPPORTING]
    return fields


def registration_certificate(row):
    """The certificate the old portal took with the registration itself."""
    return FileSource(
        table="sd_login",
        row_id=row["sd_id"],
        column="certificate",
        original_name="registration-certificate.pdf",
        created_at=row["created_at"],
    )


def verification_document(exits, documents_by_exit, preferred_type, certificates=()):
    """A GST certificate from an exit, else the registration's own, else any."""
    candidates = []
    for exit_row in exits:
        candidates.extend(
            _document(row)
            for row in documents_by_exit.get(exit_row["id"], [])
            if row["doc_type_id"] == SUPPORTING
        )
        if exit_row.get("has_suporting_doc"):
            candidates.append(_inline(exit_row, "suporting_doc", "supporting-document"))
    candidates.sort(key=lambda source: clean.when(source.created_at), reverse=True)
    matching = [
        source for source in candidates if source.supporting_type == preferred_type
    ]
    if preferred_type and matching:
        return matching[0]
    if certificates:
        return max(certificates, key=lambda source: clean.when(source.created_at))
    return (matching or candidates or [None])[0]


def wasa_certificates(exits, documents_by_exit):
    """Every WASA certificate an account's exits carried, newest first."""
    candidates = []
    for exit_row in exits:
        candidates.extend(
            _document(row)
            for row in documents_by_exit.get(exit_row["id"], [])
            if row["doc_type_id"] == WASA
        )
        if exit_row.get("has_wasa_file"):
            candidates.append(_inline(exit_row, "wasa_file", "wasa-certificate"))
    candidates.sort(key=lambda source: clean.when(source.created_at), reverse=True)
    return candidates


class FileStore:
    def __init__(self, legacy, *, enabled):
        self.legacy = legacy
        self.enabled = enabled
        self.stored = {}
        self.bytes_written = 0

    def store(self, source):
        key = (source.table, source.row_id, source.column)
        if key in self.stored:
            return self.stored[key]
        if source.table == "sd_exit_docs":
            content = self.legacy.document_bytes(source.row_id)
        elif source.table == "sd_login":
            content = self.legacy.certificate_bytes(source.row_id)
        else:
            content = self.legacy.inline_bytes(
                source.table,
                source.row_id,
                source.column,
            )
        if not content:
            self.stored[key] = None
            return None
        content_type, extension = clean.sniff_content_type(content[:8])
        name = self._name(source, extension)
        saved = default_storage.save(name, ContentFile(content))
        stored = StoredFile(
            name=saved,
            original_name=source.original_name[:255],
            size=len(content),
            content_type=content_type,
        )
        self.bytes_written += len(content)
        self.stored[key] = stored
        return stored

    @staticmethod
    def _name(source, extension):
        when = source.created_at
        folder = f"{LEGACY_FOLDER}/{when:%Y/%m}" if when else f"{LEGACY_FOLDER}/undated"
        stem = (
            re.sub(r"[^A-Za-z0-9]+", "-", source.original_name.rsplit(".", 1)[0])
            .strip("-")
            .lower()
            or "file"
        )
        prefix = f"{folder}/{source.table.replace('_', '')}-{source.row_id}-"
        room = NAME_LIMIT - len(prefix) - len(extension) - 1 - 8
        return f"{prefix}{stem[: max(room, 1)]}.{extension}"
