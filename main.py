"""Telegram AI evidence-review assistant.

The bot reviews user-provided text and optional screenshots. It does not access
social-media accounts, scrape profiles, or submit/coordinate reports.

Required Railway variables:
  TELEGRAM_BOT_TOKEN
  GEMINI_API_KEY
  AUTHORIZED_USERS

Optional:
  GEMINI_API_KEY_2   Second Google Gemini API key (failover)
  GEMINI_MODEL       Defaults to gemini-3.8-flash
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import sys
import time
from typing import Any

import telebot
from google import genai
from google.genai import types
from PIL import Image, ImageOps, UnidentifiedImageError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEYS = []
for _key_name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2"):
    _key = os.getenv(_key_name, "").strip()
    if _key and _key not in GEMINI_API_KEYS:
        GEMINI_API_KEYS.append(_key)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
# Keep the two API keys, but also switch models when Google's selected model is
# overloaded. These stable Flash alternatives support text + image understanding.
_fallback_raw = os.getenv(
    "GEMINI_FALLBACK_MODELS",
    "gemini-3.6-flash,gemini-3.5-flash-lite",
)
GEMINI_MODEL_CANDIDATES = []
for _model in [GEMINI_MODEL, *[x.strip() for x in _fallback_raw.split(",") if x.strip()]]:
    if _model and _model not in GEMINI_MODEL_CANDIDATES:
        GEMINI_MODEL_CANDIDATES.append(_model)
try:
    GEMINI_TIMEOUT_MS = int(os.getenv("GEMINI_TIMEOUT_MS", "180000"))
except ValueError:
    GEMINI_TIMEOUT_MS = 180_000
GEMINI_TIMEOUT_MS = max(60_000, min(GEMINI_TIMEOUT_MS, 300_000))
AUTHORIZED_USERS = {
    item.strip()
    for item in os.getenv("AUTHORIZED_USERS", "").split(",")
    if item.strip()
}

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_SIDE = 3072
MAX_IMAGE_PIXELS = 16_000_000
MAX_EVIDENCE_IMAGES = 4
MAX_CONTEXT_CHARS = 3500

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("ai_reporting_bot")

if not BOT_TOKEN:
    raise SystemExit("Missing Railway variable: TELEGRAM_BOT_TOKEN")
if not GEMINI_API_KEYS:
    raise SystemExit("Set GEMINI_API_KEY (and optionally GEMINI_API_KEY_2) in Railway.")
if len(GEMINI_API_KEYS) < 2:
    logger.warning("Only one Gemini API key is configured; API-key failover is not available.")
if not AUTHORIZED_USERS:
    logger.warning("AUTHORIZED_USERS is empty; only /myid will be useful until IDs are added.")

bot = telebot.TeleBot(BOT_TOKEN, skip_pending=True)


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def is_authorized(user_id: int | str | None) -> bool:
    return user_id is not None and str(user_id) in AUTHORIZED_USERS


def is_cancel(message: telebot.types.Message) -> bool:
    return bool(message.text and message.text.strip().lower() == "/cancel")


def is_skip(message: telebot.types.Message) -> bool:
    return bool(message.text and message.text.strip().lower() in {"skip", "done", "no", "none"})


def deny(message: telebot.types.Message) -> None:
    bot.reply_to(
        message,
        "Access denied. Ask the bot owner to add your numeric Telegram ID to AUTHORIZED_USERS.\n"
        "Use /myid to see your own ID.",
    )


def image_file_id(message: telebot.types.Message) -> str | None:
    if message.photo:
        return message.photo[-1].file_id
    doc = message.document
    if doc:
        mime = (doc.mime_type or "").lower()
        name = (doc.file_name or "").lower()
        if mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp")):
            return doc.file_id
    return None


def download_image(message: telebot.types.Message) -> Image.Image:
    file_id = image_file_id(message)
    if not file_id:
        raise ValueError("Please send a JPG, PNG or WebP screenshot/image.")

    info = bot.get_file(file_id)
    raw = bot.download_file(info.file_path)
    if not raw:
        raise ValueError("Telegram returned an empty image. Please send it again.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Image is over 8 MB. Please send a smaller screenshot.")

    try:
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("Image dimensions are too large. Please send a smaller screenshot.")
            image.load()
            image = image.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("I could not open that image. Please send JPG, PNG or WebP.") from exc

    if image.width > MAX_IMAGE_SIDE or image.height > MAX_IMAGE_SIDE:
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
    return image


def parse_json(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        raise ValueError("Gemini returned an empty answer.")
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Gemini response did not contain JSON.")
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Gemini response was not a JSON object.")
    return value


def safe_text(value: Any, default: str, limit: int = 600) -> str:
    if not isinstance(value, str):
        return default
    value = " ".join(value.split()).strip()
    return value[:limit] if value else default


def safe_list(value: Any, limit: int = 5) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:limit]:
        if isinstance(item, str) and item.strip():
            result.append(" ".join(item.split())[:240])
    return result


# ---------------------------------------------------------------------------
# Gemini prompt and structured result
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = r"""
You are a careful, neutral social-media safety and reporting assistant.
Your user is asking for a preliminary review, not a verdict or a way to punish someone.

