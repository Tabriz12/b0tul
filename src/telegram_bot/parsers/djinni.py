import asyncio
import json
from pathlib import Path

from playwright.async_api import Page, async_playwright

from config import settings
from telegram_bot.helpers.logger import setup_logger

EMAIL = settings.get("DJINNI_EMAIL")
PASSWORD = settings.get("DJINNI_PASSWORD")
PROCESSED_FILE = Path("processed_jobs.json")
HEADLESS = settings.get(
    "PLAYWRIGHT_HEADLESS", True
)  # Set DYNACONF_PLAYWRIGHT_HEADLESS=false for UI debugging

logger = setup_logger("DjinniParser")


class DjinniParser:
    """A parser to interact with Djinni.co using Playwright."""

    def __init__(self, email: str, password: str) -> None:
        if not email or not password:
            raise ValueError("DJINNI_EMAIL or DJINNI_PASSWORD is not set in settings.")
        self.email = email
        self.password = password
        self.processed_ids = self._load_processed_ids()
        self.dashboard_url = "https://djinni.co/my/dashboard/"
        # self.llm_client = llm_client

    def _load_processed_ids(self) -> set[str]:
        if PROCESSED_FILE.exists():
            raw = PROCESSED_FILE.read_text().strip()
            if not raw:
                return set()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    "processed_jobs.json is invalid JSON; ignoring its contents."
                )
                return set()
            if isinstance(data, list):
                return set(data)
            logger.warning(
                "processed_jobs.json does not contain a list; ignoring its contents."
            )
            return set()
        return set()

    def _save_processed_ids(self) -> None:
        PROCESSED_FILE.write_text(json.dumps(sorted(self.processed_ids), indent=2))

    async def login(self, page: Page) -> None:
        """Login to Djinni.co with provided credentials."""
        await page.goto("https://djinni.co/login", timeout=60000)
        await page.wait_for_selector("input[name='email']")

        await page.fill("input[name='email']", self.email)
        await page.fill("input[name='password']", self.password)
        await page.click("button[type='submit']")

        await page.wait_for_selector("a[href='/my/inbox/']", timeout=60000)

    async def get_job_ids(self, page: Page, page_num: int = 1) -> list[str]:
        await page.goto(f"{self.dashboard_url}?page={page_num}", timeout=60000)
        await page.wait_for_selector("[id^='job-item-']", timeout=60000)

        return await page.locator("[id^='job-item-']").evaluate_all(
            "els => els.map(e => e.id.replace('job-item-', ''))"
        )

    async def open_job(self, page: Page, job_id: str) -> str:
        logger.info(f"Opening job {job_id}...")
        job_container = page.locator(f"#job-item-{job_id}")
        logger.info(f"Waiting for job {job_id} container to be visible...")

        title_link = job_container.locator("a.job_item__header-link")
        logger.info(f"Waiting for job {job_id} title link to be visible...")

        await title_link.click()

        logger.info(f"Waiting for job {job_id} description to load...")

        await page.wait_for_selector("div.job-post__description", timeout=60000)

        description = (
            await page.locator("div.job-post__description").inner_text()
        ).strip()

        logger.info(f"\n--- Job {job_id} description ---\n")
        logger.info(description[:50])  # preview
        logger.info("\n------------------------------\n")

        # TODO: save description somewhere
        # self.save_job_description(job_id, description)

        return description

    async def prepare_to_apply(self, page: Page, job_id: str) -> str | None:
        apply_toggle = page.locator("button.js-inbox-toggle-reply-form")

        if not await apply_toggle.is_visible():
            print(f"No apply button for job {job_id}")

            return None

        await apply_toggle.click()

        textarea = page.locator("textarea#message")
        await textarea.wait_for(timeout=5000)

        draft = """Hello,
        I'm interested in this role and believe my background is a good fit.
        Looking forward to discussing further.
        """

        return draft

    async def submit_application(self, page: Page, message: str) -> None:
        apply_button = page.locator("button#job_apply").first

        motivation_field = page.locator("textarea#message").first

        if motivation_field.is_visible():
            await motivation_field.fill(message)

        if apply_button.is_visible():
            print("Submitting application...")
            # await apply_button.click()

        # wait for success signal (example)
        await page.wait_for_timeout(5_000)

    async def _collect_questions(self, page: Page) -> list[dict[str, object]]:
        script = """
        () => {
          const questions = [];

          const textSelectors = [
            "textarea",
            "input[type='text']",
            "input[type='email']",
            "input[type='tel']",
          ];

          document.querySelectorAll(textSelectors.join(",")).forEach((el, idx) => {
            if (el.id === "message") return;
            const label = el.id ? document.querySelector(`label[for="${el.id}"]`) : null;
            const text =
              (label && label.innerText.trim()) ||
              el.getAttribute("aria-label") ||
              el.getAttribute("placeholder") ||
              el.name ||
              el.id;
            if (!text) return;
            questions.push({
              type: "text",
              name: el.name || el.id || `field-${idx}`,
              text,
            });
          });

          const radios = Array.from(document.querySelectorAll("input[type='radio']"));
          const groups = new Map();
          radios.forEach((radio) => {
            const name = radio.name || radio.id || "radio";
            if (!groups.has(name)) groups.set(name, []);
            groups.get(name).push(radio);
          });

          groups.forEach((group, name) => {
            const first = group[0];
            const fieldset = first.closest("fieldset");
            const legend = fieldset ? fieldset.querySelector("legend") : null;
            let text = legend ? legend.innerText.trim() : "";
            if (!text) {
              const label = first.id
                ? document.querySelector(`label[for="${first.id}"]`)
                : null;
              text = label ? label.innerText.trim() : name;
            }

            const options = group.map((radio) => {
              const label = radio.id
                ? document.querySelector(`label[for="${radio.id}"]`)
                : null;
              return (
                (label && label.innerText.trim()) ||
                radio.value ||
                radio.getAttribute("aria-label") ||
                radio.id
              );
            });

            questions.push({
              type: "radio",
              name,
              text,
              options,
            });
          });

          return questions;
        }
        """  # noqa: E501
        return await page.evaluate(script)

    async def _fill_text_field(self, page: Page, name: str, value: str) -> None:
        script = """
        ({ name, value }) => {
          const selector = [
            `textarea[name="${name}"]`,
            `input[name="${name}"]`,
            `textarea#${name}`,
            `input#${name}`,
          ].join(",");
          const el = document.querySelector(selector);
          if (!el) return false;
          el.focus();
          el.value = value;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          return true;
        }
        """
        await page.evaluate(script, {"name": name, "value": value})

    async def _select_radio(self, page: Page, name: str, value: str) -> None:
        script = """
        ({ name, value }) => {
          const radios = Array.from(document.querySelectorAll(`input[type="radio"][name="${name}"]`));
          if (!radios.length) return false;
          const lower = value.toLowerCase();
          for (const radio of radios) {
            const label = radio.id ? document.querySelector(`label[for="${radio.id}"]`) : null;
            const labelText = label ? label.innerText.trim().toLowerCase() : "";
            const radioValue = (radio.value || "").toLowerCase();
            if (labelText.includes(lower) || radioValue.includes(lower)) {
              radio.click();
              return true;
            }
          }
          return false;
        }
        """  # noqa: E501
        await page.evaluate(script, {"name": name, "value": value})

    async def prepare_application(
        self, page: Page, job_id: str, message: str, answers: dict[str, str]
    ) -> list[dict[str, object]]:
        apply_toggle = page.locator("button.js-inbox-toggle-reply-form")

        if not await apply_toggle.is_visible():
            return []

        await apply_toggle.click()

        textarea = page.locator("textarea#message")
        await textarea.wait_for(timeout=5000)
        await textarea.fill(message)

        questions = await self._collect_questions(page)
        unanswered: list[dict[str, object]] = []

        for question in questions:
            q_text = str(question.get("text", "")).strip()
            q_type = str(question.get("type", ""))
            q_name = str(question.get("name", "")).strip()
            if not q_text or not q_name:
                continue

            normalized = " ".join(q_text.lower().split())
            answer = answers.get(normalized)
            if not answer:
                unanswered.append(question)
                continue

            if q_type == "text":
                await self._fill_text_field(page, q_name, answer)
            elif q_type == "radio":
                await self._select_radio(page, q_name, answer)
            else:
                unanswered.append(question)

        return unanswered

    async def collect_jobs(
        self, page_num: int = 1, limit: int = 3
    ) -> list[dict[str, str | int]]:
        async with async_playwright() as playwright:
            logger.info("Launching browser for job collection...")
            browser = await playwright.chromium.launch(headless=HEADLESS)
            context = await browser.new_context(locale="en-US")
            page = await context.new_page()

            await self.login(page)
            job_ids = await self.get_job_ids(page, page_num=page_num)
            logger.info(f"Found job IDs on page {page_num}: {job_ids}")

            jobs: list[dict[str, str | int]] = []
            for job_id in job_ids[:limit]:
                description = await self.open_job(page, job_id)
                jobs.append(
                    {
                        "job_id": job_id,
                        "description": description,
                        "page_num": page_num,
                    }
                )

                await page.goto(f"{self.dashboard_url}?page={page_num}", timeout=60000)

            await context.close()
            await browser.close()

            return jobs

    async def prepare_job_application(
        self,
        job_id: str,
        message: str,
        answers: dict[str, str],
        page_num: int = 1,
    ) -> list[dict[str, object]]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=HEADLESS)
            context = await browser.new_context(locale="en-US")
            page = await context.new_page()

            await self.login(page)
            await page.goto(f"{self.dashboard_url}?page={page_num}", timeout=60000)
            await page.wait_for_selector(f"#job-item-{job_id}", timeout=60000)

            await self.open_job(page, job_id)
            unanswered = await self.prepare_application(page, job_id, message, answers)

            await context.close()
            await browser.close()
            return unanswered

    async def apply_to_job(
        self,
        job_id: str,
        message: str,
        answers: dict[str, str],
        page_num: int = 1,
    ) -> list[dict[str, object]]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=HEADLESS)
            context = await browser.new_context(locale="en-US")
            page = await context.new_page()

            await self.login(page)
            await page.goto(f"{self.dashboard_url}?page={page_num}", timeout=60000)
            await page.wait_for_selector(f"#job-item-{job_id}", timeout=60000)

            await self.open_job(page, job_id)
            unanswered = await self.prepare_application(page, job_id, message, answers)
            if unanswered:
                await context.close()
                await browser.close()
                return unanswered

            await self.submit_application(page, message)

            await context.close()
            await browser.close()
            return []

    def go_to_dashboard(self) -> None:
        async def _run() -> None:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=HEADLESS)
                context = await browser.new_context(locale="en-US")
                page = await context.new_page()

                await self.login(page)
                await page.goto(self.dashboard_url, timeout=60000)

                await context.close()
                await browser.close()

        asyncio.run(_run())

    def collect_jobs_sync(
        self, page_num: int = 1, limit: int = 3
    ) -> list[dict[str, str | int]]:
        return asyncio.run(self.collect_jobs(page_num=page_num, limit=limit))

    def apply_to_job_sync(
        self,
        job_id: str,
        message: str,
        answers: dict[str, str],
        page_num: int = 1,
    ) -> list[dict[str, object]]:
        return asyncio.run(
            self.apply_to_job(
                job_id=job_id,
                message=message,
                answers=answers,
                page_num=page_num,
            )
        )

    def prepare_application_sync(
        self, job_id: str, message: str, answers: dict[str, str], page_num: int = 1
    ) -> list[dict[str, object]]:
        async def _run() -> list[dict[str, object]]:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=HEADLESS)
                context = await browser.new_context(locale="en-US")
                page = await context.new_page()

                await self.login(page)
                await page.goto(f"{self.dashboard_url}?page={page_num}", timeout=60000)
                await page.wait_for_selector(f"#job-item-{job_id}", timeout=60000)
                await self.open_job(page, job_id)

                unanswered = await self.prepare_application(
                    page, job_id, message, answers
                )

                await context.close()
                await browser.close()
                return unanswered

        return asyncio.run(_run())

    # async def check_job_board(self, page_num: int = 1) -> list[str]:
    #     async with async_playwright() as p:
    #         self.browser = await p.chromium.launch(headless = HEADLESS)
    #         self.context = await self.browser.new_context(locale="en-US")

    #         page = await self.context.new_page()

    #         await self.login(page)

    #         await page.goto(self.dashboard_url + f"?page={page_num}", timeout=60000)

    #         await page.wait_for_selector("[id^='job-item-']", timeout=60000)

    #         job_ids = await page.locator("[id^='job-item-']").evaluate_all(
    #             "els => els.map(e => e.id.replace('job-item-', ''))"
    #         )

    #         await self.browser.close()

    #     return job_ids


# parser = DjinniParser(EMAIL, PASSWORD)
