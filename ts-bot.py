import logging
import os
import requests
import time

from telegram import Update, Message
from telegram.ext import Application, ApplicationHandlerStop, CommandHandler, ContextTypes, MessageHandler, TypeHandler, filters
from telegram.helpers import escape_markdown


# Enable logging
logging.basicConfig(

    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO

)

# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def get_user_list(url: str, apikey: str) -> list[str]:

    """Get the user list from the TeamSpeak server."""

    r = requests.get(url+"/1/clientlist?-voice%20-away", headers={"X-API-Key": apikey})
    body = r.json()["body"]
    users = [user for user in body if user["client_type"] == '0']
    away_nicknames = {user["client_nickname"] for user in users if user["client_away"] == '1' or user["client_output_muted"] == '1' or user["client_input_muted"] == '1'}


    all_nicknames = {user["client_nickname"] for user in users}
    active_nicknames = all_nicknames - away_nicknames

    return sorted(active_nicknames, key=str.lower), sorted(away_nicknames, key=str.lower)


def ts_sanifize(msg: str) -> str:
    """sanitize for teamspeak"""
    s = msg.replace(' ', '\\s')
    s = s.replace('&', '\&')
    return s


def ts_global_message(url: str, apikey: str, msg: str) -> None:
    """Broadcast a global message"""

    s = ts_sanitize(msg)
    r = requests.get(url+f"/1/gm?msg={s}", headers={"X-API-Key": apikey})
    body = r.json()["body"]



async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """Send a message when the command /help is issued."""

    await update.message.reply_text("Help!")


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """Send a message when the command /whoami is issued."""

    user = update.effective_user
    group = update.effective_chat

    await update.message.reply_html(

        rf"Your user ID is {user.id} and this group ID is {group.id}",

    )


def format_user_list(active: list[str], away: list[str]) -> str:

    """Format the user list from active and away users."""

    temp = f"{len(active)}\\+_{len(away)}_: "
    temp += ", ".join([escape_markdown(x) for x in active])

    if len(away) > 0:
        temp += " \\+ _" + escape_markdown(away[0]) + "_"
    for user in away[1:]:
        temp += f", _{escape_markdown(user)}_"
    
    return temp 


async def ts_get_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """Get the user list from the TeamSpeak server."""

    ts_url = context.bot_data["ts_url"]
    ts_apikey = context.bot_data["ts_apikey"]
    active, away = get_user_list(ts_url, ts_apikey)

    await update.message.reply_text(format_user_list(active, away), parse_mode="MarkdownV2")


async def get_live_message(context: ContextTypes.DEFAULT_TYPE) -> Message:

    """Get the live message from the bot data or file."""

    if context.bot_data.get("live_msg") is None:
        logger.info("No live message to update. Trying to load message ID from file.")
        try:
            with open("config/live_chat_id.txt", "r") as f:
                chat_id, message_id  = [int(x) for x in f.read().strip().split(",")]
                live_msg = await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"Initializing...{time.time()}"
                )
                logger.info("Live message created from chat ID in file.")
                context.bot_data["live_msg"] = live_msg

        except Exception as e:
            logger.error(f"Failed to read chat ID from file: {e}")
    
    if context.bot_data.get("live_msg") is not None:
        return context.bot_data["live_msg"]
    
    logger.info("No live message found.")
    return None


async def ts_get_users_live(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """Get the user list from the TeamSpeak server. Live version."""

    # check fo existing live message
    live_msg = await get_live_message(context)

    # there is an existing live message, inform the user and return
    if live_msg is not None:
        live_msg = context.bot_data["live_msg"]
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=live_msg.message_id,
            text="Live message already exists. Please wait for it to update."
        )
        return

    # no existing live message, create one
    # Send the message and store the Message in bot_data and file
    active, away = get_user_list(context.bot_data["ts_url"], context.bot_data["ts_apikey"])
    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=format_user_list(active, away), parse_mode="MarkdownV2")
    context.bot_data["live_msg"] = sent_message  # Store the Message object

    # save the chat id persistently
    os.makedirs("config", exist_ok=True)
    with open("config/live_chat_id.txt", "w") as f:
        f.write(f"{update.effective_chat.id},{sent_message.message_id}")


async def update_live_message(context: ContextTypes.DEFAULT_TYPE):

    """Update the live message with the current user list."""

    live_msg = await get_live_message(context)
    if live_msg is not None:
        active, away = get_user_list(context.bot_data["ts_url"], context.bot_data["ts_apikey"])
        text = format_user_list(active, away)
        if text != live_msg.text_markdown_v2:
            context.bot_data["live_msg"] = await live_msg.edit_text(text, parse_mode="MarkdownV2")
        return

async def broadcast_message(context: ContextTypes.DEFAULT_TYPE):
    """Broadcast message to all users on server"""


async def check_perms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    """Check permissions."""

    group = update.effective_chat
    allowed_groups = context.bot_data["allowed_groups"]

    if group.id not in allowed_groups:

        logger.error(f"Unauthorized access to group {group.id} ({group.title})")
        raise ApplicationHandlerStop


def main() -> None:

    """Start the bot."""

    # Create the Application and pass it your bot's token.

    token = os.getenv("BOT_TOKEN")
    application = Application.builder().token(token).build()
    job_queue = application.job_queue
    job_queue.run_repeating(update_live_message, interval=60, first=10)
    application.bot_data["allowed_groups"] = list(map(int, os.getenv("ALLOWED_GROUPS", "").split(",")))
    application.bot_data["ts_apikey"] = os.getenv("TS_APIKEY")
    application.bot_data["ts_url"] = os.getenv("TS_URL")

    # enforce permission for all
    check_perms_handler = TypeHandler(Update, check_perms)
    application.add_handler(check_perms_handler, -1)

    # normal handlers
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("whoami", whoami_command))
    application.add_handler(CommandHandler("ts", ts_get_users))
    application.add_handler(CommandHandler("tslive", ts_get_users_live))
    application.add_handler(CommandHandler("gm", ts_global_message))

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":

    main()