PLATFORM: {platform}
PUBLIC USERNAME / PROFILE LINK (user-entered; it is not independently verified):
{account}

WHAT THE USER SAYS HAPPENED (unverified user-provided context):
<context>
{context}
</context>

The user may also attach up to four screenshots. They can be a profile, comment,
DM/chat, post, or story. Read every attached image directly. Text can be Hindi,
English, Hinglish, transliterated Hindi, or mixed with emojis. There may be no images.

Review rules:
1. Base the preliminary category on the actual described/visible conduct. A single rude
   word or disagreement is not automatically bullying/harassment. Consider targeting,
   repetition, threats, hateful abuse, spam/scam, privacy violation, and sexual harassment
   only when the evidence/context supports it.
2. For a story, post, comment, or DM, tell the user whether the most suitable report target
   is the specific content/message or the account. Do not recommend reporting both without
   separate evidence for both.
3. Fake/impersonation assessment is separate. Low followers, a new account, no profile photo,
   an unusual username, anonymity, a story being posted, or an insult do NOT prove a fake ID.
   Use POSSIBLE_IMPERSONATION only when the submitted context/images provide concrete signs
   the profile is pretending to be a specific real person or organisation (for example,
   copying their identity/photos and presenting itself as them). If the user alleges copying
   but provides no visible corroboration, use USER_REPORTED_ONLY and say what evidence is
   still needed. If unclear, use INSUFFICIENT_EVIDENCE.
4. The entered username/profile link is only a reference. You cannot open, search, access,
   or verify the live account. Never claim you checked followers, profile history, stories,
   identity, or account status online.
5. Compare usernames in screenshots only when clearly visible. Say when the sender or profile
   cannot be matched. Do not assume the account in a chat is the account in a profile screenshot.
6. Distinguish what is visible in screenshots from what the user merely reports. Quote short
   relevant excerpts only; mask severe profanity with asterisks when practical.
7. Suggest one closest genuine report category only when evidence supports it. If unclear,
   say "No report category can be chosen confidently yet" and list the missing evidence.
   In-app wording varies by platform/version; do not invent exact menu options.
8. Do not advise mass-reporting, multiple accounts, pressure campaigns, false claims,
   retaliation, doxxing, or contacting the target. Never suggest a report just because
   someone has low followers or seems suspicious.
9. Keep the output understandable Hindi/Hinglish. Do not repeat private details such as
   phone numbers, email addresses, home addresses, passwords, or login codes.

