import os
import logging
import html
import re
import uuid
import asyncio
from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InputMediaPhoto,
    ReplyKeyboardRemove
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)
from telegram.error import TimedOut
import gspread
from datetime import datetime
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

# Import Amharic translations
from texts_am import TEXTS
from flask import Flask, request
import telegram


# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # For rentals
CHANNEL_ID2 = os.getenv("CHANNEL_ID2")  # For sales
CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "credentials.json")
PORT = int(os.environ.get("PORT", 10000))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # Set this in your .env or Render environment

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Google Sheets setup
try:
    gc = gspread.service_account(CREDENTIALS_JSON)
    SHEET_NAME = "RentalListings"
    sh = gc.open(SHEET_NAME)
    worksheet = sh.sheet1
    logger.info("Google Sheets initialized successfully")
except Exception as e:
    logger.error(f"Error setting up Google Sheets: {e}")
    raise SystemExit("Failed to initialize Google Sheets connection.")

# Initialize Google Drive API
try:
    creds = Credentials.from_service_account_file(CREDENTIALS_JSON)
    drive_service = build('drive', 'v3', credentials=creds)
    logger.info("Google Drive API initialized successfully")
except Exception as e:
    logger.error(f"Error setting up Google Drive: {e}")
    drive_service = None

# Sheet headers
HEADERS = [
    "Property ID", "Rent/Sell", "Property Use", "House Type", "Rooms",
    "Area", "Location", "Price", "Additional Info", "Contact Info", 
    "Posted By", "Date", "Photo 1", "Photo 2", "Photo 3"
]

# Ensure headers exist
if not worksheet.get_all_values():
    worksheet.append_row(HEADERS)

# Conversation states
RENT_SELL, PROPERTY_USE, HOUSE_TYPE, ROOMS, AREA, LOCATION, PRICE, INFO, CONTACT, PHOTOS, CONFIRM = range(11)

# Retry configuration for Telegram API
MAX_RETRIES = 3
RETRY_DELAY = 2

async def retry_telegram_request(coroutine_func, *args, **kwargs):
    """Helper function to retry Telegram API calls"""
    for attempt in range(MAX_RETRIES):
        try:
            return await coroutine_func(*args, **kwargs)
        except TimedOut:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAY)
                continue
            raise
    return None

# ======================================================================
# BUTTON CONFIGURATIONS
# ======================================================================
def create_keyboard(button_keys, one_time=True):
    """Create a ReplyKeyboardMarkup from button keys"""
    rows = []
    for row_keys in button_keys:
        row = [KeyboardButton(TEXTS["buttons"][key]) for key in row_keys]
        rows.append(row)
    return ReplyKeyboardMarkup(rows, one_time_keyboard=one_time, resize_keyboard=True)

# Button configurations
RENT_SELL_BUTTONS = create_keyboard([["rent", "sell"]])
PROPERTY_USE_BUTTONS = create_keyboard([
    ["residence", "shop"],
    ["office", "cafe"],
    ["warehouse", "other"]
])
HOUSE_TYPE_BUTTONS = create_keyboard([
    ["traditional", "condominium"],
    ["apartment", "compound_villa"]
])
ROOMS_BUTTONS = create_keyboard([
    ["single_room", "one_bedroom"],
    ["two_bedroom", "three_bedroom"],
    ["more_than_three"]
])
AREA_BUTTONS = create_keyboard([
    ["area_small", "area_16_25"],
    ["area_26_50", "area_51_75"],
    ["area_76_110", "area_large"]
])
PREVIEW_BUTTON = create_keyboard([["preview"]])
CONTACT_BUTTON = create_keyboard([["share_contact"]])
CONFIRM_BUTTONS = create_keyboard([["confirm", "cancel"]])

# ======================================================================
# CONVERSATION HANDLERS (UPDATED WITH AMHARIC TEXT REFERENCES)
# ======================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await retry_telegram_request(update.message.reply_text, TEXTS["messages"]["start"])

