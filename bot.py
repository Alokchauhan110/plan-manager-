import os
import logging
import asyncio
from datetime import datetime, timedelta
import pytz
from flask import Flask
from threading import Thread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from motor.motor_asyncio import AsyncIOMotorClient

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PORT = int(os.environ.get("PORT", 5000))

# --- DATABASE SETUP ---
client = AsyncIOMotorClient(MONGO_URL)
db = client['subscription_bot']
channels_col = db['channels']      
subs_col = db['subscriptions']     

# --- LOGGING ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- WEB SERVER (Keep-Alive) ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

# --- HELPER: CHECK ADMIN ---
async def is_admin(update: Update):
    if update.effective_user.id != ADMIN_ID:
        return False
    return True

# --- ADMIN COMMANDS ---

async def add_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /addchannel <channel_id> <price> <name>"""
    if not await is_admin(update): return
    
    try:
        args = context.args
        if len(args) < 3:
            await update.message.reply_text("Usage: /addchannel -100xxxxxx 200INR VIP_Channel")
            return

        ch_id = args[0]
        price = args[1]
        name = " ".join(args[2:])
        
        # Check if channel exists to preserve demo link if not provided here
        existing = await channels_col.find_one({"channel_id": ch_id})
        demo_link = existing.get('demo_link', "None") if existing else "None"

        await channels_col.update_one(
            {"channel_id": ch_id},
            {"$set": {"channel_id": ch_id, "price": price, "name": name, "demo_link": demo_link}},
            upsert=True
        )
        await update.message.reply_text(f"✅ Channel '{name}' added/updated.\nPrice: {price}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def set_demo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /setdemo <channel_id> <link>"""
    if not await is_admin(update): return
    try:
        ch_id = context.args[0]
        link = context.args[1]
        
        result = await channels_col.update_one({"channel_id": ch_id}, {"$set": {"demo_link": link}})
        
        if result.matched_count > 0:
            await update.message.reply_text(f"✅ Demo link updated for {ch_id}.\nLink: {link}")
        else:
            await update.message.reply_text("❌ Channel ID not found. Add the channel first.")
    except:
        await update.message.reply_text("Usage: /setdemo <channel_id> <link>")

async def grant_access_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /grant <user_id> <channel_id> <days>"""
    if not await is_admin(update): return

    try:
        user_id = int(context.args[0])
        ch_id = context.args[1]
        days = int(context.args[2])
    except:
        await update.message.reply_text("Usage: /grant 123456789 -100xxxxxx 30")
        return

    # 1. Generate Unique Link
    try:
        invite_link = await context.bot.create_chat_invite_link(
            chat_id=ch_id, 
            member_limit=1, 
            name=f"User_{user_id}_Plan"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error generating link. Make sure I am Admin in that channel!\nError: {e}")
        return

    # 2. Calculate Expiry
    expiry_date = datetime.now(pytz.utc) + timedelta(days=days)

    # 3. Save to DB
    await subs_col.update_one(
        {"user_id": user_id, "channel_id": ch_id},
        {"$set": {
            "expiry_date": expiry_date, 
            "invite_link": invite_link.invite_link,
            "active": True
        }},
        upsert=True
    )

    # 4. Notify Admin & User
    await update.message.reply_text(f"✅ Access Granted.\nLink: {invite_link.invite_link}")

    msg = (
        f"🎉 **Payment Accepted!**\n\n"
        f"You have been granted access for {days} days.\n"
        f"This link works only for you and one time only.\n\n"
        f"🔗 [Join Channel]({invite_link.invite_link})"
    )
    try:
        await context.bot.send_message(chat_id=user_id, text=msg, parse_mode='Markdown')
    except:
        await update.message.reply_text("⚠️ User hasn't started the bot, please send the link manually.")

# --- USER COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles both /start command and 'Back' button"""
    user_first_name = update.effective_user.first_name
    
    keyboard = [
        [InlineKeyboardButton("💎 View Plans", callback_data='view_plans')],
        [InlineKeyboardButton("📜 My Subscriptions", callback_data='my_subs')],
        [InlineKeyboardButton("📞 Support", url="https://t.me/Krowzen01")] 
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_text = f"Welcome {user_first_name}! Choose an option:"

    # If called from a Button (Back Button)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text=welcome_text, reply_markup=reply_markup)
    # If called from a Command (/start)
    else:
        await update.message.reply_text(text=welcome_text, reply_markup=reply_markup)

async def view_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    channels = channels_col.find({})
    text = "📢 **Available Channels**\n\n"
    keyboard = []
    
    has_channels = False
    async for ch in channels:
        has_channels = True
        text += f"🔹 **{ch['name']}**\n💰 Price: {ch['price']}\n"
        
        # Check and display Demo Link
        demo = ch.get('demo_link')
        if demo and demo != "None":
            text += f"👁️ [View Demo Content]({demo})\n"
            
        text += "-------------------\n"
        keyboard.append([InlineKeyboardButton(f"Buy {ch['name']}", callback_data=f"buy_{ch['channel_id']}")])

    if not has_channels:
        text = "No plans available yet."

    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='start')])
    
    # disable_web_page_preview=False allows the demo link preview to show
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown', disable_web_page_preview=False)