Return ONLY valid JSON with exactly these fields:
{
  "issue_type": "harassment_bullying | hate_or_discrimination | threat | spam_or_scam | privacy_violation | sexual_harassment | impersonation | no_clear_violation | other",
  "severity": "LOW | MEDIUM | HIGH | CRITICAL",
  "confidence": "LOW | MEDIUM | HIGH",
  "report_target": "SPECIFIC_CONTENT | ACCOUNT | NONE | NEEDS_REVIEW",
  "suggested_category": "one closest truthful platform report category, or No report category yet",
  "account_status": "POSSIBLE_IMPERSONATION | USER_REPORTED_ONLY | NO_CLEAR_SIGN | INSUFFICIENT_EVIDENCE",
  "account_category": "Impersonation only if supported; otherwise do not report as fake yet",
  "account_evidence": ["brief evidence or missing evidence"],
  "content_evidence": ["brief visible screenshot evidence, or user-reported context clearly labelled"],
  "explanation": "simple, balanced Hindi/Hinglish explanation",
  "next_step": "specific safe next step and what evidence to retain",
  "missing_information": ["only the important missing facts, if any"]
}
""".strip()


def normalize_result(data: dict[str, Any], has_images: bool) -> dict[str, Any]:
    allowed_issues = {
        "harassment_bullying", "hate_or_discrimination", "threat", "spam_or_scam",
        "privacy_violation", "sexual_harassment", "impersonation",
        "no_clear_violation", "other",
    }
    allowed_severity = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    allowed_confidence = {"LOW", "MEDIUM", "HIGH"}
    allowed_targets = {"SPECIFIC_CONTENT", "ACCOUNT", "NONE", "NEEDS_REVIEW"}
    allowed_account = {
        "POSSIBLE_IMPERSONATION", "USER_REPORTED_ONLY", "NO_CLEAR_SIGN", "INSUFFICIENT_EVIDENCE"
    }

    issue = str(data.get("issue_type", "no_clear_violation")).strip().lower()
    severity = str(data.get("severity", "LOW")).strip().upper()
    confidence = str(data.get("confidence", "LOW")).strip().upper()
    target = str(data.get("report_target", "NEEDS_REVIEW")).strip().upper()
    account = str(data.get("account_status", "INSUFFICIENT_EVIDENCE")).strip().upper()

    if issue not in allowed_issues:
        issue = "other"
    if severity not in allowed_severity:
        severity = "LOW"
    if confidence not in allowed_confidence:
        confidence = "LOW"
    if target not in allowed_targets:
        target = "NEEDS_REVIEW"
    if account not in allowed_account:
        account = "INSUFFICIENT_EVIDENCE"

    account_evidence = safe_list(data.get("account_evidence"))
    content_evidence = safe_list(data.get("content_evidence"))

    # Without an image, any claim of impersonation can only be user-reported.
    if not has_images and account == "POSSIBLE_IMPERSONATION":
        account = "USER_REPORTED_ONLY"
    if account == "POSSIBLE_IMPERSONATION" and not account_evidence:
        account = "INSUFFICIENT_EVIDENCE"

    if issue == "no_clear_violation":
        if account == "POSSIBLE_IMPERSONATION":
            target = "ACCOUNT"
            suggested_category = "Impersonation (only after manual identity verification)"
        else:
            target = "NONE"
            suggested_category = "No report recommended from the information provided"
    else:
        suggested_category = safe_text(
            data.get("suggested_category"), "No report category can be chosen confidently yet", 140
        )

    # Prevent an unverified fake-account allegation from becoming an account-report
    # instruction. If evidence is user-reported only, keep it at manual review.
    if target == "ACCOUNT" and account != "POSSIBLE_IMPERSONATION":
        target = "NEEDS_REVIEW"
        suggested_category = "Impersonation only if manual checks confirm identity copying"

    if account == "POSSIBLE_IMPERSONATION":
        account_category = "Possible impersonation — verify the copied identity before reporting"
    elif account == "USER_REPORTED_ONLY":
        account_category = "User-reported suspicion — collect profile/identity evidence first"
    elif account == "NO_CLEAR_SIGN":
        account_category = "No fake-account report recommended from the supplied evidence"
    else:
        account_category = "Not enough information to recommend a fake-account report"

    if confidence == "HIGH" and not has_images:
        confidence = "MEDIUM"

    return {
        "issue_type": issue,
        "severity": severity,
        "confidence": confidence,
        "report_target": target,
        "suggested_category": suggested_category,
        "account_status": account,
        "account_category": account_category,
        "account_evidence": account_evidence,
        "content_evidence": content_evidence,
        "explanation": safe_text(data.get("explanation"), "Abhi diye gaye details se clear decision nahi ho saka.", 900),
        "next_step": safe_text(data.get("next_step"), "Original content ka screenshot aur date/time save karein.", 650),
        "missing_information": safe_list(data.get("missing_information"), 4),
    }


def _http_status_from_error(exc: Exception, message: str) -> int | None:
    """Extract an HTTP status from google-genai exception variants."""
    for attr in ("code", "status_code", "http_status"):
        value = getattr(exc, attr, None)
        try:
            if value is not None:
                parsed = int(value)
                if 100 <= parsed <= 599:
                    return parsed
        except (TypeError, ValueError):
            pass
    match = re.search(r"\b(400|401|403|404|408|429|500|502|503|504)\b", message)
    return int(match.group(1)) if match else None


def run_gemini_review(
    platform: str,
    account: str,
    context: str,
    images: list[Image.Image],
) -> tuple[dict[str, Any], int, str]:
    # Replace only named placeholders so the JSON example braces remain literal.
    prompt = (
        PROMPT_TEMPLATE
        .replace("{platform}", platform[:60])
        .replace("{account}", account or "Not provided")
        .replace("{context}", context or "No explanation provided")
    )
    contents: Any = [prompt, *images] if images else prompt
    errors: list[str] = []
    max_attempts_per_key_model = 2
    retry_delays_seconds = (3,)
    retryable_status_codes = {408, 500, 502, 503, 504}

    # Try the configured primary model first, then stable Flash alternatives.
    # Both Google API keys are retained and tested against each model. Changing
    # the model is important for 503 capacity errors; changing only the API key
    # cannot make an overloaded model available.
    for model_name in GEMINI_MODEL_CANDIDATES:
        for key_number, api_key in enumerate(GEMINI_API_KEYS, start=1):
            client = None
            last_error = "No response received."
            try:
                client = genai.Client(
                    api_key=api_key,
                    http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
                )
                for attempt in range(1, max_attempts_per_key_model + 1):
                    try:
                        response = client.models.generate_content(
                            model=model_name,
                            contents=contents,
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                temperature=0.2,
                                max_output_tokens=1500,
                            ),
                        )
                        parsed = parse_json(response.text or "")
                        return normalize_result(parsed, bool(images)), key_number, model_name
                    except Exception as exc:
                        raw_error = str(exc)
                        for secret in GEMINI_API_KEYS:
                            if secret:
                                raw_error = raw_error.replace(secret, "[REDACTED]")
                        last_error = raw_error[:220]
                        status = _http_status_from_error(exc, raw_error)

                        if status in retryable_status_codes and attempt < max_attempts_per_key_model:
                            delay = retry_delays_seconds[attempt - 1]
                            logger.warning(
                                "Gemini model %s / API key %d returned HTTP %s; retry %d/%d in %ds",
                                model_name, key_number, status, attempt, max_attempts_per_key_model, delay,
                            )
                            time.sleep(delay)
                            continue

                        logger.warning(
                            "Gemini model %s / API key %d failed (HTTP %s): %s",
                            model_name, key_number, status or "unknown", last_error,
                        )
                        break
            except Exception as exc:
                raw_error = str(exc)
                for secret in GEMINI_API_KEYS:
                    if secret:
                        raw_error = raw_error.replace(secret, "[REDACTED]")
                last_error = raw_error[:220]
                logger.warning(
                    "Could not initialize Gemini model %s with API key %d: %s",
                    model_name, key_number, last_error,
                )
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        logger.debug("Could not close Gemini client.")

            errors.append(f"{model_name} / API key {key_number}: {last_error}")

    models_tried = ", ".join(GEMINI_MODEL_CANDIDATES)
    raise RuntimeError(
        "All configured Gemini model/API-key combinations failed. "
        f"Models tried: {models_tried}. " + " | ".join(errors)
    )


# ---------------------------------------------------------------------------
# Reporting guidance and final result
# ---------------------------------------------------------------------------

def reporting_guidance(platform: str, result: dict[str, Any]) -> str:
    p = platform.lower()
    target = result["report_target"]
    status = result["account_status"]
    category = result["suggested_category"]

    if target == "NONE":
        return "Abhi report submit karne ki recommendation nahi hai. Agar baad mein clear evidence mile, tab app ke official Report option mein sachchi category choose karein."

    if "instagram" in p or p in {"ig", "insta"}:
        if target == "ACCOUNT" and status == "POSSIBLE_IMPERSONATION":
            return (
                "Instagram (manual): exact profile kholkar username verify karein → profile ke ⋯ menu → Report → "
                "Report account → impersonation/pretending-to-be-someone-else wala sachcha option choose karein. "
                "Official help: https://help.instagram.com/370054663112398"
            )
        if target == "NEEDS_REVIEW" and status in {"USER_REPORTED_ONLY", "INSUFFICIENT_EVIDENCE"}:
            return (
                "Abhi account ko fake report mat karein. Pehle check karein ki profile waqai kisi real person/business ki "
                "identity ya photos copy karke usi ke roop mein present kar raha hai ya nahi. Username, copied-photo evidence, "
                "original profile link aur date ke screenshots save karein. Confirm hone par hi Instagram ke account "
                "impersonation report route ko use karein: https://help.instagram.com/370054663112398"
            )
        return (
            f"Instagram (manual): jis exact comment/message/story/post par concern hai use kholkar uske Report option ko use karein; "
            f"app mein jo sabse accurate category {category!r} se match kare wahi choose karein. "
            "Agar concern bullying/harassment ka hai: https://help.instagram.com/547601325292351 | "
            "Comment report help: https://help.instagram.com/198034803689028/"
        )

    if "telegram" in p:
        if target == "ACCOUNT" and status == "POSSIBLE_IMPERSONATION":
            return (
                "Telegram: impersonation ke evidence ko save karein aur Telegram ke official support/report route ko use karein. "
                "Agar koi aapki identity copy karke aap ban raha hai, Telegram FAQ @NoToScam ka zikr karta hai. "
                "Message reporting: message par tap/hold ya right-click → Report → sachcha reason choose karein. "
                "Official FAQ: https://telegram.org/faq"
            )
        if target == "NEEDS_REVIEW" and status in {"USER_REPORTED_ONLY", "INSUFFICIENT_EVIDENCE"}:
            return (
                "Abhi account ko fake report mat karein. Pehle profile aur claimed real identity ka comparison karein aur "
                "original profile link, copied-photo proof aur dates ke screenshots rakhein. Agar aapki identity copy ki gayi "
                "hai tabhi official Telegram support route dekhein: https://telegram.org/faq"
            )
        return (
            "Telegram (manual): exact message par Android mein tap, iOS mein press-and-hold, "
            "Desktop/Web mein right-click → Report → jo accurate reason app dikhaye woh choose karein. "
            "Official FAQ: https://telegram.org/faq"
        )

    return (
        "Is platform ke app ke official Report/Help option ko hi use karein. Exact content ya account par report kholein "
        "aur sirf wahi category select karein jo sach mein evidence se match karti ho. Menu labels platform/version ke hisaab se badal sakte hain."
    )


def build_report(platform: str, account: str, context: str, result: dict[str, Any], key_number: int, model_used: str, image_count: int) -> str:
    issue_labels = {
        "harassment_bullying": "Bullying / harassment concern",
        "hate_or_discrimination": "Hate / discriminatory abuse concern",
        "threat": "Threat concern",
        "spam_or_scam": "Spam / scam concern",
        "privacy_violation": "Privacy violation concern",
        "sexual_harassment": "Sexual harassment concern",
        "impersonation": "Impersonation concern",
        "no_clear_violation": "No clear violation identified",
        "other": "Other / needs review",
    }
    account_labels = {
        "POSSIBLE_IMPERSONATION": "Possible impersonation — manual verification needed",
        "USER_REPORTED_ONLY": "User-reported suspicion — not independently verified",
        "NO_CLEAR_SIGN": "No clear impersonation signs in supplied material",
        "INSUFFICIENT_EVIDENCE": "Insufficient evidence to assess account authenticity",
    }
    evidence = "\n".join(f"  {i}. {item}" for i, item in enumerate(result["content_evidence"], 1))
    if not evidence:
        evidence = "  No clear screenshot/text excerpt was identified."
    missing = "\n".join(f"  - {item}" for item in result["missing_information"])
    if not missing:
        missing = "  No additional details requested by the AI."

    return (
        "AI CONTENT REVIEW (PRELIMINARY)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Platform: {platform}\n"
        f"Public profile reference: {account or 'Not provided'}\n"
        f"Evidence images reviewed: {image_count}\n"
        f"Your description: {context[:450] if context else 'Not provided'}\n\n"
        f"WHAT MAY HAVE HAPPENED: {issue_labels[result['issue_type']]}\n"
        f"Suggested report target: {result['report_target']}\n"
        f"Suggested in-app category: {result['suggested_category']}\n"
        f"Severity estimate: {result['severity']}\n"
        f"AI confidence: {result['confidence']}\n\n"
        "ACCOUNT / FAKE-ID CHECK (SEPARATE)\n"
        f"Assessment: {account_labels[result['account_status']]}\n"
        f"Account category: {result['account_category']}\n"
        f"Account evidence: {'; '.join(result['account_evidence']) or 'No concrete identity-copying evidence found.'}\n\n"
        "EVIDENCE REVIEWED\n"
        f"{evidence}\n\n"
        f"AI explanation: {result['explanation']}\n\n"
        f"WHAT TO DO NEXT: {result['next_step']}\n\n"
        f"MISSING DETAILS (if any):\n{missing}\n\n"
        "OFFICIAL REPORTING GUIDE (YOU SUBMIT IT MANUALLY)\n"
        f"{reporting_guidance(platform, result)}\n\n"
        "Important: Bot username/profile ko online open ya verify nahi karta. Low followers, new ID, anonymity, story post, ya ek gaali alone fake ID ka proof nahi hai. AI result platform ka final decision nahi hai. False ya coordinated reports na karein.\n"
        f"Gemini model: {model_used} | Google API key used: {key_number}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


# ---------------------------------------------------------------------------
# Telegram wizard
# ---------------------------------------------------------------------------

def ask(chat_id: int, text: str, handler: Any, *args: Any) -> None:
    sent = bot.send_message(chat_id, text)
    bot.register_next_step_handler(sent, handler, *args)


def start_review(message: telebot.types.Message, mode: str) -> None:
    if not is_authorized(message.from_user.id):
        deny(message)
        return
    intro = (
        "Fake/impersonation concern review" if mode == "fake" else "Content and account review"
    )
    ask(
        message.chat.id,
        f"{intro}\nStep 1/4: Platform name type karein (Instagram / Telegram / other).\n"
        "Cancel karne ke liye /cancel bhejein.",
        get_platform,
        mode,
    )


def get_platform(message: telebot.types.Message, mode: str) -> None:
    if not is_authorized(message.from_user.id):
        deny(message)
        return
    if is_cancel(message):
        bot.reply_to(message, "Review cancelled. Naya review shuru karne ke liye /analyze bhejein.")
        return
    if not message.text or not message.text.strip():
        ask(message.chat.id, "Platform ka naam type karein, jaise Instagram ya Telegram.", get_platform, mode)
        return
    platform = message.text.strip()[:60]
    ask(
        message.chat.id,
        "Step 2/4: Us account ka PUBLIC username ya profile link bhejein (jaise @username).\n"
        "Skip likh sakte hain. Password, OTP, phone number ya private login details na bhejein.",
        get_account,
        mode,
        platform,
    )


def get_account(message: telebot.types.Message, mode: str, platform: str) -> None:
    if not is_authorized(message.from_user.id):
        deny(message)
        return
    if is_cancel(message):
        bot.reply_to(message, "Review cancelled.")
        return
    if not message.text or not message.text.strip():
        ask(message.chat.id, "Public username/profile link type karein ya skip likhein.", get_account, mode, platform)
        return
    account = "" if message.text.strip().lower() == "skip" else message.text.strip()[:220]
    if mode == "fake":
        question = (
            "Step 3/4: Aapko fake/impersonation ka shak KIS BAAT se hua?\n"
            "Example: meri photo/name copy ki, kisi aur ki identity ban raha hai, ya sirf followers kam hain? "
            "Jo actually dekha wahi likhein. Sirf kam followers proof nahi hain."
        )
    else:
        question = (
            "Step 3/4: Kya hua? Apne words mein poori baat type karein.\n"
            "Example: 'is user ne comment mein mujhe gaali di', 'DM mein dhamki di', "
            "'story mein meri photo bina permission dali', ya 'meri identity copy ki'.\n"
            "Hindi / Hinglish / English sab chalega."
        )
    ask(message.chat.id, question, get_context, mode, platform, account)


def get_context(message: telebot.types.Message, mode: str, platform: str, account: str) -> None:
    if not is_authorized(message.from_user.id):
        deny(message)
        return
    if is_cancel(message):
        bot.reply_to(message, "Review cancelled.")
        return
    if not message.text or not message.text.strip():
        ask(message.chat.id, "Please type what happened in your own words.", get_context, mode, platform, account)
        return

    context = message.text.strip()[:MAX_CONTEXT_CHARS]
    ask(
        message.chat.id,
        "Step 4/4: Ab optional screenshot bhejein — profile, comment, chat, post ya story.\n"
        "Aap 1 se 4 images bhej sakte hain. Har image ke baad aur image bhejein, ya DONE/SKIP type karein.\n"
        "Agar screenshot nahi hai to SKIP likhein; AI aapke typed details se preliminary review karega.",
        get_evidence,
        mode,
        platform,
        account,
        context,
        [],
    )


def get_evidence(
    message: telebot.types.Message,
    mode: str,
    platform: str,
    account: str,
    context: str,
    images: list[Image.Image],
) -> None:
    if not is_authorized(message.from_user.id):
        deny(message)
        return
    if is_cancel(message):
        bot.reply_to(message, "Review cancelled.")
        return
    if is_skip(message):
        perform_review(message.chat.id, platform, account, context, images)
        return

    if image_file_id(message):
        try:
            image = download_image(message)
        except Exception as exc:
            ask(
                message.chat.id,
                f"Image receive nahi hui: {exc}\nDusri image bhejein ya SKIP likhein.",
                get_evidence,
                mode,
                platform,
                account,
                context,
                images,
            )
            return
        images = images + [image]
        if len(images) >= MAX_EVIDENCE_IMAGES:
            perform_review(message.chat.id, platform, account, context, images)
            return
        ask(
            message.chat.id,
            f"Screenshot {len(images)} receive ho gaya. Ek aur image bhejein, ya DONE likhein.",
            get_evidence,
            mode,
            platform,
            account,
            context,
            images,
        )
        return

    if message.text and message.text.strip():
        extra = message.text.strip()
        if extra.lower() not in {"done", "skip", "no", "none"}:
            context = (context + "\nAdditional user context: " + extra)[:MAX_CONTEXT_CHARS]
        perform_review(message.chat.id, platform, account, context, images)
        return

    ask(
        message.chat.id,
        "Screenshot bhejein, ya agar screenshot nahi hai to SKIP likhein.",
        get_evidence,
        mode,
        platform,
        account,
        context,
        images,
    )


def send_long_message(chat_id: int, text: str, limit: int = 3900) -> None:
    """Split long analysis reports under Telegram's message length limit."""
    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            bot.send_message(chat_id, remaining)
            return
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunk = remaining[:split_at].strip()
        if chunk:
            bot.send_message(chat_id, chunk)
        remaining = remaining[split_at:].strip()