async def post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await retry_telegram_request(
        update.message.reply_text,
        TEXTS["messages"]["post_start"],
        reply_markup=RENT_SELL_BUTTONS
    )
    return RENT_SELL
    
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send help message from TEXTS without footer"""
    await update.message.reply_text(
        TEXTS["messages"]["help"], 
        parse_mode=ParseMode.HTML
    )
    
async def get_rent_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["rent_or_sell"] = update.message.text
    await retry_telegram_request(
        update.message.reply_text,
        TEXTS["messages"]["ask_property_use"],
        reply_markup=PROPERTY_USE_BUTTONS
    )
    return PROPERTY_USE

async def get_property_use(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["property_use"] = update.message.text
    
    # Skip house type and rooms for warehouse/store and other non-residential types
    if context.user_data["property_use"] in [TEXTS["buttons"]["warehouse"], TEXTS["buttons"]["other"]]:
        context.user_data["house_type"] = "N/A"
        context.user_data["rooms"] = "N/A"
        await retry_telegram_request(
            update.message.reply_text,
            TEXTS["messages"]["ask_area"],
            reply_markup=AREA_BUTTONS
        )
        return AREA
    
    # For residence, shop, office, cafe - ask house type
    await retry_telegram_request(
        update.message.reply_text,
        TEXTS["messages"]["ask_house_type"],
        reply_markup=HOUSE_TYPE_BUTTONS
    )
    return HOUSE_TYPE

async def get_house_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["house_type"] = update.message.text
    await retry_telegram_request(
        update.message.reply_text,
        TEXTS["messages"]["ask_rooms"],
        reply_markup=ROOMS_BUTTONS
    )
    return ROOMS

async def get_rooms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["rooms"] = update.message.text
    await retry_telegram_request(
        update.message.reply_text,
        TEXTS["messages"]["ask_area"],
        reply_markup=AREA_BUTTONS
    )
    return AREA

async def get_area(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["area"] = update.message.text
    await retry_telegram_request(
        update.message.reply_text,
        TEXTS["messages"]["ask_location"],
        reply_markup=ReplyKeyboardRemove()
    )
    return LOCATION

async def get_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        location = update.message.text.strip()
        if not location or len(location) < 3:
            await retry_telegram_request(
                update.message.reply_text,
                TEXTS["messages"]["invalid_location"],
                reply_markup=ReplyKeyboardRemove()
            )
            return LOCATION
            
        context.user_data["location"] = location
        await retry_telegram_request(
            update.message.reply_text,
            TEXTS["messages"]["ask_price"],
            reply_markup=ReplyKeyboardRemove()
        )
        return PRICE
        
    except Exception as e:
        logger.error(f"Error in get_location: {e}")
        await retry_telegram_request(
            update.message.reply_text,
            "⚠️ An error occurred while processing location. Please try again.",
            reply_markup=ReplyKeyboardRemove()
        )
        return LOCATION

async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    price = update.message.text.strip()
    if not price or not re.match(r'^[0-9,.\s]+$', price):
        await retry_telegram_request(update.message.reply_text, TEXTS["messages"]["invalid_price"])
        return PRICE
        
    context.user_data["price"] = price
    await retry_telegram_request(
        update.message.reply_text,
        TEXTS["messages"]["ask_info"],
        reply_markup=ReplyKeyboardRemove()
    )
    return INFO

async def get_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["info"] = update.message.text
    contact_example = TEXTS["messages"]["contact_format_example"]
    await retry_telegram_request(
        update.message.reply_text,
        TEXTS["messages"]["ask_contact"] + "\n" + contact_example,
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton(TEXTS["buttons"]["share_contact"], request_contact=True)]],
            one_time_keyboard=True,
            resize_keyboard=True
        )
    )
    return CONTACT

async def get_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.contact:
        raw_phone = update.message.contact.phone_number
        # Normalize to 10-digit local format with space before 0
        digits = re.sub(r"\D", "", raw_phone)
        if digits.startswith("251") and len(digits) == 12:
            normalized = " 0" + digits[3:]  # 251911223344 →  0911223344
        elif digits.startswith("9") and len(digits) == 9:
            normalized = " 0" + digits  # 911223344 →  0911223344
        elif digits.startswith("0") and len(digits) == 10:
            normalized = "  " + digits  # 0911223344 →  0911223344
        else:
            normalized = "  " + raw_phone  # fallback
            
        context.user_data["contact"] = normalized
    else:
        # Manual phone number input
        phone = update.message.text.strip()
        digits = re.sub(r"\D", "", phone)
        
        if len(digits) == 10 and digits.startswith("0"):
            normalized = " " + digits
        elif len(digits) == 9:
            normalized = " 0" + digits
        else:
            contact_example = TEXTS["messages"]["contact_format_example"]
            await retry_telegram_request(
                update.message.reply_text,
                TEXTS["messages"]["invalid_contact"] + "\n" + contact_example,
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton(TEXTS["buttons"]["share_contact"], request_contact=True)]],
                    one_time_keyboard=True,
                    resize_keyboard=True
                )
            )
            return CONTACT
            
        context.user_data["contact"] = normalized

    context.user_data["posted_by"] = update.message.from_user.id
    context.user_data["photos"] = []

    await retry_telegram_request(
        update.message.reply_text,
        TEXTS["messages"]["ask_photos"],
        reply_markup=PREVIEW_BUTTON
    )
    return PHOTOS

async def get_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        # Handle preview button
        if update.message.text == TEXTS["buttons"]["preview"]:
            return await preview_listing(update, context)
        
        # Initialize photos list if not exists
        if "photos" not in context.user_data:
            context.user_data["photos"] = []
        
        # Check if message contains photo
        if not update.message.photo:
            await retry_telegram_request(
                update.message.reply_text,
                TEXTS["messages"]["photo_error"],
                reply_markup=PREVIEW_BUTTON
            )
            return PHOTOS
        
        # Check photo count
        if len(context.user_data["photos"]) >= 3:
            await retry_telegram_request(
                update.message.reply_text,
                TEXTS["messages"]["max_photos"],
                reply_markup=PREVIEW_BUTTON
            )
            return PHOTOS
        
        # Process photo
        photo_file = await update.message.photo[-1].get_file()
        photo_path = f"photo_{uuid.uuid4().hex}.jpg"
        await photo_file.download_to_drive(photo_path)
        context.user_data["photos"].append(photo_path)
        
        # Send appropriate response
        remaining = 3 - len(context.user_data["photos"])
        if remaining > 0:
            message = f"{TEXTS['messages']['photo_added'].format(remaining)}\n\n{TEXTS['messages']['ask_photos']}"
        else:
            message = TEXTS["messages"]["all_photos_added"]
        
        await retry_telegram_request(
            update.message.reply_text,
            message,
            reply_markup=PREVIEW_BUTTON
        )
        
        return PHOTOS
        
    except Exception as e:
        logger.error(f"Error in get_photos: {e}")
        await retry_telegram_request(
            update.message.reply_text,
            TEXTS["messages"]["photo_error"],
            reply_markup=PREVIEW_BUTTON
        )
        return PHOTOS
        
async def preview_listing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data
    data["property_id"] = str(uuid.uuid4().hex)[:8].upper()
    data["date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # Escape user inputs for safety
    def esc(txt): return html.escape(str(txt))
    
    # Build caption
    caption = TEXTS["messages"]["preview_title"]
    caption += TEXTS["messages"]["property_id"].format(esc(data["property_id"]))
    caption += TEXTS["messages"]["rent_or_sell"].format(esc(data["rent_or_sell"]))
    caption += TEXTS["messages"]["property_use"].format(esc(data["property_use"]))
    
    # Only include these if they exist
    if "house_type" in data:
        caption += TEXTS["messages"]["house_type"].format(esc(data["house_type"]))
    if "rooms" in data:
        caption += TEXTS["messages"]["rooms"].format(esc(data["rooms"]))
        
    caption += TEXTS["messages"]["area"].format(esc(data["area"]))
    caption += TEXTS["messages"]["location"].format(esc(data["location"]))
    caption += TEXTS["messages"]["price"].format(esc(data["price"]))
    caption += TEXTS["messages"]["details"].format(esc(data["info"]))
    caption += TEXTS["messages"]["contact"].format(esc(data["contact"]))
    
    username = update.message.from_user.username or str(data["posted_by"])
    caption += TEXTS["messages"]["posted_by"].format(esc(username))
    caption += TEXTS["messages"]["date"].format(esc(data["date"]))
    caption += "\n\n" + TEXTS["messages"]["footer"]
    # Send preview
    if data.get("photos"):
        try:
            media = []
            for i, path in enumerate(data["photos"]):
                with open(path, "rb") as f:
                    media.append(InputMediaPhoto(
                        media=f,
                        caption=caption if i == 0 else None,
                        parse_mode=ParseMode.HTML
                    ))
            await retry_telegram_request(context.bot.send_media_group, chat_id=update.message.chat_id, media=media)
        except Exception as e:
            logger.error(f"Media group error: {e}")
            await retry_telegram_request(
                update.message.reply_text,
                caption,
                parse_mode=ParseMode.HTML
            )
    else:
        await retry_telegram_request(
            update.message.reply_text,
            caption,
            parse_mode=ParseMode.HTML
        )

    await retry_telegram_request(
        update.message.reply_text,
        TEXTS["messages"]["confirm_prompt"],
        reply_markup=CONFIRM_BUTTONS
    )
    return CONFIRM

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_input = update.message.text
    if user_input == TEXTS["buttons"]["cancel"]:
        return await cancel(update, context)
    
    data = context.user_data
    try:
        # Save to Google Sheets
        row = [
            data["property_id"],
            data["rent_or_sell"],
            data["property_use"],
            data.get("house_type", "N/A"),
            data.get("rooms", "N/A"),
            data["area"],
            data["location"],
            data["price"],
            data["info"],
            data["contact"],
            f"@{update.message.from_user.username}" if update.message.from_user.username else str(data["posted_by"]),
            data["date"],
        ]
        
        # Add photo paths
        for i in range(3):
            row.append(data["photos"][i] if i < len(data["photos"]) else "")
            
        worksheet.append_row(row)
        logger.info(f"Saved to Google Sheets: {data['property_id']}")
        
        # Prepare channel post (same as preview but without ID)
        # Escape user inputs for safety
        def esc(txt): return html.escape(str(txt))
        
        # Build channel caption
        channel_caption = TEXTS["messages"]["preview_title"]
        channel_caption += TEXTS["messages"]["rent_or_sell"].format(esc(data["rent_or_sell"]))
        channel_caption += TEXTS["messages"]["property_use"].format(esc(data["property_use"]))
        
        if "house_type" in data:
            channel_caption += TEXTS["messages"]["house_type"].format(esc(data["house_type"]))
        if "rooms" in data:
            channel_caption += TEXTS["messages"]["rooms"].format(esc(data["rooms"]))
            
        channel_caption += TEXTS["messages"]["area"].format(esc(data["area"]))
        channel_caption += TEXTS["messages"]["location"].format(esc(data["location"]))
        channel_caption += TEXTS["messages"]["price"].format(esc(data["price"]))
        channel_caption += TEXTS["messages"]["details"].format(esc(data["info"]))
        channel_caption += TEXTS["messages"]["contact"].format(esc(data["contact"]))
        
        username = update.message.from_user.username or str(data["posted_by"])
        channel_caption += TEXTS["messages"]["posted_by"].format(esc(username))
        channel_caption += TEXTS["messages"]["date"].format(esc(data["date"]))
        channel_caption += "\n\n" + TEXTS["messages"]["footer"]
        
        # Determine which channel to post to based on rent/sell
        channel_id = CHANNEL_ID2 if data["rent_or_sell"] == TEXTS["buttons"]["sell"] else CHANNEL_ID
        
        # Post to channel
        if data.get("photos"):
            try:
                media = []
                for i, photo_path in enumerate(data["photos"]):
                    with open(photo_path, "rb") as photo_file:
                        media.append(InputMediaPhoto(
                            media=photo_file,
                            caption=channel_caption if i == 0 else None,
                            parse_mode=ParseMode.HTML
                        ))
                await retry_telegram_request(
                    context.bot.send_media_group,
                    chat_id=channel_id,
                    media=media
                )
            except Exception as e:
                logger.error(f"Error sending media to channel: {e}")
                await retry_telegram_request(
                    context.bot.send_message,
                    chat_id=channel_id,
                    text=channel_caption,
                    parse_mode=ParseMode.HTML
                )
        else:
            await retry_telegram_request(
                context.bot.send_message,
                chat_id=channel_id,
                text=channel_caption,
                parse_mode=ParseMode.HTML
            )
        
        # Cleanup photos
        for photo_path in data.get("photos", []):
            try:
                os.remove(photo_path)
            except OSError:
                pass
        
        await retry_telegram_request(
            update.message.reply_text,
            TEXTS["messages"]["success"],
            reply_markup=ReplyKeyboardRemove()
        )
        
    except Exception as e:
        logger.error(f"Error posting listing: {e}")
        await retry_telegram_request(
            update.message.reply_text,
            TEXTS["messages"]["sheet_error"].format(e),
            reply_markup=ReplyKeyboardRemove()
        )
    
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Cleanup any uploaded photos
    photos = context.user_data.get("photos", [])
    for photo_path in photos:
        try:
            os.remove(photo_path)
        except OSError:
            pass
    
    await retry_telegram_request(
        update.message.reply_text,
        TEXTS["messages"]["canceled"],
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data.clear()
    return ConversationHandler.END

def main() -> None:
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .concurrent_updates(True)
        .build()
    )

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("post", post)],
        states={
            RENT_SELL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_rent_sell)],
            PROPERTY_USE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_property_use)],
            HOUSE_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_house_type)],
            ROOMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_rooms)],
            AREA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_area)],
            LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_location)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_price)],
            INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_info)],
            CONTACT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_contact),
                MessageHandler(filters.CONTACT, get_contact)
            ],
            PHOTOS: [
                MessageHandler(filters.PHOTO, get_photos),
                MessageHandler(filters.TEXT & filters.Regex(f'^{re.escape(TEXTS["buttons"]["preview"])}$'), preview_listing)
            ],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conv_handler)

    # Set webhook
    async def set_webhook():
        await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook")

    async def run():
        await set_webhook()
        await application.initialize()
        await application.start()
        await application.updater.start_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=f"{WEBHOOK_URL}/webhook",
        )
        logger.info("Bot is running with webhook...")

    asyncio.run(run())

application = None

def main() -> None:
    global application
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .concurrent_updates(True)
        .build()
    )
    ...
    asyncio.run(run())

main()


