AI REPORTING BOT — UPDATED SETUP

GitHub repository: ashisharmy1982-b/ai-reporting-bot

FILES TO REPLACE IN THE REPOSITORY ROOT
- main.py
- requirements.txt
- Aptfile
- nixpacks.toml

BOT COMMANDS
/start or /help — show instructions
/analyze — review a profile screenshot + message/comment screenshot; then enter platform, public username/profile link, and optional context
/fakeid — enter platform + public username/profile link to receive the platform's official manual reporting navigation
/myid — show your own Telegram numeric ID for the bot owner's authorization list
/cancel — cancel the current step

GEMINI MODELS
Primary: gemini-3.8-flash
Fallback: gemini-3.7-flash
The second model is tried only if the first one errors, is unavailable, is rate-limited, or returns invalid JSON.

RAILWAY VARIABLES
Required:
TELEGRAM_BOT_TOKEN
GEMINI_API_KEY
AUTHORIZED_USERS

AUTHORIZED_USERS is a comma-separated list of numeric Telegram user IDs. Each team member can use /myid to see their own ID; the owner adds allowed IDs in Railway.

IMPORTANT
- Enter only public profile usernames/handles or public profile links. Never send passwords, login codes, phone numbers, or private credentials.
- /fakeid provides manual platform navigation only. It does not check or fetch the account from the social platform and does not submit a report.
- /analyze sends both uploaded screenshots directly to Gemini Vision. It compares visible handles where possible and separately assesses visible impersonation evidence.
- A rude comment, anonymous username, new account, or low follower count alone is not proof that an account is fake.
- Instagram impersonation reporting should be used only when the profile genuinely pretends to be a real person/business. The affected person or an authorized representative should use the appropriate route.
- Telegram's in-app Report action is for specific messages/content; Telegram's FAQ directs users to @NoToScam if a scammer is pretending to be them.
- The bot does not automate reports, coordinate reports, or recommend false reports.

LIVE TESTING
Python syntax and local helper tests should be run before redeploy. Live Gemini model access, Telegram polling, and Railway deployment must be verified from Railway logs after the files are uploaded.
