import os
from datetime import datetime

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID_RAW = os.getenv("CHANNEL_ID")
BASE_URL = os.getenv("BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден")

if not CHANNEL_ID_RAW:
    raise ValueError("CHANNEL_ID не найден")

try:
    CHANNEL_ID = int(CHANNEL_ID_RAW)
except ValueError:
    raise ValueError("CHANNEL_ID должен быть числом, например -1001234567890")

if not BASE_URL:
    raise ValueError("BASE_URL не найден")

if not WEBHOOK_SECRET:
    raise ValueError("WEBHOOK_SECRET не найден")

WEBHOOK_PATH = f"/api/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"

TYPE_OPTIONS = {"Сторис", "Пост", "Рилс", "Другое"}
PLATFORM_OPTIONS = {"Инстаграм", "ВК", "Ютуб", "Телеграм", "Комбинированный"}

USER_STATE = {}


def user_default_state() -> dict:
    return {
        "step": "",
        "media": "",
        "content_type": "",
        "platform": "",
        "description": "",
        "deadline": "",
        "comment": "",
    }


def get_user_state(user_id: int) -> dict:
    return USER_STATE.get(user_id, user_default_state().copy())


def set_user_fields(user_id: int, **fields) -> None:
    if user_id not in USER_STATE:
        USER_STATE[user_id] = user_default_state().copy()
    USER_STATE[user_id].update(fields)


def clear_user_state(user_id: int) -> None:
    USER_STATE.pop(user_id, None)


def parse_media(raw: str) -> list:
    if not raw:
        return []
    items = []
    for chunk in raw.split("|||"):
        if not chunk:
            continue
        parts = chunk.split("::", 1)
        if len(parts) != 2:
            continue
        items.append({"type": parts[0], "file_id": parts[1]})
    return items


def dump_media(items: list) -> str:
    return "|||".join(f"{item['type']}::{item['file_id']}" for item in items)


async def telegram_request(method: str, payload: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=payload or {})
        response.raise_for_status()
        return response.json()


async def send_message(chat_id: int, text: str, reply_markup: dict | None = None, parse_mode: str | None = None) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    await telegram_request("sendMessage", payload)


async def send_media_group(chat_id: int, media: list) -> None:
    await telegram_request("sendMediaGroup", {
        "chat_id": chat_id,
        "media": media,
    })


async def send_document(chat_id: int, file_id: str) -> None:
    await telegram_request("sendDocument", {
        "chat_id": chat_id,
        "document": file_id,
    })


async def send_audio(chat_id: int, file_id: str) -> None:
    await telegram_request("sendAudio", {
        "chat_id": chat_id,
        "audio": file_id,
    })


async def send_voice(chat_id: int, file_id: str) -> None:
    await telegram_request("sendVoice", {
        "chat_id": chat_id,
        "voice": file_id,
    })


async def send_video_note(chat_id: int, file_id: str) -> None:
    await telegram_request("sendVideoNote", {
        "chat_id": chat_id,
        "video_note": file_id,
    })


def keyboard(button_rows: list[list[str]]) -> dict:
    return {
        "keyboard": [[{"text": text} for text in row] for row in button_rows],
        "resize_keyboard": True,
    }


def remove_keyboard() -> dict:
    return {"remove_keyboard": True}


def extract_file(update_message: dict) -> tuple[str | None, str | None]:
    if update_message.get("photo"):
        return update_message["photo"][-1]["file_id"], "photo"
    if update_message.get("video"):
        return update_message["video"]["file_id"], "video"
    if update_message.get("document"):
        return update_message["document"]["file_id"], "document"
    if update_message.get("audio"):
        return update_message["audio"]["file_id"], "audio"
    if update_message.get("voice"):
        return update_message["voice"]["file_id"], "voice"
    if update_message.get("video_note"):
        return update_message["video_note"]["file_id"], "video_note"
    return None, None


@app.get("/")
async def root():
    return {"ok": True, "message": "Bot is alive"}


@app.get("/setup-webhook")
async def setup_webhook():
    result = await telegram_request("setWebhook", {
        "url": WEBHOOK_URL,
        "drop_pending_updates": True,
    })
    info = await telegram_request("getWebhookInfo")
    return {
        "set_result": result,
        "webhook_info": info,
    }


@app.get("/delete-webhook")
async def delete_webhook():
    result = await telegram_request("deleteWebhook", {
        "drop_pending_updates": True,
    })
    return result


