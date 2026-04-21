import os
import sys
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

def render(html_path, out_path):
    html_file = Path(html_path).resolve()
    if not html_file.exists():
        raise FileNotFoundError(f"HTML source not found: {html_file}")
    if html_file.stat().st_size == 0:
        raise RuntimeError(f"HTML source is empty: {html_file}")

    out_file = Path(out_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_pdf_path = tempfile.mkstemp(prefix="pdf_tmp_", suffix=".pdf", dir=str(out_file.parent))
    os.close(fd)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        try:
            page = browser.new_page()
            page.goto(html_file.as_uri(), wait_until="networkidle")
            page.emulate_media(media="screen")
            page.pdf(
                path=tmp_pdf_path,
                format="A4",
                print_background=True,
                margin={"top": "2.3cm", "bottom": "1.8cm", "left": "0cm", "right": "0cm"}
            )
        finally:
            browser.close()

    final_size = os.path.getsize(tmp_pdf_path) if os.path.exists(tmp_pdf_path) else 0
    if final_size < 1024:
        try:
            if os.path.exists(tmp_pdf_path):
                os.remove(tmp_pdf_path)
        finally:
            raise RuntimeError(f"Rendered PDF is unexpectedly small ({final_size} bytes)")
    os.replace(tmp_pdf_path, str(out_file))

if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(1)
    render(sys.argv[1], sys.argv[2])