def explain_api_failure(error: str) -> str:
    """Translate common Gemini API failures into useful, non-misleading advice."""
    lowered = error.lower()
    if "504" in lowered or "deadline_exceeded" in lowered:
        return (
            "Gemini 504 timeout: Google ka server request ko deadline ke andar complete nahi kar saka. "
            "Iska matlab zaroori nahi ki API key galat hai. Is build mein 180-second request timeout "
            "aur temporary errors ke liye automatic retry hai. 30-60 seconds baad dobara try karein; "
            "agar bahut screenshots bheje the to pehle sirf 1-2 clear screenshots ke saath test karein."
        )
    if "429" in lowered or "resource_exhausted" in lowered or "quota" in lowered:
        return (
            "Gemini quota/rate limit hit hui hai. Thodi der baad retry karein. Do API keys agar ek hi "
            "Google AI Studio project ki hain to dono project quota share karti hain."
        )
    if "api_key_invalid" in lowered or "api key not valid" in lowered or "401" in lowered or "403" in lowered:
        return (
            "Google API key invalid ya permission issue lag raha hai. Railway Variables mein dono keys "
            "ke naam/values check karein; key ke aage-peeche spaces na hon aur Gemini API access enabled ho."
        )
    if "503" in lowered or "service_unavailable" in lowered:
        return (
            "503 UNAVAILABLE ka matlab selected Gemini model ki capacity abhi busy hai; ye aam taur par API key ka error nahi hai. "
            "Ab bot primary model ke baad GEMINI_FALLBACK_MODELS mein diye stable Flash models ko bhi try karta hai, "
            "aur har model par dono Google API keys use karta hai. API keys regenerate karne ki zaroorat nahi. "
            "Agar sabhi models par 503 aaye, kuch der baad dobara try karein; Google ki service-side capacity theek hone ka wait karna hoga."
        )
    if "502" in lowered:
        return "Google Gemini gateway temporarily unavailable hai. Bot ne retry kiya; kuch der baad dobara try karein."
    return (
        "Railway > Deployments > latest deployment > View Logs mein poora error dekhein. "
        "API key ko chat mein kabhi share na karein."
    )


