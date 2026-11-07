AI REPORTING BOT — UPDATED SETUP (2 GOOGLE API KEYS)

FILES TO REPLACE IN THE ROOT OF YOUR GITHUB REPOSITORY
- main.py
- requirements.txt
- Aptfile
- nixpacks.toml
- Procfile (keep this worker start command)

BOT COMMANDS
/start or /help  — show bot instructions
/analyze          — review public username + your written explanation + optional screenshots
/fakeid           — review a suspected fake/impersonation account separately
/cancel           — cancel the current step
/myid             — show your own Telegram numeric ID

HOW /analyze WORKS
1. Type the platform: Instagram, Telegram, or another platform.
2. Enter the public username/handle or profile URL (or type skip).
3. Type what happened in your own words. Examples:
   - "This user abused me in an Instagram comment."
   - "This account sent me threatening DMs."
   - "This story posted my photo without permission."
   - "This profile copied my name and photos and is pretending to be me."
4. Optionally upload up to four screenshots: profile, comment, DM/chat, post, or story.
   Send DONE/SKIP when finished. If you have no screenshot, type SKIP and it reviews your written details.

TWO GOOGLE GEMINI API KEYS
Set these in Railway > Service > Variables:
- GEMINI_API_KEY      (first / primary API key)
- GEMINI_API_KEY_2    (second API key; used if the first key fails)
- TELEGRAM_BOT_TOKEN
- AUTHORIZED_USERS    (comma-separated Telegram numeric user IDs)
Optional:
- GEMINI_MODEL        (default: gemini-3.8-flash)

Create Gemini Authorization API keys in Google AI Studio. Google's current API-key guide says standard keys are being rejected from September 2026; use the newer Authorization key type. Guide: https://ai.google.dev/gemini-api/docs/api-key

IMPORTANT QUOTA NOTE
Gemini rate limits are applied PER GOOGLE PROJECT, not per API key. If both API keys belong to the same Google project, they share that project's quota. A second key from the same project provides credential failover only; it does NOT double the free quota. If you need independent project quota, the second key must belong to another eligible Google project. Guide: https://ai.google.dev/gemini-api/docs/rate-limits

PRIVACY AND ACCURACY
- Enter only public usernames/profile URLs. Never send passwords, OTPs, phone numbers, login details, or private account data.
- Crop/blur unrelated personal details in screenshots before sending.
- The bot does not open, scrape, search, or verify the live social-media account. It reviews only your text and the optional screenshots you submit.
- Low followers, a new account, anonymity, no profile picture, a story being posted, or one rude comment alone do not prove a fake account.
- If no screenshot is provided, an impersonation allegation stays labelled as user-reported and is not independently verified.
- The bot does not submit reports, coordinate reports, or recommend mass/false reporting.
- The AI gives a preliminary suggestion. Use only truthful categories in the platform's actual in-app report menu.

DEPLOY
1. Open https://github.com/ashisharmy1982-b/ai-reporting-bot
2. Replace the listed files in the repository root and commit the changes.
3. Railway should redeploy from GitHub.
4. Check Railway > Deployments > View Logs.
5. Verify both API keys are set and test /analyze with your own harmless sample text first.

LIVE TESTING NOTE
Python syntax and local simulated tests pass. Live Gemini API access, Telegram polling, and Railway deployment must still be tested in Railway after upload.
