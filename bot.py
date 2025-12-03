import os
import logging
import asyncio
from datetime import datetime, timedelta
import pytz
from flask import Flask
from threading import Thread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from motor.motor_asyncio import AsyncIOMotorClient

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
# Your Payment Info (UPI / Wallet) - Edit this string here or I can add a command later
PAYMENT_INFO_TEXT = """
UPI ID: `Your_UPI_Here@okaxis`
Crypto: `TRX_ADDRESS_HERE`

*Scan QR Code or Copy ID above.*
"""

PORT = int(os.environ.get("PORT", 5000))

# --- DATABASE SETUP ---
client = AsyncIOMotorClient(MONGO_URL)
db = client['subscription_bot']
channels_col = db['channels']      
subs_col = db['subscriptions']     

# --- LOGGING ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- WEB SERVER ---
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
    """Usage: /addchannel <id> <price> <type> <name>"""
    # Example: /addchannel -10012345 250INR Lifetime Desi_Content
    if not await is_admin(update): return
    
    try:
        args = context.args
        if len(args) < 4:
            await update.message.reply_text("Usage: /addchannel <id> <price> <Lifetime/Monthly> <Name>")
            return

        ch_id = args[0]
        price = args[1]
        plan_type = args[2] # Lifetime or Monthly
        name = " ".join(args[3:])
        
        # Preserve existing settings if updating
        existing = await channels_col.find_one({"channel_id": ch_id})
        demo = existing.get('demo_link', "None") if existing else "None"
        forwarding = existing.get('forwarding', True) if existing else True
        media_count = existing.get('media_count', "5000+") if existing else "5000+"

        await channels_col.update_one(
            {"channel_id": ch_id},
            {"$set": {
                "channel_id": ch_id, 
                "price": price, 
                "plan_type": plan_type,
                "name": name, 
                "demo_link": demo,
                "forwarding": forwarding,
                "media_count": media_count
            }},
            upsert=True
        )
        await update.message.reply_text(f"✅ Channel Saved.\nName: {name}\nType: {plan_type}\nPrice: {price}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def set_demo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /setdemo <channel_id> <link>"""
    if not await is_admin(update): return
    try:
        ch_id = context.args[0]
        link = context.args[1]
        await channels_col.update_one({"channel_id": ch_id}, {"$set": {"demo_link": link}})
        await update.message.reply_text("✅ Demo link updated.")
    except:
        await update.message.reply_text("Usage: /setdemo <channel_id> <link>")

async def toggle_forwarding_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /setforwarding <channel_id> <on/off>"""
    if not await is_admin(update): return
    try:
        ch_id = context.args[0]
        status = context.args[1].lower() == 'on'
        await channels_col.update_one({"channel_id": ch_id}, {"$set": {"forwarding": status}})
        await update.message.reply_text(f"✅ Forwarding set to: {status}")
    except:
        await update.message.reply_text("Usage: /setforwarding <channel_id> on")

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

    # 1. Generate Link
    try:
        invite_link = await context.bot.create_chat_invite_link(
            chat_id=ch_id, 
            member_limit=1, 
            name=f"User_{user_id}_Plan"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error generating link. Bot must be Admin!\nError: {e}")
        return

    # 2. Save
    expiry_date = datetime.now(pytz.utc) + timedelta(days=days)
    await subs_col.update_one(
        {"user_id": user_id, "channel_id": ch_id},
        {"$set": {"expiry_date": expiry_date, "invite_link": invite_link.invite_link, "active": True}},
        upsert=True
    )

    # 3. Notify Admin & User
    await update.message.reply_text(f"✅ Granted. Link: {invite_link.invite_link}")

    msg = (
        f"✅ **Payment Verified!**\n\n"
        f"You have been granted access.\n"
        f"🔗 [Join Channel]({invite_link.invite_link})"
    )
    try:
        await context.bot.send_message(chat_id=user_id, text=msg, parse_mode='Markdown')
    except:
        pass

# --- USER FLOW COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main Menu"""
    text = (
        "👋 **Welcome to The Subscription Bot**\n\n"
        "👇 Choose an option below to get started:"
    )
    
    # Fetch all channels to create buttons
    channels = channels_col.find({})
    keyboard = []
    
    # Create a button for each channel (Category Style)
    async for ch in channels:
        btn_text = f"📂 {ch['name']}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"select_{ch['channel_id']}")])

    keyboard.append([InlineKeyboardButton("📜 My Subscriptions", callback_data='my_subs')])
    keyboard.append([InlineKeyboardButton("🆘 Support Team", url=f"https://t.me/Krowzen01")]) # Change username

    if update.callback_query:
        await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def show_channel_details(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    """Displays the plan details similar to the screenshot"""
    ch = await channels_col.find_one({"channel_id": channel_id})
    if not ch:
        await update.callback_query.answer("Channel not found.")
        return

    # Prepare Data
    plan_type = ch.get('plan_type', 'Lifetime')
    price = ch.get('price', '0')
    is_fwd = ch.get('forwarding', True)
    demo_link = ch.get('demo_link', None)
    
    # Build Text
    text = (
        f"📂 **Category: {ch['name']}**\n\n"
        f"🔑 Select a subscription plan below.\n"
        f"Forwarding Options determine whether you can save/forward content.\n\n"
        f"📌 **Plan Details:**\n"
        f"• Type: {plan_type}\n"
        f"• Price: {price}\n"
    )

    # Build Keyboard matching the screenshot style
    keyboard = []
    
    # 1. Demo Link Button (if exists)
    if demo_link and demo_link != "None":
        keyboard.append([InlineKeyboardButton("👀 View Sample Content ↗️", url=demo_link)])

    # 2. Forwarding Info Button (Visual only)
    fwd_text = "🚀 Forwarding: ON ✅" if is_fwd else "🚀 Forwarding: OFF ❌"
    keyboard.append([InlineKeyboardButton(fwd_text, callback_data="info_fwd")])

    # 3. The Buy Button
    buy_text = f"💎 {plan_type} Plan - {price}"
    keyboard.append([InlineKeyboardButton(buy_text, callback_data=f"buy_{channel_id}")])

    # 4. Back Button
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="start")])

    await update.callback_query.edit_message_text(
        text=text, 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode='Markdown'
    )

