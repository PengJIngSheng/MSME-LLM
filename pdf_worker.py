import sys
from playwright.sync_api import sync_playwright

def render(html_path, out_path):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        page = browser.new_page()
        page.goto(f"file://{html_path}", wait_until="domcontentloaded")
        page.pdf(
            path=out_path,
            format="A4",
            print_background=True,
            margin={"top": "2.3cm", "bottom": "1.8cm", "left": "0cm", "right": "0cm"}
        )
        browser.close()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(1)
    render(sys.argv[1], sys.argv[2])
