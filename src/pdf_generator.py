import asyncio
import base64
import io
import os
import tempfile
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

    async def generate_report(self, project_name, start_date, end_date, items) -> str:
        if not self.browser:
            await self.start_browser()

        rendered_items = []
        for item in items:
            encoded = []
            for path in item.get("photo_paths", []):
                if os.path.exists(path):
                    encoded.append(await self._encode_image(path))
            rendered_items.append(
                {
                    "date": item.get("date", ""),
                    "time": item.get("time", ""),
                    "caption": item.get("caption", ""),
                    "username": item.get("username", ""),
                    "photos": encoded,
                }
            )

        with open(os.path.join(self.template_dir, "style.css"), "r", encoding="utf-8") as fh:
            css = fh.read()

        template = self.env.get_template("report.html")
        html = template.render(
            project_name=project_name,
            start_date=start_date.strftime("%d.%m.%Y"),
            end_date=end_date.strftime("%d.%m.%Y"),
            generated_at=datetime.now().strftime("%d.%m.%Y %H:%M"),
            items=rendered_items,
            css=css,
        )

        page = await self.browser.new_page()
        try:
            await page.set_content(html, wait_until="domcontentloaded")
            output = os.path.join(
                tempfile.gettempdir(),
                f"ars_photo_report_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.pdf",
            )
            await page.pdf(
                path=output,
                format="A4",
                print_background=True,
                margin={"top": "12mm", "right": "12mm", "bottom": "14mm", "left": "12mm"},
            )
            return output
        finally:
            await page.close()
