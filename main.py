import os
import telebot
from telebot import types
import google.generativeai as genai
from PIL import Image
import pytesseract
import io
import json

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

if not BOT_TOKEN or not GEMINI_API_KEY:
    print("❌ ERROR: Missing Environment Variables")
    exit()

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash-exp') 

bot = telebot.TeleBot(BOT_TOKEN, skip_pending=True)

AUTHORIZED_USERS_STR = os.environ.get('AUTHORIZED_USERS', '')
AUTHORIZED_USERS = [uid.strip() for uid in AUTHORIZED_USERS_STR.split(',') if uid.strip()]

def is_authorized(user_id):
    return str(user_id) in AUTHORIZED_USERS

# --- OCR FUNCTION (Screenshot Reader) ---
def extract_text_from_photo(photo_file_id):
    try:
        file_info = bot.get_file(photo_file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        image = Image.open(io.BytesIO(downloaded_file))
        text = pytesseract.image_to_string(image, lang='eng+hin')
        return text.strip()
    except Exception as e:
        return f"OCR Error: {str(e)}"

# --- GEMINI ANALYSIS WITH IMAGE CONTEXT ---
def get_gemini_analysis(extracted_text, user_comment, platform):
    try:
        prompt = f"""
        Act as a Social Media Safety Expert for {platform}.
        
        Extracted Text from Screenshot: "{extracted_text}"
        User Provided Context: "{user_comment}"
        
        Analyze the violation based on both sources.
        Return ONLY valid JSON:
        {{
            "violation_type": "Hate Speech/Harassment/Adult/Threat",
            "severity": "LOW/MEDIUM/HIGH/CRITICAL",
            "reports_needed": Integer (3 to 20),
            "report_category": "Exact category name for reporting",
            "reason": "Short explanation in Hindi/English"
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
        # Store ID screenshot temporarily if needed, or just proceed
        msg = bot.send_message(message.chat.id, "💬 **Step 2:** Send screenshot of the Gali/Comment.")
        bot.register_next_step_handler(msg, get_gali_ss)
    else:
        bot.reply_to(message, "❌ Please send a valid image.")

def get_gali_ss(message):
    if message.photo:
        # Extract text immediately from this photo
        extracted_text = extract_text_from_photo(message.photo[-1].file_id)
        
        msg = bot.send_message(message.chat.id, "📱 **Step 3:** Platform Name? (Instagram/Telegram)")
        bot.register_next_step_handler(msg, get_platform, extracted_text)
    else:
        bot.reply_to(message, "❌ Please send a valid image.")

def get_platform(message, extracted_text):
    platform = message.text.strip()
    msg = bot.send_message(message.chat.id, "✍️ **Step 4:** Type any extra context (or type 'skip'):")
    bot.register_next_step_handler(msg, process_final, platform, extracted_text)

def process_final(message, platform, extracted_text):
    user_comment = message.text if message.text.lower() != 'skip' else ""
    
    proc_msg = bot.send_message(message.chat.id, "🤖 Reading screenshot & analyzing...")
    
    analysis = get_gemini_analysis(extracted_text, user_comment, platform)
    
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
• Extracted Text: "{extracted_text[:100]}..."

📊 **ACTION PLAN:**
🎯 Reports Needed: **{reports} Reports**
📂 Select Category: **{category}**
⏰ Strategy: Report from different accounts within 24h.

💡 **Tip:** Select '{category}' in the app for best results.
━━━━━━━━━━━━━━━━━━━━
    """
    
    bot.send_message(message.chat.id, report, parse_mode="Markdown")

print("✅ Bot with OCR is running on Railway!")
bot.infinity_polling()
