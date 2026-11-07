import os
import telebot
from telebot import types
import google.generativeai as genai
import json

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# Setup Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') # Fast and free model

bot = telebot.TeleBot(BOT_TOKEN)

# Authorized Team Members
AUTHORIZED_USERS = os.environ.get('AUTHORIZED_USERS', '').split(',')

def is_authorized(user_id):
    return str(user_id) in AUTHORIZED_USERS

# --- GEMINI AI ANALYSIS ---
def get_gemini_analysis(text, platform):
    """Uses Google Gemini to analyze violation and give strategy"""
    
    prompt = f"""
    Act as a Social Media Safety Expert for {platform}.
    Analyze this text: "{text}"
    
    Identify if it contains: Hate Speech, Harassment, Adult Content, or Threats.
    
    Return ONLY a JSON object with these keys:
    1. "violation_type": (e.g., 'Hate Speech', 'Harassment', 'None')
    2. "severity": ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
    3. "reports_needed": (Number, e.g., 5, 10, 15)
    4. "report_category": (Exact category name to select in app)
    5. "reason": (Short reason in Hindi/English)
    """
    
    try:
        response = model.generate_content(prompt)
        # Clean response to get pure JSON
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_text)
    except Exception as e:
        return {"error": str(e)}

# --- BOT COMMANDS ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    if not is_authorized(message.from_user.id):
        return
    bot.reply_to(message, "👋 Welcome! Use /analyze to start smart investigation.")

@bot.message_handler(commands=['analyze'])
def start_analysis(message):
    if not is_authorized(message.from_user.id):
        return
    
    msg = bot.send_message(message.chat.id, "📸 Step 1: Send screenshot of User ID.")
    bot.register_next_step_handler(msg, get_id_ss)

def get_id_ss(message):
    if message.photo:
        msg = bot.send_message(message.chat.id, "💬 Step 2: Send screenshot of the Gali/Comment.")
        bot.register_next_step_handler(msg, get_gali_ss)
    else:
        bot.reply_to(message, "❌ Please send an image.")

def get_gali_ss(message):
    if message.photo:
        msg = bot.send_message(message.chat.id, "📱 Step 3: Platform Name? (Instagram/Telegram)")
        bot.register_next_step_handler(msg, get_platform)
    else:
        bot.reply_to(message, "❌ Please send an image.")

def get_platform(message):
    platform = message.text.strip()
    msg = bot.send_message(message.chat.id, "✍️ Step 4: Type the exact Gali/Text here:")
    bot.register_next_step_handler(msg, process_final, platform)

def process_final(message, platform):
    offensive_text = message.text
    
    proc_msg = bot.send_message(message.chat.id, "🤖 Gemini AI is analyzing...")
    
    analysis = get_gemini_analysis(offensive_text, platform)
    
    bot.delete_message(message.chat.id, proc_msg.message_id)
    
    if "error" in analysis:
        bot.reply_to(message, f"⚠️ AI Error: {analysis['error']}")
        return

    # Build Report
    v_type = analysis.get('violation_type', 'Unknown')
    severity = analysis.get('severity', 'MEDIUM')
    reports = analysis.get('reports_needed', 5)
    category = analysis.get('report_category', 'Harassment')
    reason = analysis.get('reason', 'AI detected violation')
    
    report = f"""
🔍 **GEMINI AI INVESTIGATION REPORT**
━━━━━━━━━━━━━━━━━━━━
📱 Platform: {platform}
⚠️ Severity: **{severity}**

🧠 **AI DETECTION:**
• Type: **{v_type}**
• Reason: {reason}

📊 **ACTION PLAN:**
🎯 Reports Needed: **{reports} Reports**
📂 Select Category: **{category}**
⏰ Strategy: Report from different accounts within 24h.

💡 **Pro Tip:** Since AI flagged it as '{v_type}', ensure you select the matching category in the reporting menu for faster action.
━━━━━━━━━━━━━━━━━━━━
    """
    
    bot.send_message(message.chat.id, report, parse_mode="Markdown")

print("🚀 Bot is running on Railway with Gemini AI!")
bot.infinity_polling()