async def my_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    subs = subs_col.find({"user_id": user_id, "active": True})
    text = "📜 **Your Active Subscriptions**\n\n"
    
    count = 0
    async for sub in subs:
        expiry = sub['expiry_date'].strftime("%Y-%m-%d %H:%M")
        text += f"✅ Channel ID: {sub['channel_id']}\n⏳ Expires: {expiry}\n\n"
        count += 1
        
    if count == 0:
        text = "You have no active subscriptions."
        
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='start')]]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == 'start':
        await start(update, context)
        
    elif data.startswith('buy_'):
        # Send Payment Instructions
        msg = (
            f"💳 **Payment Instructions**\n\n"
            f"To get access, please send the payment to your preferred method.\n\n"
            f"📸 **IMPORTANT:** After paying, send the **Screenshot** to Admin: @Krowzen01\n\n"
            f"🆔 **Include your User ID:** `{query.from_user.id}`\n\n"
            f"Admin will verify and grant you access immediately."
        )
        # We send a new message so the user can easily copy the ID
        await context.bot.send_message(chat_id=query.from_user.id, text=msg, parse_mode='Markdown')
        await query.answer("Check the message sent!")

# --- BACKGROUND TASKS (AUTO KICK & WARNINGS) ---

async def check_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    """Checks for expired subscriptions every minute."""
    now = datetime.now(pytz.utc)
    
    cursor = subs_col.find({
        "active": True,
        "expiry_date": {"$lt": now}
    })

    async for sub in cursor:
        user_id = sub['user_id']
        channel_id = sub['channel_id']
        
        try:
            await context.bot.ban_chat_member(chat_id=channel_id, user_id=user_id)
            await context.bot.unban_chat_member(chat_id=channel_id, user_id=user_id)
            
            await subs_col.update_one({"_id": sub['_id']}, {"$set": {"active": False}})
            
            await context.bot.send_message(
                chat_id=user_id, 
                text=f"⚠️ **Plan Expired**\n\nYour subscription for channel `{channel_id}` has ended. Please renew to rejoin.",
                parse_mode='Markdown'
            )
            logger.info(f"Removed user {user_id} from {channel_id}")
            
        except Exception as e:
            logger.error(f"Failed to kick user {user_id}: {e}")
            if "Chat not found" in str(e) or "Not enough rights" in str(e):
                await subs_col.update_one({"_id": sub['_id']}, {"$set": {"active": False}})

# --- MAIN EXECUTION ---

def main():
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addchannel", add_channel_command))
    application.add_handler(CommandHandler("setdemo", set_demo_command))
    application.add_handler(CommandHandler("grant", grant_access_command))
    
    application.add_handler(CallbackQueryHandler(view_plans, pattern='^view_plans$'))
    application.add_handler(CallbackQueryHandler(my_subs, pattern='^my_subs$'))
    application.add_handler(CallbackQueryHandler(button_handler, pattern='^start$|^buy_'))

    job_queue = application.job_queue
    job_queue.run_repeating(check_subscriptions, interval=60, first=10)

    print("Bot is polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()