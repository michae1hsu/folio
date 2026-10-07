[Folio](../README.md) / [Documentation](README.md)

# Supported formats and limits

Check source compatibility and size limits before preparing an import.

| Category | Supported extensions |
| --- | --- |
| Documents | PDF; DOC/DOCX, PPT/PPTX, XLS/XLSX, ODT/ODS/ODP, RTF |
| Images | PNG, JPG/JPEG, TIFF/TIF, BMP, WEBP, GIF |
| Text/code | TXT, MD, CSV, TSV, JSON, XML, YAML/YML, LOG, PY, JS, TS, HTML/HTM, CSS, SQL |
| Audio | MP3, WAV, M4A, FLAC, AAC, OGG, OPUS, WMA, MPGA |
| Video audio tracks | MP4, MOV, MKV, AVI, WEBM, MPEG/MPG, M4V, WMV |

- ZIP upload: **512 MiB** maximum compressed size; **2 GiB** expanded total; **500** source files; **512 MiB** per source file; at most **1,000** ZIP entries including directories.
- ZIP supports stored and deflated members. Encrypted entries, symlinks, special files, unsafe paths, duplicate normalized names, corrupt data, and unsupported compression methods are rejected. Ordinary OS metadata is listed as ignored. Nested archives and unsupported file types are retained as explicit unsupported entries; they are not recursively unpacked or silently treated as documents.
- Converted PDF must be smaller than **50,000,000 bytes** and at most **2,000 pages** locally. Model context/output limits may be lower.
- Compressed complete audio must be smaller than **25,000,000 bytes**. It is not split or truncated to fit.
- Cloud Markdown/Agent transfer limits may be lower than the source import limit; Markdown must be smaller than **50 MiB**. An import can succeed while a later provider transfer fails its own limit.
- Office layout, formulas, fonts, embedded objects, animation, hidden content, and image details may require manual verification. A media report is not a verbatim transcript.
