"""Check a complete ZIP directory before any member is decompressed."""
import zipfile

from .protocol import require

# These are the methods produced by the supported Godot and XLSX exporters.
# In particular, do not reach the BZIP2/LZMA readers on older Python runtimes
# affected by CVE-2026-15310, even if a member advertises a small decoded size.
SUPPORTED_COMPRESSION = frozenset((zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED))


def check_budget(entries, *, max_files, max_expanded, expanded_code='expanded_limit', max_ratio=None):
    require(0 < len(entries) <= max_files, 'file_count')
    total = 0
    for item in entries:
        require(item.compress_type in SUPPORTED_COMPRESSION, 'compression_unsupported', 415)
        total += item.file_size
        require(0 <= item.file_size <= max_expanded and total <= max_expanded, expanded_code, 413)
        if max_ratio is not None:
            require(item.file_size / max(item.compress_size, 1) <= max_ratio, 'compression_ratio')
