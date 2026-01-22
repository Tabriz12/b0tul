import json
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from config import settings

URL = "https://djinni.co/my/dashboard/#/"

EMAIL = settings.get("DJINNI_EMAIL")


PASSWORD = settings.get("DJINNI_PASSWORD")

if not PASSWORD:
    raise ValueError("DJINNI_PASSWORD is not set in settings.")
PROCESSED_FILE = Path("processed_jobs.json")


class DjinniParser:
    """A parser to interact with Djinni.co using Playwright."""

    def __init__(self, email: str, password: str) -> None:
        self.email = email
        self.password = password
        self.processed_ids = self._load_processed_ids()

    def _load_processed_ids(self) -> set[str]:
        if PROCESSED_FILE.exists():
            return set(json.loads(PROCESSED_FILE.read_text()))
        return set()

    def _save_processed_ids(self) -> None:
        PROCESSED_FILE.write_text(json.dumps(sorted(self.processed_ids), indent=2))

    def login(self, page: Page) -> None:
        """Login to Djinni.co with provided credentials."""
        page.goto("https://djinni.co/login", timeout=60000)

        page.wait_for_selector("input[name='email']")
        page.fill("input[name='email']", self.email)
        page.fill("input[name='password']", self.password)

        page.click("button[type='submit']")

        page.wait_for_selector("a[href='/my/inbox/']", timeout=60000)

    def check_job_board(self, page: Page) -> None:
        page.wait_for_selector("[id^='job-item-']", timeout=60000)

        job_ids = page.locator("[id^='job-item-']").evaluate_all(
            "els => els.map(e => e.id.replace('job-item-', ''))"
        )

        print(f"{len(job_ids)} job items found.")

        for job_id in job_ids:
            if job_id in self.processed_ids:
                print(f"Skipping already processed job {job_id}")
                continue

            print(f"Processing job {job_id}")

            job_page = self.context.new_page()
            job_page.goto(page.url)

            self.process_single_job(job_page, job_id)

            print(f"Finished processing job {job_id}")

            job_page.close()

            # TODO: extract message / details here

            # mark as processed
            self.processed_ids.add(job_id)
            self._save_processed_ids()

    def process_single_job(self, page: Page, job_id: str) -> None:
        job_container = page.locator(f"#job-item-{job_id}")

        title_link = job_container.locator("a.job-item__title-link")

        # click to open job details
        title_link.click()

        description_selector = "div.job-post__description"
        page.wait_for_selector(description_selector, timeout=60000)

        description = page.locator(description_selector).inner_text().strip()

        print(f"\n--- Job {job_id} description ---\n")
        print(description[:50])  # preview
        print("\n------------------------------\n")

        # TODO: save description somewhere
        # self.save_job_description(job_id, description)

        apply_button = page.locator("button.js-inbox-toggle-reply-form").first

        if apply_button.is_visible():
            print(f"Apply button found for job {job_id}")

            # apply_button.click()
            # page.wait_for_timeout(2000)
        else:
            print(f"No apply button for job {job_id}")

    def go_to_dashboard(self) -> None:
        with sync_playwright() as p:
            self.browser = p.chromium.launch(headless=False)
            self.context = self.browser.new_context(locale="en-US")

            page = self.context.new_page()

            self.login(page)

            page.goto(URL, timeout=60000)

            page.wait_for_selector("body", timeout=60000)
            self.check_job_board(page)
            page.wait_for_timeout(5 * 1000)

            self.browser.close()


parser = DjinniParser(EMAIL, PASSWORD)
parser.go_to_dashboard()
