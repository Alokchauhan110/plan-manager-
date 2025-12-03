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
# Payment Text
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

# --- ADMIN PANEL & COMMANDS ---

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens the Admin Dashboard"""
    if not await is_admin(update): return

    # Calculate Stats
    total_channels = await channels_col.count_documents({})
    active_subs = await subs_col.count_documents({"active": True})
    
    text = (
        "👮‍♂️ **Admin Dashboard**\n\n"
        "📊 **Live Statistics:**\n"
        f"• Active Subscriptions: `{active_subs}`\n"
        f"• Added Channels: `{total_channels}`\n\n"
        "👇 Select an action below:"
    )

    keyboard = [
        [InlineKeyboardButton("🗑️ Remove/Manage Channels", callback_data='admin_manage_ch')],
        [InlineKeyboardButton("➕ How to Add Channel?", callback_data='admin_add_help')],
        [InlineKeyboardButton("📜 Command List", callback_data='admin_help_list')],
        [InlineKeyboardButton("❌ Close", callback_data='close_panel')]
    ]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_manage_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists channels with a delete button"""
    channels = channels_col.find({})
    text = "🗑️ **Delete Channels**\nClick the ❌ button to remove a channel from the list.\n\n"
    keyboard = []
    
    count = 0
    async for ch in channels:
        count += 1
        btn_text = f"❌ {ch['name']}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"del_ch_{ch['channel_id']}")])

    if count == 0:
        text = "No channels added yet."

    keyboard.append([InlineKeyboardButton("🔙 Back to Admin", callback_data='admin_home')])
    await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def delete_channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id):
    """Deletes the channel from DB"""
    await channels_col.delete_one({"channel_id": channel_id})
    await update.callback_query.answer("✅ Channel Removed!", show_alert=True)
    # Refresh the list
    await admin_manage_channels(update, context)

async def admin_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, mode):
    """Shows help text in the panel"""
    if mode == "add":
        text = (
            "➕ **How to Add a Channel**\n\n"
            "Use this format:\n"
            "`/addchannel <ID> <Price> <Type> <Name>`\n\n"
            "**Example:**\n"
            "`/addchannel -100123456 250₹ Lifetime 🤱 Mom & Son`\n\n"
            "💡 _Tip: Send this command in the chat, not here._"
        )
    else:
        text = (
            "📜 **Admin Commands**\n\n"
            "`/admin` - Open Dashboard\n"
            "`/addchannel` - Add/Update Channel\n"
            "`/setdemo <id> <link>` - Set Demo Link\n"
            "`/setforwarding <id> <on/off>`\n"
            "`/grant <uid> <chid> <days>` - Manual Access"
        )
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='admin_home')]]
    await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# --- STANDARD ADMIN COMMANDS (Keep these for functionality) ---

async def add_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update): return
    try:
        args = context.args
        if len(args) < 4:
            await update.message.reply_text("❌ Use: `/addchannel <ID> <Price> <Type> <Name>`", parse_mode='Markdown')
            return
        ch_id, price, plan_type = args[0], args[1], args[2]
        name = " ".join(args[3:])
        
        existing = await channels_col.find_one({"channel_id": ch_id})
        demo = existing.get('demo_link', "None") if existing else "None"
        forwarding = existing.get('forwarding', True) if existing else True

        await channels_col.update_one(
            {"channel_id": ch_id},
            {"$set": {"channel_id": ch_id, "price": price, "plan_type": plan_type, "name": name, "demo_link": demo, "forwarding": forwarding}},
            upsert=True
        )
        await update.message.reply_text(f"✅ **Saved:** {name}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def set_demo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update): return
    try:
        await channels_col.update_one({"channel_id": context.args[0]}, {"$set": {"demo_link": context.args[1]}})
        await update.message.reply_text("✅ Demo updated.")
    except: await update.message.reply_text("Use: /setdemo <id> <link>")

async def toggle_forwarding_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update): return
    try:
        status = context.args[1].lower() == 'on'
        await channels_col.update_one({"channel_id": context.args[0]}, {"$set": {"forwarding": status}})
        await update.message.reply_text(f"✅ Forwarding: {status}")
    except: await update.message.reply_text("Use: /setforwarding <id> on/off")

async def grant_access_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update): return
    try:
        uid, chid, days = int(context.args[0]), context.args[1], int(context.args[2])
        invite = await context.bot.create_chat_invite_link(chat_id=chid, member_limit=1, name=f"User_{uid}")
        expiry = datetime.now(pytz.utc) + timedelta(days=days)
        await subs_col.update_one({"user_id": uid, "channel_id": chid}, {"$set": {"expiry_date": expiry, "invite_link": invite.invite_link, "active": True}}, upsert=True)
        await update.message.reply_text(f"✅ Granted. Link: {invite.invite_link}")
        await context.bot.send_message(uid, f"✅ **Access Granted!**\n🔗 [Join Here]({invite.invite_link})", parse_mode='Markdown')
    except Exception as e: await update.message.reply_text(f"Error: {e}")

