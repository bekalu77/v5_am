from fastapi import FastAPI, Request
import os
from main_bot import application  # Import the telegram bot
from telegram import Update

app = FastAPI()

# Load env vars
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret")  # Optional

@app.post(f"/webhook/{WEBHOOK_SECRET}")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}
