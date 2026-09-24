import asyncio
import base64
import html
import io
import os
import tempfile
from collections import OrderedDict
from datetime import datetime

from jinja2 import Environment, FileSystemLoader
from PIL import Image
from playwright.async_api import async_playwright


class PDFGenerator:
    def __init__(self, template_dir="templates"):
        self.env = Environment(loader=FileSystemLoader(template_dir))
        self.template_dir = template_dir
        self.playwright = None
        self.browser = None

    async def start_browser(self):
        if self.browser:
            return
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(args=["--no-sandbox"])

    async def close_browser(self):
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None

    @staticmethod
    def _encode_image_sync(path: str, max_width: int = 1200, quality: int = 78) -> str:
        with Image.open(path) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            width, height = img.size
            if width > max_width:
                ratio = max_width / width
                img = img.resize((max_width, int(height * ratio)), Image.Resampling.LANCZOS)

            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=quality, optimize=True)
            return base64.b64encode(buffer.getvalue()).decode("utf-8")

    async def _encode_image(self, path: str) -> str:
        return await asyncio.to_thread(self._encode_image_sync, path)

    @staticmethod
    def _chunk_photos(photos, chunk_size=6):
        return [photos[i:i + chunk_size] for i in range(0, len(photos), chunk_size)]

    async def generate_report(self, project_name, start_date, end_date, items) -> str:
        if not self.browser:
            await self.start_browser()

        expanded_items = []

        for item in items:
            encoded = []
            for path in item.get("photo_paths", []):
                if os.path.exists(path):
                    encoded.append(await self._encode_image(path))

            if not encoded:
                continue

            chunks = self._chunk_photos(encoded, 6)
            for index, chunk in enumerate(chunks):
                caption = item.get("caption", "")
                if index > 0 and caption:
                    caption = f"{caption} — продолжение"

                expanded_items.append(
                    {
                        "date": item.get("date", ""),
                        "caption": caption,
                        "username": item.get("username", ""),
                        "photos": chunk,
                        "continued": index > 0,
                    }
                )

        grouped_days = OrderedDict()
        for item in expanded_items:
            grouped_days.setdefault(item["date"], []).append(item)

        days = [
            {"date": date, "items": day_items}
            for date, day_items in grouped_days.items()
        ]

        with open(os.path.join(self.template_dir, "style.css"), "r", encoding="utf-8") as fh:
            css = fh.read()

        template = self.env.get_template("report.html")
        html_content = template.render(
            project_name=project_name,
            start_date=start_date.strftime("%d.%m.%Y"),
            end_date=end_date.strftime("%d.%m.%Y"),
            generated_at=datetime.now().strftime("%d.%m.%Y %H:%M"),
            days=days,
            css=css,
        )

        period = f"{start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}"
        safe_project = html.escape(project_name)
        safe_period = html.escape(period)

        footer_template = f"""
        <div style="
            width:100%;
            font-family:Arial,Helvetica,sans-serif;
            font-size:8px;
            color:#777;
            padding:0 12mm;
            display:flex;
            justify-content:space-between;
            align-items:center;
        ">
            <span>{safe_project}</span>
            <span>{safe_period}</span>
            <span>Стр. <span class="pageNumber"></span> из <span class="totalPages"></span></span>
        </div>
        """

        page = await self.browser.new_page()
        try:
            await page.set_content(html_content, wait_until="domcontentloaded")
            output = os.path.join(
                tempfile.gettempdir(),
                f"ars_photo_report_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.pdf",
            )

            await page.pdf(
                path=output,
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template="<div></div>",
                footer_template=footer_template,
                margin={
                    "top": "12mm",
                    "right": "12mm",
                    "bottom": "18mm",
                    "left": "12mm",
                },
            )
            return output
        finally:
            await page.close()
