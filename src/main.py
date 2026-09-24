import asyncio
import logging
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from src.config import Config
from src.database import AsyncSessionLocal, init_db
from src.models import WorkLog
from src.pdf_generator import PDFGenerator


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo(Config.TIMEZONE)
pdf_generator = PDFGenerator()


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_date_from_utc(value: datetime):
    return value.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).date()


def parse_date(value: str):
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError("Неверный формат даты")


def date_bounds_utc(start_date, end_date):
    start_local = datetime.combine(start_date, datetime.min.time(), tzinfo=LOCAL_TZ)
    end_local = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=LOCAL_TZ)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def report_allowed(user_id: int) -> bool:
    return not Config.admin_ids or user_id in Config.admin_ids


async def post_init(application: Application) -> None:
    await init_db()
    await pdf_generator.start_browser()
    logger.info("ARS Photo Report Bot initialized")


async def post_shutdown(application: Application) -> None:
    await pdf_generator.close_browser()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Бот фотоотчётов готов.\n\n"
        "Отправляйте в эту группу фотографии или фотоальбомы с подписями. "
        "Обычная переписка в фотоотчёт не попадает. Для формирования отчёта используйте /report."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/report — выбрать период отчёта\n"
        "/report 01.09.2026 07.09.2026 — отчёт за произвольный период\n\n"
        "В отчёт попадают только фотографии и фотоальбомы. Обычные текстовые сообщения игнорируются."
    )


async def save_work_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    user = update.effective_user
    text = message.caption if message.photo else message.text
    photo_file_id = None
    photo_unique_id = None

    if message.photo:
        photo = message.photo[-1]
        photo_file_id = photo.file_id
        photo_unique_id = photo.file_unique_id

    entry = WorkLog(
        chat_id=str(update.effective_chat.id),
        message_id=message.message_id,
        media_group_id=message.media_group_id,
        user_id=str(user.id) if user else None,
        username=(user.username or user.full_name) if user else None,
        text=text or "",
        photo_file_id=photo_file_id,
        photo_unique_id=photo_unique_id,
        timestamp=utcnow_naive(),
    )

    try:
        async with AsyncSessionLocal() as session:
            session.add(entry)
            await session.commit()
    except Exception as exc:
        logger.exception("Could not save message: %s", exc)


def period_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Сегодня", callback_data="report:today"),
                InlineKeyboardButton("Последние 7 дней", callback_data="report:7"),
            ],
            [
                InlineKeyboardButton("Последние 30 дней", callback_data="report:30"),
                InlineKeyboardButton("Другой период", callback_data="report:custom"),
            ],
        ]
    )


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not report_allowed(update.effective_user.id):
        await update.message.reply_text("Формирование отчётов доступно только администратору.")
        return

    if len(context.args) == 2:
        try:
            start_date = parse_date(context.args[0])
            end_date = parse_date(context.args[1])
            if start_date > end_date:
                start_date, end_date = end_date, start_date
        except ValueError:
            await update.message.reply_text(
                "Не понял даты. Используйте, например:\n/report 01.09.2026 07.09.2026"
            )
            return

        await build_and_send_report(
            context=context,
            chat_id=update.effective_chat.id,
            start_date=start_date,
            end_date=end_date,
            reply_target=update.message,
        )
        return

    await update.message.reply_text("За какой период сформировать отчёт?", reply_markup=period_keyboard())


async def report_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not report_allowed(update.effective_user.id):
        await query.edit_message_text("Формирование отчётов доступно только администратору.")
        return

    action = query.data.split(":", 1)[1]
    today = datetime.now(LOCAL_TZ).date()

    if action == "custom":
        await query.edit_message_text(
            "Для произвольного периода отправьте команду в формате:\n"
            "/report 01.09.2026 07.09.2026"
        )
        return

    if action == "today":
        start_date = end_date = today
    else:
        days = int(action)
        end_date = today
        start_date = today - timedelta(days=days - 1)

    await query.edit_message_text(
        f"Формирую отчёт за {start_date.strftime('%d.%m.%Y')}–{end_date.strftime('%d.%m.%Y')}…"
    )
    await build_and_send_report(
        context=context,
        chat_id=update.effective_chat.id,
        start_date=start_date,
        end_date=end_date,
        reply_target=query.message,
    )


