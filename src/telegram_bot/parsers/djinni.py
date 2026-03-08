import asyncio
import json
from pathlib import Path

from anyio import sleep
from playwright.async_api import Locator, Page, async_playwright

from config import settings
from telegram_bot.helpers.logger import setup_logger
from telegram_bot.parsers.models import ApplicationQuestion

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

    async def open_job(self, page: Page, job_id: str) -> str | None:
        logger.info(f"Opening job {job_id}...")
        job_container = page.locator(f"#job-item-{job_id}")

        title_link = job_container.locator("a.job_item__header-link")

        await title_link.click()

        logger.info(f"Waiting for job {job_id} description to load...")

        await page.wait_for_selector("div.job-post__description", timeout=60000)

        description = (
            await page.locator("div.job-post__description").inner_text()
        ).strip()

        apply_button = page.locator("button.js-inbox-toggle-reply-form").first

        # if job_id in self.processed_ids:
        #     logger.info(
        #         f"Job {job_id} has already been processed. Skipping application."
        #     )  # noqa: E501

        self.processed_ids.add(job_id)
        self._save_processed_ids()

        if await apply_button.is_visible():
            return description

        else:
            logger.info(f"Job {job_id} is not open for applications.")
            return None

    async def submit_application(self, page: Page, message: str) -> None:
        apply_button = page.locator("button#job_apply").first

        motivation_field = page.locator("textarea#message").first

        if await motivation_field.is_visible():
            await motivation_field.fill(message)

        if await apply_button.is_visible():
            print("Submitting application...")
            await sleep(2)  # small delay before clicking apply
            await apply_button.click()

        # wait for success signal (example)
        await page.wait_for_timeout(5_000)

    async def _collect_questions(self, page: Page) -> list[ApplicationQuestion]:
        questions: list[ApplicationQuestion] = []
        form = page.locator("form#apply_form").first
        if not await form.count():
            return questions

        skip_field_names = {
            "apply",
            "csrfmiddlewaretoken",
            "salary_changed",
            "cv_file_upload_id",
            "save_msg_template",
            "msg_template_name",
        }

        inputs = form.locator("input:not([type='hidden'])")
        input_count = await inputs.count()
        for idx in range(input_count):
            field = inputs.nth(idx)
            input_type = ((await field.get_attribute("type")) or "text").strip().lower()
            name = (
                (await field.get_attribute("name"))
                or (await field.get_attribute("id"))
                or f"field-{idx + 1}"
            ).strip()
            element_id = ((await field.get_attribute("id")) or "").strip()

            if (
                not name
                or name in skip_field_names
                or element_id == "message"
                or input_type == "hidden"
                or await field.is_disabled()
            ):
                continue

            if input_type == "radio":
                continue

            q_type = "number" if input_type == "number" else "text"
            text = await self._get_field_label_text(page, field, name, element_id)
            if not text:
                continue

            questions.append(ApplicationQuestion(type=q_type, name=name, text=text))

        radios = form.locator("input[type='radio']")
        radio_count = await radios.count()
        radio_groups: dict[str, list[Locator]] = {}

        for idx in range(radio_count):
            radio = radios.nth(idx)
            if await radio.is_disabled():
                continue
            name = (
                (await radio.get_attribute("name"))
                or (await radio.get_attribute("id"))
                or "radio"
            ).strip()
            radio_groups.setdefault(name, []).append(radio)

        for name, group in radio_groups.items():
            if not group:
                continue

            first = group[0]
            question_text = await self._get_radio_group_text(page, first, name)
            options: list[str] = []
            for radio in group:
                option_text = await self._get_radio_option_text(page, radio)
                if option_text:
                    options.append(option_text)

            questions.append(
                ApplicationQuestion(
                    type="radio",
                    name=name,
                    text=question_text,
                    options=options,
                )
            )

        return questions

    async def _get_field_label_text(
        self, page: Page, field: Locator, name: str, element_id: str
    ) -> str:
        if element_id:
            label = page.locator(f'label[for="{element_id}"]').first
            if await label.count():
                return (await label.inner_text()).strip()

        for attr in ("aria-label", "placeholder"):
            value = await field.get_attribute(attr)
            if value and value.strip():
                return value.strip()

        return name

    async def _get_select_options(self, select: Locator) -> list[str]:
        options: list[str] = []
        option_locator = select.locator("option")
        count = await option_locator.count()
        for idx in range(count):
            text = (await option_locator.nth(idx).inner_text()).strip()
            if text:
                options.append(text)
        return options

    async def _get_radio_group_text(
        self, page: Page, first_radio: Locator, default_name: str
    ) -> str:
        fieldset = first_radio.locator("xpath=ancestor::fieldset[1]")
        if await fieldset.count():
            legend = fieldset.locator("legend").first
            if await legend.count():
                text = (await legend.inner_text()).strip()
                if text:
                    return text

        radio_id = (await first_radio.get_attribute("id")) or ""
        if radio_id:
            label = page.locator(f'label[for="{radio_id}"]').first
            if await label.count():
                text = (await label.inner_text()).strip()
                if text:
                    return text

        return default_name

    async def _get_radio_option_text(self, page: Page, radio: Locator) -> str:
        radio_id = (await radio.get_attribute("id")) or ""
        if radio_id:
            label = page.locator(f'label[for="{radio_id}"]').first
            if await label.count():
                text = (await label.inner_text()).strip()
                if text:
                    return text

        for attr in ("value", "aria-label", "id"):
            value = await radio.get_attribute(attr)
            if value and value.strip():
                return value.strip()

        return ""

    @staticmethod
    def _lookup_answer(answers: dict[str, str], keys: list[str]) -> str | None:
        for key in keys:
            normalized = " ".join(key.lower().split())
            value = answers.get(normalized)
            if value:
                return value
        return None

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

    async def _fill_number_field(self, page: Page, name: str, value: str) -> None:
        script = """
        ({ name, value }) => {
          const selector = [
            `input[type="number"][name="${name}"]`,
            `input[type="number"]#${name}`,
            `input[name="${name}"]`,
            `input#${name}`,
          ].join(",");
          const el = document.querySelector(selector);
          if (!el) return false;
          el.focus();
          el.value = value;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
          return true;
        }
        """
        await page.evaluate(script, {"name": name, "value": value})

    async def _select_option(self, page: Page, name: str, value: str) -> None:
        script = """
        ({ name, value }) => {
          const selector = [`select[name="${name}"]`, `select#${name}`].join(",");
          const select = document.querySelector(selector);
          if (!select) return false;

          const target = value.trim().toLowerCase();
          const options = Array.from(select.options || []);

          let chosen = options.find((opt) => opt.value.toLowerCase() === target);
          if (!chosen) {
            chosen = options.find(
              (opt) => opt.innerText.trim().toLowerCase() === target
            );
          }
          if (!chosen) {
            chosen = options.find((opt) => {
              const optionText = opt.innerText.trim().toLowerCase();
              const optionValue = opt.value.toLowerCase();
              return optionText.includes(target) || optionValue.includes(target);
            });
          }
          if (!chosen) return false;

          select.value = chosen.value;
          select.dispatchEvent(new Event("input", { bubbles: true }));
          select.dispatchEvent(new Event("change", { bubbles: true }));
          return true;
        }
        """
        await page.evaluate(script, {"name": name, "value": value})

    async def _fill_apply_form_fields(
        self, page: Page, answers: dict[str, str]
    ) -> None:
        cv_value = self._lookup_answer(
            answers,
            [
                "cv",
                "resume",
                "cv file",
                "cv name",
                "share cv",
            ],
        )
        if cv_value:
            await self._select_option(page, "cv_file_upload_id", cv_value)

        salary_value = self._lookup_answer(
            answers,
            [
                "salary expectations",
                "salary expectation",
                "expected salary",
                "salary",
            ],
        )
        if salary_value:
            toggle = page.locator("button.js-salary-toggle-btn").first
            if await toggle.is_visible():
                await toggle.click()
            await self._fill_number_field(page, "salary_changed", salary_value)

    async def prepare_application(
        self, page: Page, job_id: str, message: str, answers: dict[str, str]
    ) -> list[ApplicationQuestion]:
        apply_toggle = page.locator("button.js-inbox-toggle-reply-form").first

        if not await apply_toggle.is_visible():
            return []

        await apply_toggle.click()

        textarea = page.locator("textarea#message")
        await textarea.wait_for(timeout=5000)
        await textarea.fill(message)
        await self._fill_apply_form_fields(page, answers)

        await sleep(3)  # wait for any dynamic changes based on filled fields

        questions = await self._collect_questions(page)
        unanswered: list[ApplicationQuestion] = []

        for question in questions:
            q_text = question.text.strip()
            q_type = question.type
            q_name = question.name.strip()
            if not q_text or not q_name:
                continue

            normalized = " ".join(q_text.lower().split())
            answer = answers.get(normalized)
            if not answer:
                unanswered.append(question)
                continue

            if q_type == "text":
                await self._fill_text_field(page, q_name, answer)
            elif q_type == "number":
                await self._fill_number_field(page, q_name, answer)
            elif q_type == "select":
                await self._select_option(page, q_name, answer)
            elif q_type == "radio":
                await self._select_radio(page, q_name, answer)
            else:
                unanswered.append(question)

            await sleep(1)  # small delay between filling fields

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
                if description:
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
    ) -> list[ApplicationQuestion]:
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
    ) -> list[ApplicationQuestion]:
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
    ) -> list[ApplicationQuestion]:
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
    ) -> list[ApplicationQuestion]:
        async def _run() -> list[ApplicationQuestion]:
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