def perform_review(chat_id: int, platform: str, account: str, context: str, images: list[Image.Image]) -> None:
    progress = bot.send_message(
        chat_id,
        f"AI review chal raha hai… typed details aur {len(images)} screenshot(s) check kar raha hoon.\n"
        "Gemini ko 1-3 minute lag sakte hain; beech mein /analyze dobara na bhejein.\n"
        "Bot account ko online open nahi karta; sirf aapke diye details dekhta hai.",
    )
    try:
        result, key_number, model_used = run_gemini_review(platform, account, context, images)
        report = build_report(platform, account, context, result, key_number, model_used, len(images))
        send_long_message(chat_id, report)
    except Exception as exc:
        logger.exception("Review failed")
        error = str(exc)
        for secret in GEMINI_API_KEYS:
            error = error.replace(secret, "[REDACTED]")
        bot.send_message(
            chat_id,
            "AI review complete nahi ho saka. Main fake result nahi dunga.\n\n"
            f"Error: {error[:500]}\n\n"
            f"Kya karein: {explain_api_failure(error)}",
        )
    finally:
        try:
            bot.delete_message(chat_id, progress.message_id)
        except Exception:
            logger.debug("Could not delete progress message.")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@bot.message_handler(commands=["myid"])
def my_id(message: telebot.types.Message) -> None:
    bot.reply_to(message, f"Your Telegram numeric ID: {message.from_user.id}")


