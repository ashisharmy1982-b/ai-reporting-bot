import os
import telebot
from telebot import types
import requests
import json
from datetime import datetime

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY') # For smart analysis

bot = telebot.TeleBot(BOT_TOKEN)

# Authorized Team Members (Telegram User IDs)
AUTHORIZED_USERS = os.environ.get('AUTHORIZED_USERS', '').split(',')

def is_authorized(user_id):
    return str(user_id) in AUTHORIZED_USERS

# --- AI ANALYSIS FUNCTION ---
def get_ai_analysis(offensive_text, platform="Instagram"):
    """Uses OpenAI to analyze the violation and give a strategy"""
    
    if not OPENAI_API_KEY:
        return {
            "violations": ["Manual Review Needed"],
            "severity": "MEDIUM",
            "report_count": 5,
            "strategy": "Please use manual reporting guidelines."
        }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""
    Act as a Social Media Safety Expert for {platform}.
    Analyze this offensive text: "{offensive_text}"
    
    Provide output in JSON format with:
    1. 'violation_types': List of specific violations (e.g., 'Hate Speech', 'Harassment', 'Adult Content', 'Self-Harm').
    2. 'severity': LOW, MEDIUM, HIGH, or CRITICAL.
    3. 'estimated_reports': Number of reports needed to get account banned/actioned (e.g., 5-10).
    4. 'reporting_category': The exact category to select in the app.
    5. 'reasoning': Short explanation why.
    """

    data = {
        "model": "gpt-3.5-turbo",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }

    try:
        response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=data)
        result = response.json()
        content = result['choices'][0]['message']['content']
        
        # Clean JSON from response
        start = content.find('{')
        end = content.rfind('}') + 1
        json_str = content[start:end]
        
        return json.loads(json_str)
    except Exception as e:
        return {"error": str(e)}

# --- BOT COMMANDS ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    if not is_authorized(message.from_user.id):
        return
    bot.reply_to(message, "👋 Welcome! Send me:\n1. Screenshot of ID\n2. Screenshot of Gali/Comment\n3. Platform Name (Insta/Telegram)")

@bot.message_handler(commands=['analyze'])
def start_analysis(message):
    if not is_authorized(message.from_user.id):
        return
    
    msg = bot.send_message(message.chat.id, "📸 Step 1: Send screenshot of the User's ID/Profile.")
    bot.register_next_step_handler(msg, get_id_ss)

def get_id_ss(message):
    if message.photo:
        msg = bot.send_message(message.chat.id, "💬 Step 2: Send screenshot of the Gali/Offensive Comment.")
        bot.register_next_step_handler(msg, get_gali_ss)
    else:
        bot.reply_to(message, "❌ Please send a valid image.")

def get_gali_ss(message):
    if message.photo:
        msg = bot.send_message(message.chat.id, "📱 Step 3: Which platform? (Instagram / Telegram / Twitter)")
        bot.register_next_step_handler(msg, get_platform)
    else:
        bot.reply_to(message, "❌ Please send a valid image.")

def get_platform(message):
    platform = message.text.strip()
    msg = bot.send_message(message.chat.id, "✍️ Step 4: Type the exact Gali/Text used in the comment:")
    bot.register_next_step_handler(msg, process_final_analysis, platform)

def process_final_analysis(message, platform):
    offensive_text = message.text
    
    # Show processing
    proc_msg = bot.send_message(message.chat.id, "🤖 AI is analyzing the violation...")
    
    # Get AI Analysis
    analysis = get_ai_analysis(offensive_text, platform)
    
    bot.delete_message(message.chat.id, proc_msg.message_id)
    
    if "error" in analysis:
        bot.reply_to(message, f"⚠️ AI Error: {analysis['error']}")
        return

    # Format the Report
    violations = analysis.get('violation_types', [])
    severity = analysis.get('severity', 'MEDIUM')
    est_reports = analysis.get('estimated_reports', 5)
    category = analysis.get('reporting_category', 'Harassment')
    
    report = f"""
🔍 **AI INVESTIGATION REPORT**
━━━━━━━━━━━━━━━━━━━━
📱 Platform: {platform}
⚠️ Severity: **{severity}**

🚨 **VIOLATIONS DETECTED:**
"""
    for v in violations:
        report += f"• {v}\n"
    
    report += f"""
━━━━━━━━━━━━━━━━━━━━
📊 **ACTION PLAN:**

🎯 Reports Needed: **{est_reports} Reports**
📂 Select Category: **{category}**
⏰ Timeframe: Next 24 Hours

💡 **Strategy:**
1. Report from {est_reports} different accounts.
2. Use category: '{category}'.
3. Attach both screenshots (ID + Gali).
4. If no action in 24h, escalate to 'Hate Speech'.

✅ **Success Chance:** High (if reported correctly)
━━━━━━━━━━━━━━━━━━━━
    """
    
    bot.send_message(message.chat.id, report, parse_mode="Markdown")

# Keep bot alive
print("🚀 Bot is running on Railway!")
bot.infinity_polling()