@app.post("/api/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    data = await request.json()
    message = data.get("message")

    if not message:
        return JSONResponse({"ok": True})

    chat = message.get("chat", {})
    from_user = message.get("from", {})
    chat_id = chat.get("id")
    user_id = from_user.get("id")
    text = message.get("text", "")

    if not chat_id or not user_id:
        return JSONResponse({"ok": True})

    state = get_user_state(user_id)
    step = state.get("step", "")
    media_items = parse_media(state.get("media", ""))

    if text == "/start":
        clear_user_state(user_id)
        set_user_fields(
            user_id,
            step="waiting_for_media",
            media="",
            content_type="",
            platform="",
            description="",
            deadline="",
            comment="",
        )
        await send_message(
            chat_id,
            "Загрузи файлы (фото, видео, документы, аудио, голосовые, кружки)\nКогда закончишь — нажми кнопку",
            reply_markup=keyboard([["Это все файлы"]]),
        )
        return JSONResponse({"ok": True})

    if text == "Загрузить новые файлы":
        clear_user_state(user_id)
        set_user_fields(
            user_id,
            step="waiting_for_media",
            media="",
            content_type="",
            platform="",
            description="",
            deadline="",
            comment="",
        )
        await send_message(
            chat_id,
            "Загрузи новые файлы",
            reply_markup=keyboard([["Это все файлы"]]),
        )
        return JSONResponse({"ok": True})

    file_id, file_type = extract_file(message)
    if step == "waiting_for_media" and file_id and file_type:
        media_items.append({"file_id": file_id, "type": file_type})
        set_user_fields(user_id, media=dump_media(media_items))
        await send_message(chat_id, "Файл добавлен 👍")
        return JSONResponse({"ok": True})

    if step == "waiting_for_media" and text == "Это все файлы":
        set_user_fields(user_id, step="waiting_for_type")
        await send_message(
            chat_id,
            "Тип контента?",
            reply_markup=keyboard([["Сторис", "Пост", "Рилс", "Другое"]]),
        )
        return JSONResponse({"ok": True})

    if step == "waiting_for_type" and text in TYPE_OPTIONS:
        set_user_fields(user_id, step="waiting_for_platform", content_type=text)
        await send_message(
            chat_id,
            "Для какой соцсети?",
            reply_markup=keyboard([["Инстаграм", "ВК", "Ютуб", "Телеграм", "Комбинированный"]]),
        )
        return JSONResponse({"ok": True})

    if step == "waiting_for_platform" and text in PLATFORM_OPTIONS:
        set_user_fields(user_id, step="waiting_for_description", platform=text)
        await send_message(
            chat_id,
            "Описание?",
            reply_markup=remove_keyboard(),
        )
        return JSONResponse({"ok": True})

    if step == "waiting_for_description" and text:
        set_user_fields(user_id, step="waiting_for_deadline", description=text)
        await send_message(chat_id, "Дедлайн?")
        return JSONResponse({"ok": True})

    if step == "waiting_for_deadline" and text:
        set_user_fields(user_id, step="waiting_for_comment", deadline=text)
        await send_message(chat_id, "Комментарий?")
        return JSONResponse({"ok": True})

    if step == "waiting_for_comment" and text:
        set_user_fields(user_id, comment=text)
        final_state = get_user_state(user_id)
        final_media = parse_media(final_state.get("media", ""))

        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        final_text = (
            f"<b>{now}</b>\n\n"
            f"<b>Тип:</b>\n{final_state.get('content_type', '')}\n\n"
            f"<b>Платформа:</b>\n{final_state.get('platform', '')}\n\n"
            f"<b>Описание:</b>\n{final_state.get('description', '')}\n\n"
            f"<b>Дедлайн:</b>\n<u>{final_state.get('deadline', '')}</u>\n\n"
            f"<b>Комментарий:</b>\n{final_state.get('comment', '')}"
        )

        media_group = []
        other_files = []

        for item in final_media:
            if item["type"] in ["photo", "video"]:
                if item["type"] == "photo":
                    media_group.append({
                        "type": "photo",
                        "media": item["file_id"],
                    })
                else:
                    media_group.append({
                        "type": "video",
                        "media": item["file_id"],
                    })
            else:
                other_files.append(item)

        await send_message(CHANNEL_ID, final_text, parse_mode="HTML")

        if media_group:
            await send_media_group(CHANNEL_ID, media_group)

        for item in other_files:
            if item["type"] == "document":
                await send_document(CHANNEL_ID, item["file_id"])
            elif item["type"] == "audio":
                await send_audio(CHANNEL_ID, item["file_id"])
            elif item["type"] == "voice":
                await send_voice(CHANNEL_ID, item["file_id"])
            elif item["type"] == "video_note":
                await send_video_note(CHANNEL_ID, item["file_id"])

        await send_message(
            chat_id,
            "Отправлено в канал 🚀",
            reply_markup=keyboard([["Загрузить новые файлы"]]),
        )

        clear_user_state(user_id)
        return JSONResponse({"ok": True})

    return JSONResponse({"ok": True})
