# AI Reporting Evidence Review Bot

Telegram bot that reviews two screenshots with Gemini's native image understanding:

1. Profile/account screenshot (reads a public username only when clearly visible).
2. Message/comment screenshot (reads Hindi, English, Hinglish, and surrounding context).

The bot produces a preliminary evidence summary. It does not submit reports, estimate a target number of reports, or coordinate reports from multiple accounts.

## Railway variables

Set these under **Railway → your service → Variables**:

- `TELEGRAM_BOT_TOKEN` — token from BotFather.
- `GEMINI_API_KEY` — Google Gemini API key.
- `AUTHORIZED_USERS` — comma-separated numeric Telegram user IDs allowed to use the bot. Example format: `123456789,987654321`. Send `/myid` to the bot to view your own ID. Until IDs are added, the bot starts but only `/myid` is available.
- `GEMINI_MODELS` — optional comma-separated model fallback list. The default list is `gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash-lite,gemini-2.5-flash`.

Do not put API keys or the Telegram token in source code or commit them to GitHub.

## How to use

1. Open the bot and send `/start`.
2. Send `/analyze`.
3. Send the profile/account screenshot.
4. Send the message/comment screenshot.
5. Type the platform name.
6. Add context or type `skip`.

Use `/cancel` to stop a review. Send `/myid` to view your own numeric Telegram user ID for the allow-list.

Images can be sent as Telegram photos or as image files. Both screenshots are sent directly to Gemini Vision; local Tesseract installation is not required.

## Deploy

Push these project files to the correct GitHub repository connected to Railway. Railway should use the existing `Procfile` worker command (`python main.py`) and redeploy on the push. Keep the three required variables set in Railway.

## Troubleshooting

- **Access denied:** Add the user's numeric Telegram ID to `AUTHORIZED_USERS` in Railway Variables.
- **All Gemini models failed:** Check that `GEMINI_API_KEY` is valid, that the key's project has access to at least one configured model, and that API quota is available. Check Railway deployment logs for the actual error.
- **Image not received:** Send a JPG/PNG image smaller than 15 MB, as a photo or image file.
- **Bot does not respond:** Confirm the Railway service is running as a worker and that `TELEGRAM_BOT_TOKEN` is correct.

The AI output is only an initial review. Always inspect the original screenshots and use the platform's official report option only for a genuine, evidence-supported violation. Do not coordinate mass reports or submit false reports.