async def load_rows(chat_id: int, start_date, end_date):
    start_utc, end_utc = date_bounds_utc(start_date, end_date)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WorkLog)
            .where(
                WorkLog.chat_id == str(chat_id),
                WorkLog.timestamp >= start_utc,
                WorkLog.timestamp < end_utc,
                WorkLog.photo_file_id.is_not(None),
            )
            .order_by(WorkLog.timestamp, WorkLog.message_id)
        )
        return list(result.scalars().all())


def group_rows(rows):
    grouped = defaultdict(list)
    for row in rows:
        date_key = local_date_from_utc(row.timestamp).isoformat()
        if row.media_group_id:
            item_key = f"album:{row.media_group_id}"
        else:
            item_key = f"message:{row.message_id}"
        grouped[(date_key, item_key)].append(row)

    result = []
    for (date_key, _), items in grouped.items():
        first = items[0]
        caption = next((item.text for item in items if item.text), "")
        result.append(
            {
                "date": date_key,
                "timestamp": first.timestamp,
                "caption": caption,
                "username": first.username or "",
                "photos": [item for item in items if item.photo_file_id],
            }
        )

    result.sort(key=lambda x: x["timestamp"])
    return result


async def download_photo(bot, file_id: str, destination: str):
    file = await bot.get_file(file_id)
    await file.download_to_drive(destination)


async def build_and_send_report(context, chat_id: int, start_date, end_date, reply_target) -> None:
    rows = await load_rows(chat_id, start_date, end_date)
    if not rows:
        await reply_target.reply_text("За выбранный период записей не найдено.")
        return

    groups = group_rows(rows)
    photo_count = sum(len(group["photos"]) for group in groups)

    status = await reply_target.reply_text(
        f"Найдено записей: {len(groups)}\nФотографий: {photo_count}\nГотовлю PDF…"
    )

    try:
        with tempfile.TemporaryDirectory(prefix="ars_report_") as temp_dir:
            report_items = []

            for group_index, group in enumerate(groups, start=1):
                local_paths = []
                for photo_index, photo in enumerate(group["photos"], start=1):
                    path = os.path.join(temp_dir, f"{group_index}_{photo_index}.jpg")
                    try:
                        await download_photo(context.bot, photo.photo_file_id, path)
                        local_paths.append(path)
                    except Exception as exc:
                        logger.warning("Failed to download photo %s: %s", photo.id, exc)

                local_dt = group["timestamp"].replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)
                report_items.append(
                    {
                        "date": local_dt.strftime("%d.%m.%Y"),
                        "time": local_dt.strftime("%H:%M"),
                        "caption": group["caption"],
                        "username": group["username"],
                        "photo_paths": local_paths,
                    }
                )

            title = Config.PROJECT_NAME.strip() or getattr(reply_target.chat, "title", None) or "Строительный объект"

            pdf_path = await pdf_generator.generate_report(
                project_name=title,
                start_date=start_date,
                end_date=end_date,
                items=report_items,
            )

            filename = (
                f"Фотоотчет_{start_date.strftime('%d.%m.%Y')}-"
                f"{end_date.strftime('%d.%m.%Y')}.pdf"
            )
            with open(pdf_path, "rb") as fh:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=fh,
                    filename=filename,
                    caption=f"Фотоотчёт за {start_date.strftime('%d.%m.%Y')}–{end_date.strftime('%d.%m.%Y')}",
                )

            try:
                os.remove(pdf_path)
            except OSError:
                pass

        await status.delete()

    except Exception as exc:
        logger.exception("Report generation failed: %s", exc)
        await status.edit_text(f"Не удалось сформировать PDF: {exc}")


def main() -> None:
    application = (
        Application.builder()
        .token(Config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CallbackQueryHandler(report_button, pattern=r"^report:"))
    application.add_handler(
        MessageHandler(filters.PHOTO, save_work_log)
    )

    logger.info("Starting @arsphotobot")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