# --- USER FLOW ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "👋 **Welcome!**\n👇 Choose an option:"
    channels = channels_col.find({})
    keyboard = []
    async for ch in channels:
        keyboard.append([InlineKeyboardButton(f"{ch['name']}", callback_data=f"select_{ch['channel_id']}")])
    keyboard.append([InlineKeyboardButton("📜 My Subscriptions", callback_data='my_subs')])
    keyboard.append([InlineKeyboardButton("🆘 Support Team", url=f"https://t.me/Krowzen01")])
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def show_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, ch_id):
    ch = await channels_col.find_one({"channel_id": ch_id})
    if not ch: return await update.callback_query.answer("Channel not found.")
    
    text = (f"📂 **{ch['name']}**\n📌 Type: {ch.get('plan_type','Lifetime')}\n💰 Price: {ch['price']}\n"
            f"🚀 Forwarding: {'✅ ON' if ch.get('forwarding', True) else '❌ OFF'}")
    
    kb = []
    if ch.get('demo_link') and ch['demo_link'] != "None":
        kb.append([InlineKeyboardButton("👀 View Sample Content ↗️", url=ch['demo_link'])])
    kb.append([InlineKeyboardButton(f"💎 Buy {ch.get('plan_type','Lifetime')} - {ch['price']}", callback_data=f"buy_{ch_id}")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="start")])
    
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def show_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, ch_id):
    ch = await channels_col.find_one({"channel_id": ch_id})
    text = (f"💳 **Payment**\n👑 {ch['name']}\n💰 {ch['price']}\n\n{PAYMENT_INFO_TEXT}\n\n⚠️ Send Screenshot + ID to Admin.")
    kb = [[InlineKeyboardButton("✅ I've Completed Payment", callback_data=f"confirm_{ch_id}")], [InlineKeyboardButton("🔙 Back", callback_data=f"select_{ch_id}")]]
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, ch_id):
    user = update.effective_user
    ch = await channels_col.find_one({"channel_id": ch_id})
    await context.bot.send_message(ADMIN_ID, f"🔔 **Claim:** {user.first_name} (`{user.id}`) paid for {ch['name']}\n`/grant {user.id} {ch_id} 30`", parse_mode='Markdown')
    await update.callback_query.edit_message_text("✅ **Request Sent!**\nSend Screenshot to: @Krowzen01", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="start")]]), parse_mode='Markdown')

async def my_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subs = subs_col.find({"user_id": update.callback_query.from_user.id, "active": True})
    text = "📜 **Active Subs**\n\n"
    count = 0
    async for sub in subs:
        ch = await channels_col.find_one({"channel_id": sub['channel_id']})
        name = ch['name'] if ch else "Unknown"
        text += f"✅ **{name}** | Exp: {sub['expiry_date'].strftime('%Y-%m-%d')}\n"
        count += 1
    if count == 0: text = "No active subscriptions."
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='start')]]), parse_mode='Markdown')

# --- HANDLER ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    # Admin Handling
    if data == 'admin_home': await admin_panel(update, context)
    elif data == 'admin_manage_ch': await admin_manage_channels(update, context)
    elif data.startswith('del_ch_'): await delete_channel_callback(update, context, data.split('_')[2])
    elif data == 'admin_add_help': await admin_help_callback(update, context, "add")
    elif data == 'admin_help_list': await admin_help_callback(update, context, "list")
    elif data == 'close_panel': await query.delete_message()
    
    # User Handling
    elif data == 'start': await start(update, context)
    elif data.startswith('select_'): await show_channel(update, context, data.split('_')[1])
    elif data.startswith('buy_'): await show_payment(update, context, data.split('_')[1])
    elif data.startswith('confirm_'): await confirm_payment(update, context, data.split('_')[1])
    elif data == 'my_subs': await my_subs(update, context)

# --- TASKS ---
async def check_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(pytz.utc)
    cursor = subs_col.find({"active": True, "expiry_date": {"$lt": now}})
    async for sub in cursor:
        try:
            await context.bot.ban_chat_member(chat_id=sub['channel_id'], user_id=sub['user_id'])
            await context.bot.unban_chat_member(chat_id=sub['channel_id'], user_id=sub['user_id'])
            await subs_col.update_one({"_id": sub['_id']}, {"$set": {"active": False}})
            await context.bot.send_message(sub['user_id'], "⚠️ Plan Expired.")
        except: await subs_col.update_one({"_id": sub['_id']}, {"$set": {"active": False}})

def main():
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel)) # NEW
    app.add_handler(CommandHandler("addchannel", add_channel_command))
    app.add_handler(CommandHandler("setdemo", set_demo_command))
    app.add_handler(CommandHandler("setforwarding", toggle_forwarding_command))
    app.add_handler(CommandHandler("grant", grant_access_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    app.job_queue.run_repeating(check_subscriptions, interval=60, first=10)
    print("Bot is polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()