async def show_payment_page(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    """Shows the payment details and 'I've completed payment' button"""
    ch = await channels_col.find_one({"channel_id": channel_id})
    
    # Simulated Loading Effect
    await update.callback_query.edit_message_text("💳 Creating payment details...")
    await asyncio.sleep(1) # Fake delay for effect

    text = (
        f"💳 **Payment Information**\n"
        f"-------------------------------\n"
        f"👑 **Channel:** {ch['name']}\n"
        f"💰 **Amount to Pay:** {ch['price']}\n"
        f"-------------------------------\n\n"
        f"{PAYMENT_INFO_TEXT}\n\n"
        f"⚠️ **Instructions:**\n"
        f"1. Pay the exact amount.\n"
        f"2. Take a screenshot.\n"
        f"3. Click the button below."
    )

    keyboard = [
        [InlineKeyboardButton("✅ I've Completed Payment", callback_data=f"confirm_{channel_id}")],
        [InlineKeyboardButton("🔙 Back to Plans", callback_data=f"select_{channel_id}")]
    ]

    await update.callback_query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    """Handles the user clicking 'Completed Payment'"""
    user = update.effective_user
    ch = await channels_col.find_one({"channel_id": channel_id})

    # Notify Admin
    admin_text = (
        f"🔔 **New Payment Claim**\n\n"
        f"👤 User: {user.first_name} (ID: `{user.id}`)\n"
        f"📂 Channel: {ch['name']}\n"
        f"💰 Price: {ch['price']}\n"
        f"-----------------------\n"
        f"User claims they have paid. Please check your bank/wallet and use /grant to approve."
    )
    
    # Send message to Admin
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Could not notify admin: {e}")

    # Reply to User
    await update.callback_query.answer("✅ Request Sent!", show_alert=True)
    
    text = (
        "✅ **Payment Request Sent!**\n\n"
        "Please send your **Screenshot** now to the Admin: @Krowzen01\n\n"
        f"Include your User ID: `{user.id}`\n\n"
        "Once verified, you will receive the join link automatically."
    )
    keyboard = [[InlineKeyboardButton("🔙 Home", callback_data="start")]]
    
    await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# --- HANDLER LOGIC ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == 'start':
        await start(update, context)
    
    elif data.startswith('select_'):
        ch_id = data.split('_')[1]
        await show_channel_details(update, context, ch_id)
        
    elif data == 'info_fwd':
        await query.answer("This setting is controlled by the admin.", show_alert=True)
        
    elif data.startswith('buy_'):
        ch_id = data.split('_')[1]
        await show_payment_page(update, context, ch_id)

    elif data.startswith('confirm_'):
        ch_id = data.split('_')[1]
        await confirm_payment(update, context, ch_id)

    elif data == 'my_subs':
        # Simple sub check
        subs = subs_col.find({"user_id": query.from_user.id, "active": True})
        text = "📜 **Your Active Subscriptions**\n\n"
        count = 0
        async for sub in subs:
            expiry = sub['expiry_date'].strftime("%Y-%m-%d")
            text += f"✅ ID: {sub['channel_id']} | Exp: {expiry}\n"
            count += 1
        if count == 0: text = "No active subscriptions."
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='start')]]))

# --- BACKGROUND TASKS ---
async def check_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(pytz.utc)
    cursor = subs_col.find({"active": True, "expiry_date": {"$lt": now}})
    async for sub in cursor:
        try:
            await context.bot.ban_chat_member(chat_id=sub['channel_id'], user_id=sub['user_id'])
            await context.bot.unban_chat_member(chat_id=sub['channel_id'], user_id=sub['user_id'])
            await subs_col.update_one({"_id": sub['_id']}, {"$set": {"active": False}})
            await context.bot.send_message(sub['user_id'], f"⚠️ Plan expired for channel {sub['channel_id']}")
        except:
            await subs_col.update_one({"_id": sub['_id']}, {"$set": {"active": False}})

# --- MAIN ---
def main():
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addchannel", add_channel_command))
    application.add_handler(CommandHandler("setdemo", set_demo_command))
    application.add_handler(CommandHandler("setforwarding", toggle_forwarding_command))
    application.add_handler(CommandHandler("grant", grant_access_command))
    
    application.add_handler(CallbackQueryHandler(button_handler))

    job_queue = application.job_queue
    job_queue.run_repeating(check_subscriptions, interval=60, first=10)

    print("Bot is polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()