import asyncio
import base64
import html
import io
import os
import tempfile
from datetime import datetime

from jinja2 import Environment, FileSystemLoader
from PIL import Image, ImageOps
from playwright.async_api import async_playwright

ARSOLIT_BLUE = "#005DAB"
ARSOLIT_LOGO_PATH = "M 133 312 L 135 313 L 134 332 L 132 331 Z M 51 312 L 54 313 L 53 322 L 50 321 Z M 11 312 L 13 313 L 12 324 L 10 323 Z M 247 302 L 247 312 L 253 313 L 253 343 L 263 343 L 263 313 L 270 312 L 270 302 Z M 205 302 L 205 343 L 213 343 L 215 341 L 219 327 L 220 342 L 229 343 L 230 302 L 220 302 L 215 317 L 214 302 Z M 166 302 L 164 331 L 162 332 L 162 343 L 172 343 L 175 312 L 176 343 L 186 343 L 186 302 Z M 122 302 L 122 343 L 145 343 L 145 302 Z M 82 302 L 82 343 L 104 343 L 104 328 L 94 328 L 94 331 L 91 331 L 91 313 L 93 312 L 94 316 L 104 316 L 104 302 Z M 63 302 L 41 302 L 41 343 L 50 343 L 51 333 L 63 332 Z M 2 302 L 0 343 L 9 343 L 10 335 L 14 336 L 14 343 L 23 343 L 21 302 Z M 237 5 L 203 5 L 202 63 L 139 0 L 137 0 L 7 126 L 2 132 L 2 287 L 215 287 L 216 155 L 139 75 L 116 96 L 56 156 L 56 233 L 90 233 L 90 170 L 119 138 L 136 121 L 139 120 L 182 168 L 182 191 L 108 192 L 108 220 L 181 220 L 182 255 L 114 256 L 36 254 L 36 145 L 137 46 L 237 144 L 236 286 L 271 286 L 272 133 L 237 97 Z"
ARSOLIT_ICON_PATH = "M 237 5 L 203 5 L 202 63 L 139 0 L 137 0 L 7 126 L 2 132 L 2 279 L 215 279 L 216 155 L 139 75 L 116 96 L 56 156 L 56 233 L 90 233 L 90 170 L 119 138 L 136 121 L 139 120 L 182 168 L 182 191 L 108 192 L 108 220 L 181 220 L 182 255 L 114 256 L 36 254 L 36 145 L 137 46 L 237 144 L 236 279 L 271 279 L 272 133 L 237 97 Z"


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
    def _encode_image_sync(path: str, max_width: int = 1200, quality: int = 78) -> dict:
        with Image.open(path) as source:
            img = ImageOps.exif_transpose(source)

            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            width, height = img.size
            if width > max_width:
                ratio = max_width / width
                img = img.resize(
                    (max_width, int(height * ratio)),
                    Image.Resampling.LANCZOS,
                )
                width, height = img.size

            aspect_ratio = width / height if height else 1
            if aspect_ratio < 0.82:
                orientation = "portrait"
            elif aspect_ratio > 1.22:
                orientation = "landscape"
            else:
                orientation = "square"

            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=quality, optimize=True)

            return {
                "b64": base64.b64encode(buffer.getvalue()).decode("utf-8"),
                "orientation": orientation,
                "aspect_ratio": round(aspect_ratio, 3),
            }

    async def _encode_image(self, path: str) -> dict:
        return await asyncio.to_thread(self._encode_image_sync, path)

    @staticmethod
    def _slot_cost(photo: dict) -> int:
        return 2 if photo.get("orientation") == "landscape" else 1

    @classmethod
    def _split_by_slots(cls, photos, max_slots: int = 4):
        chunks = []
        current = []
        used = 0

        for photo in photos:
            cost = cls._slot_cost(photo)

            if current and used + cost > max_slots:
                chunks.append(current)
                current = []
                used = 0

            current.append(photo)
            used += cost

            if used == max_slots:
                chunks.append(current)
                current = []
                used = 0

        if current:
            chunks.append(current)

        return chunks

    @staticmethod
    def _visual_rows(photos) -> int:
        landscapes = sum(1 for p in photos if p.get("orientation") == "landscape")
        compact = len(photos) - landscapes
        return landscapes + ((compact + 1) // 2)

    async def generate_report(self, project_name, start_date, end_date, items) -> str:
        if not self.browser:
            await self.start_browser()

        pages = []

        for item in items:
            encoded = []
            for path in item.get("photo_paths", []):
                if os.path.exists(path):
                    encoded.append(await self._encode_image(path))

            if not encoded:
                continue

            chunks = self._split_by_slots(encoded, 4)

            for index, chunk in enumerate(chunks):
                caption = item.get("caption", "")
                if index > 0 and caption:
                    caption = f"{caption} — продолжение"

                pages.append(
                    {
                        "date": item.get("date", ""),
                        "caption": caption,
                        "username": item.get("username", ""),
                        "photos": chunk,
                        "rows": max(1, min(2, self._visual_rows(chunk))),
                        "continued": index > 0,
                    }
                )

        with open(os.path.join(self.template_dir, "style.css"), "r", encoding="utf-8") as fh:
            css = fh.read()

        template = self.env.get_template("report.html")
        html_content = template.render(
            project_name=project_name,
            start_date=start_date.strftime("%d.%m.%Y"),
            end_date=end_date.strftime("%d.%m.%Y"),
            generated_at=datetime.now().strftime("%d.%m.%Y %H:%M"),
            pages=pages,
            css=css,
            brand_blue=ARSOLIT_BLUE,
            arsolit_logo_path=ARSOLIT_LOGO_PATH,
            arsolit_icon_path=ARSOLIT_ICON_PATH,
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
            <span style="display:flex;align-items:center;gap:5px;">
                <svg width="12" height="14" viewBox="0 0 273 280" aria-hidden="true">
                    <path d="{ARSOLIT_ICON_PATH}" fill="{ARSOLIT_BLUE}"></path>
                </svg>
                <span>Арсолит · {safe_project}</span>
            </span>
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