@bot.message_handler(commands=["start", "help"])
def help_message(message: telebot.types.Message) -> None:
    if not is_authorized(message.from_user.id):
        deny(message)
        return
    bot.reply_to(
        message,
        "AI Evidence Review Bot ready.\n\n"
        "/analyze — username + aapki typed explanation + optional screenshots/story.\n"
        "/fakeid — suspected fake/impersonation account ka separate review.\n"
        "/cancel — current step cancel.\n"
        "/myid — apna Telegram numeric ID.\n\n"
        "Bot profile ko khud online search nahi karta aur report submit nahi karta. "
        "Ye sirf evidence dekhkar truthful category aur manual official steps suggest karta hai."
    )


@bot.message_handler(commands=["analyze"])
def analyze_command(message: telebot.types.Message) -> None:
    start_review(message, "general")


@bot.message_handler(commands=["fakeid"])
def fakeid_command(message: telebot.types.Message) -> None:
    start_review(message, "fake")


@bot.message_handler(commands=["cancel"])
def cancel_command(message: telebot.types.Message) -> None:
    if not is_authorized(message.from_user.id):
        deny(message)
        return
    bot.reply_to(message, "Cancelled. /analyze ya /fakeid se dobara shuru karein.")


if __name__ == "__main__":
    logger.info("AI evidence-review bot starting")
    logger.info("Authorized users configured: %d", len(AUTHORIZED_USERS))
    logger.info("Gemini models: %s | API keys configured: %d | timeout: %d ms", ",".join(GEMINI_MODEL_CANDIDATES), len(GEMINI_API_KEYS), GEMINI_TIMEOUT_MS)
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
