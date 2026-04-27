import os
import html
from datetime import datetime

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

CHANNEL_ID = -1003818748926

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден")

if not BASE_URL:
    raise ValueError("BASE_URL не найден")

if not WEBHOOK_SECRET:
    raise ValueError("WEBHOOK_SECRET не найден")

WEBHOOK_PATH = f"/api/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"

USER_STATE = {}

QUESTIONS = [
    ("fio", "Ваше ФИО"),
    ("city", "Из какого вы города?"),
    ("experience", "Опыт работы?"),
    ("development_blocker", "Что больше всего мешает вашему профессиональному развитию?"),
    ("improvement", "Что вы бы хотели улучшить в своей работе в качестве ортодонта?"),
    ("stress_factors", "Какие факторы вы считаете основными источниками стресса в вашей работе?"),
    ("failure_reason", "Когда в работе что-то не получается, в чём вы чаще всего видите основную причину?"),
]


def keyboard(button_rows: list[list[str]]) -> dict:
    return {
        "keyboard": [[{"text": text} for text in row] for row in button_rows],
        "resize_keyboard": True,
    }


def remove_keyboard() -> dict:
    return {"remove_keyboard": True}


def get_user_state(user_id: int) -> dict:
    if user_id not in USER_STATE:
        USER_STATE[user_id] = {
            "step": 0,
            "answers": {},
        }
    return USER_STATE[user_id]


def clear_user_state(user_id: int) -> None:
    USER_STATE.pop(user_id, None)


async def telegram_request(method: str, payload: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=payload or {})
        response.raise_for_status()
        return response.json()


async def send_message(
    chat_id: int,
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str | None = None,
) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    if parse_mode:
        payload["parse_mode"] = parse_mode

    await telegram_request("sendMessage", payload)


def build_final_message(answers: dict) -> str:
    lines = []

    for key, question in QUESTIONS:
        answer = html.escape(answers.get(key, ""))
        lines.append(f"<b>{question}:</b> {answer}")

    return "\n\n".join(lines)


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
    text = message.get("text", "").strip()

    if not chat_id or not user_id:
        return JSONResponse({"ok": True})

    if text == "/start" or text == "Заполнить анкету заново":
        clear_user_state(user_id)
        state = get_user_state(user_id)

        first_question = QUESTIONS[0][1]

        await send_message(
            chat_id,
            f"Здравствуйте. Ответьте, пожалуйста, на несколько вопросов.\n\n{first_question}",
            reply_markup=remove_keyboard(),
        )

        return JSONResponse({"ok": True})

    state = get_user_state(user_id)
    step = state["step"]

    if step >= len(QUESTIONS):
        await send_message(
            chat_id,
            "Анкета уже отправлена. Чтобы заполнить заново, нажмите кнопку ниже.",
            reply_markup=keyboard([["Заполнить анкету заново"]]),
        )

        return JSONResponse({"ok": True})

    if not text:
        await send_message(chat_id, "Пожалуйста, ответьте текстом.")
        return JSONResponse({"ok": True})

    current_key, current_question = QUESTIONS[step]
    state["answers"][current_key] = text
    state["step"] += 1

    if state["step"] < len(QUESTIONS):
        next_question = QUESTIONS[state["step"]][1]
        await send_message(chat_id, next_question)
        return JSONResponse({"ok": True})

    final_text = build_final_message(state["answers"])

    await send_message(
        CHANNEL_ID,
        final_text,
        parse_mode="HTML",
    )

    await send_message(
        chat_id,
        "Спасибо. Анкета отправлена.",
        reply_markup=keyboard([["Заполнить анкету заново"]]),
    )

    clear_user_state(user_id)

    return JSONResponse({"ok": True})
