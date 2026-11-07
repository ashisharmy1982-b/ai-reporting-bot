import os
import telebot
from telebot import types
import google.generativeai as genai
import json

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

if not BOT_TOKEN or not GEMINI_API_KEY:
    print("❌ ERROR: Missing Environment Variables")
    exit()

# Setup Gemini with CORRECT Model Name
genai.configure(api_key=GEMINI_API_KEY)
# Updated model name to fix 404 error
model = genai.GenerativeModel('gemini-2.0-flash-exp') 

bot = telebot.TeleBot(BOT_TOKEN, skip_pending=True)

# Authorized Team Members
AUTHORIZED_USERS_STR = os.environ.get('AUTHORIZED_USERS', '')
AUTHORIZED_USERS = [uid.strip() for uid in AUTHORIZED_USERS_STR.split(',') if uid.strip()]

def is_authorized(user_id):
    return str(user_id) in AUTHORIZED_USERS

# --- GEMINI AI ANALYSIS ---
def get_gemini_analysis(text, platform):
    try:
        prompt = f"""
        Act as a Social Media Safety Expert for {platform}.
        Analyze this text: "{text}"
        
        Identify violations: Hate Speech, Harassment, Adult Content, Threats.
        
        Return ONLY a valid JSON object with these keys:
        {{
            "violation_type": "String",
            "severity": "LOW/MEDIUM/HIGH/CRITICAL",
            "reports_needed": Integer,
            "report_category": "String",
            "reason": "String"
        }}
        """
        
        response = model.generate_content(prompt)
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_text)
    except Exception as e:
        return {
            "violation_type": "Manual Review",
            "severity": "MEDIUM",
            "reports_needed": 5,
            "report_category": "Harassment",
            "reason": f"AI Error: {str(e)[:50]}"
        }

# --- BOT HANDLERS ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "❌ Access Denied.")
        return
    bot.reply_to(message, "👋 **Reporting Bot Ready!**\nUse /analyze to start.")

@bot.message_handler(commands=['analyze'])
def start_analysis(message):
    if not is_authorized(message.from_user.id):
        return
    
    msg = bot.send_message(message.chat.id, "📸 **Step 1:** Send screenshot of User ID.")
    bot.register_next_step_handler(msg, get_id_ss)

def get_id_ss(message):
    if message.photo:
        msg = bot.send_message(message.chat.id, "💬 **Step 2:** Send screenshot of the Gali/Comment.")
        bot.register_next_step_handler(msg, get_gali_ss)
    else:
        bot.reply_to(message, "❌ Please send a valid image.")

def get_gali_ss(message):
    if message.photo:
        msg = bot.send_message(message.chat.id, "📱 **Step 3:** Platform Name? (Instagram/Telegram)")
        bot.register_next_step_handler(msg, get_platform)
    else:
        bot.reply_to(message, "❌ Please send a valid image.")

def get_platform(message):
    platform = message.text.strip()
    msg = bot.send_message(message.chat.id, "✍️ **Step 4:** Type the exact Gali/Text here:")
    bot.register_next_step_handler(msg, process_final, platform)

def process_final(message, platform):
    offensive_text = message.text
    
    proc_msg = bot.send_message(message.chat.id, "🤖 Gemini AI is analyzing...")
    
    analysis = get_gemini_analysis(offensive_text, platform)
    
    bot.delete_message(message.chat.id, proc_msg.message_id)
    
    v_type = analysis.get('violation_type', 'Unknown')
    severity = analysis.get('severity', 'MEDIUM')
    reports = analysis.get('reports_needed', 5)
    category = analysis.get('report_category', 'Harassment')
    reason = analysis.get('reason', 'No specific reason')
    
    report = f"""
🔍 **AI INVESTIGATION REPORT**
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

💡 **Tip:** Select '{category}' in the app for best results.
━━━━━━━━━━━━━━━━━━━━
    """
    
    bot.send_message(message.chat.id, report, parse_mode="Markdown")

print("✅ Bot is successfully running on Railway!")
bot.infinity_polling()
