"""Isolated, bounded text-PDF extraction. Image-only pages require manual OCR."""

import io
import json
import sys


def main():
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    except ImportError:  # Windows: subprocess timeout still applies.
        pass
    from pypdf import PdfReader

    data = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("pdf_too_large")
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 30:
        raise ValueError("unsupported_pdf")
    pages = []
    total = 0
    for number, page in enumerate(reader.pages, 1):
        contents = page.get_contents()
        if contents is not None and len(contents.get_data()) > 5 * 1024 * 1024:
            raise ValueError("pdf_stream_too_large")
        text = (page.extract_text() or "").strip()
        total += len(text)
        if total > 60000 or len(text) < 20:
            raise ValueError("pdf_requires_manual_extraction")
        pages.append({"page": number, "text": text})
    sys.stdout.write(json.dumps(pages))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.stderr.write("pdf_extraction_failed")
        sys.exit(1)
