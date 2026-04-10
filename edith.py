#!/usr/bin/env python3
"""
EDITH - Enhanced Defense Intelligence for orko-server
Powered by Google Gemini (google-genai SDK)
"""

import os
import sys
import json
import asyncio
import subprocess
import logging
import datetime
import psutil
import re
from pathlib import Path

from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
CONFIG_FILE = Path.home() / ".edith" / "config.json"

def load_config():
    if not CONFIG_FILE.exists():
        print(f"[ERROR] Config not found at {CONFIG_FILE}")
        print("Run: python3 edith_setup.py")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)

CFG = load_config()

TELEGRAM_TOKEN   = CFG["telegram_token"]
ALLOWED_USER_IDS = set(CFG["allowed_user_ids"])
GEMINI_API_KEY   = CFG["gemini_api_key"]
DEFAULT_MODEL    = CFG.get("default_model", "gemini-2.5-flash-lite")
LOG_FILE         = Path.home() / ".edith" / "edith.log"

# Model priority list — free tier friendly
AVAILABLE_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
]

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("EDITH")

# ─────────────────────────────────────────────
#  GEMINI CLIENT
# ─────────────────────────────────────────────
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """You are EDITH (Enhanced Defense Intelligence for Terminal & Hardware),
a powerful AI agent running on a Ubuntu home server called orko-server.
You have full sudo access and can run any shell command.

Personality: Confident, precise, like Tony Stark's EDITH. Occasionally witty but always professional.
Respond in the same language the user uses (Bangla or English).

When given a task, respond ONLY with one of these JSON formats:

To run commands:
{"action": "execute", "commands": ["cmd1", "cmd2"], "explanation": "what you are doing"}

To reply with text only:
{"action": "reply", "message": "your response"}

To ask confirmation before destructive operations:
{"action": "confirm", "message": "describe what you will do", "commands": ["cmd1"]}

Rules:
- Always use sudo for system commands
- If a command fails, try an alternative approach
- Be concise but informative
- For package installs always use: DEBIAN_FRONTEND=noninteractive sudo apt-get install -y
- Prefer systemctl for service management
- For security tasks be thorough
"""

# ─────────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────────
current_model = DEFAULT_MODEL
conversation_history = {}  # user_id -> list of {"role": ..., "parts": ...}
pending_confirm = {}       # user_id -> commands list

# ─────────────────────────────────────────────
#  SECURITY
# ─────────────────────────────────────────────
def is_authorized(user_id: int) -> bool:
    return user_id in ALLOWED_USER_IDS

def security_check(update: Update) -> bool:
    uid = update.effective_user.id
    if not is_authorized(uid):
        log.warning(f"UNAUTHORIZED: user_id={uid} username={update.effective_user.username}")
        return False
    return True

# ─────────────────────────────────────────────
#  SHELL EXECUTOR
# ─────────────────────────────────────────────
async def run_command(cmd: str, timeout: int = 90) -> dict:
    log.info(f"EXEC: {cmd}")
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "success": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": stdout.decode(errors="replace").strip(),
            "stderr": stderr.decode(errors="replace").strip(),
            "cmd": cmd
        }
    except asyncio.TimeoutError:
        return {"success": False, "returncode": -1, "stdout": "", "stderr": f"Timed out after {timeout}s", "cmd": cmd}
    except Exception as e:
        return {"success": False, "returncode": -1, "stdout": "", "stderr": str(e), "cmd": cmd}

async def run_commands(commands: list) -> list:
    results = []
    for cmd in commands:
        r = await run_command(cmd)
        results.append(r)
        if not r["success"]:
            log.warning(f"Failed: {cmd} | {r['stderr']}")
    return results

def format_results(results: list) -> str:
    out = []
    for r in results:
        icon = "✅" if r["success"] else "❌"
        out.append(f"{icon} `{r['cmd']}`")
        if r["stdout"]:
            out.append(f"```\n{r['stdout'][:600]}\n```")
        if not r["success"] and r["stderr"]:
            out.append(f"⚠️ `{r['stderr'][:200]}`")
    return "\n".join(out)

# ─────────────────────────────────────────────
#  GEMINI AI BRAIN
# ─────────────────────────────────────────────
async def ask_gemini(user_id: int, user_message: str, context_data: str = "") -> dict:
    global current_model

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    full_message = user_message
    if context_data:
        full_message += f"\n\n[System Context]\n{context_data}"

    # Add to history
    conversation_history[user_id].append(
        types.Content(role="user", parts=[types.Part(text=full_message)])
    )

    # Keep last 16 turns
    history = conversation_history[user_id][-16:]

    # Try models in order
    models_to_try = [current_model] + [m for m in AVAILABLE_MODELS if m != current_model]

    for model_name in models_to_try:
        try:
            response = gemini_client.models.generate_content(
                model=model_name,
                contents=history,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.3,
                    max_output_tokens=1500,
                )
            )
            reply_text = response.text.strip()

            # Save assistant reply to history
            conversation_history[user_id].append(
                types.Content(role="model", parts=[types.Part(text=reply_text)])
            )

            # Parse JSON
            json_match = re.search(r'\{.*\}', reply_text, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group())
                    parsed["_model"] = model_name
                    return parsed
                except json.JSONDecodeError:
                    pass

            return {"action": "reply", "message": reply_text, "_model": model_name}

        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                log.warning(f"Quota exceeded for {model_name}, trying next...")
                continue
            log.warning(f"Model {model_name} error: {e}")
            continue

    return {"action": "reply", "message": "⚠️ সব Gemini model এ quota শেষ বা error। কিছুক্ষণ পর try করুন।", "_model": "none"}

# ─────────────────────────────────────────────
#  SYSTEM INFO
# ─────────────────────────────────────────────
def get_system_snapshot() -> str:
    try:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        boot = datetime.datetime.fromtimestamp(psutil.boot_time())
        uptime = str(datetime.datetime.now() - boot).split('.')[0]
        return (f"CPU:{cpu}% RAM:{mem.percent}% "
                f"({mem.used//1024//1024}MB/{mem.total//1024//1024}MB) "
                f"Disk:{disk.percent}% Uptime:{uptime}")
    except:
        return "System info unavailable"

# ─────────────────────────────────────────────
#  TELEGRAM COMMAND HANDLERS
# ─────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        return
    await update.message.reply_text(
        "🦾 *EDITH Online*\n_Even Dead, I'm The Hero_\n\n"
        f"🖥 Server: `orko-server`\n"
        f"🧠 Model: `{current_model}`\n"
        f"📊 `{get_system_snapshot()}`\n\n"
        "Just tell me what to do.\n\n"
        "/status /security /logs /model /help",
        parse_mode="Markdown"
    )

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        return
    await update.message.reply_text("🔍 Gathering intel...")
    results = await run_commands([
        "uptime",
        "free -h",
        "df -h /",
        "sudo systemctl list-units --state=failed --no-pager --no-legend | head -10",
        "sudo ufw status",
        "sudo fail2ban-client status 2>/dev/null | head -10 || echo 'fail2ban not active'",
        "who",
        "last -n 5 --time-format iso",
    ])
    await update.message.reply_text(
        "📊 *orko-server Status*\n\n" + format_results(results),
        parse_mode="Markdown"
    )

async def cmd_security(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        return
    await update.message.reply_text("🛡️ Running security audit...")
    results = await run_commands([
        "sudo apt list --upgradable 2>/dev/null | grep -v Listing | head -15",
        "sudo ss -tlnp",
        "sudo ufw status verbose",
        "sudo fail2ban-client status 2>/dev/null || echo 'fail2ban not installed'",
        "sudo grep 'Failed password' /var/log/auth.log 2>/dev/null | tail -8 || echo 'No failed logins found'",
        "last -n 8 --time-format iso",
    ])
    result_text = "\n".join([f"CMD: {r['cmd']}\n{r['stdout']}" for r in results])

    await update.message.reply_text(
        "🛡️ *Security Audit*\n\n" + format_results(results),
        parse_mode="Markdown"
    )

    ai = await ask_gemini(update.effective_user.id,
        "Analyze this security scan briefly. Any threats or recommendations?",
        result_text[:2000])
    if ai.get("action") == "reply":
        await update.message.reply_text(
            f"🤖 *EDITH Analysis:*\n\n{ai['message'][:1500]}",
            parse_mode="Markdown"
        )

async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        return
    await update.message.reply_text("📋 Analyzing logs...")
    results = await run_commands([
        "sudo journalctl -n 40 --no-pager -p err",
    ])
    log_text = results[0]["stdout"] if results else ""
    ai = await ask_gemini(update.effective_user.id,
        "Analyze these server logs. Any errors or issues?",
        log_text[:2500])
    if ai.get("action") == "reply":
        await update.message.reply_text(
            f"📋 *Log Analysis:*\n\n{ai['message'][:3000]}",
            parse_mode="Markdown"
        )

async def cmd_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        return
    global current_model
    args = ctx.args
    if not args:
        lines = "\n".join([
            f"{'→' if m == current_model else '  '} `{m}`"
            for m in AVAILABLE_MODELS
        ])
        await update.message.reply_text(
            f"🧠 *Gemini Models*\n\nCurrent: `{current_model}`\n\n{lines}\n\n"
            f"Usage: `/model gemini-1.5-flash`",
            parse_mode="Markdown"
        )
        return
    new_model = args[0]
    if new_model in AVAILABLE_MODELS:
        current_model = new_model
        CFG["default_model"] = new_model
        with open(CONFIG_FILE, "w") as f:
            json.dump(CFG, f, indent=2)
        await update.message.reply_text(f"✅ Switched to `{new_model}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Unknown model. Use `/model` to see list.", parse_mode="Markdown")

async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        return
    conversation_history[update.effective_user.id] = []
    await update.message.reply_text("🧹 Memory cleared.")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        return
    await update.message.reply_text(
        "🦾 *EDITH Commands*\n\n"
        "/start — Wake EDITH\n"
        "/status — System status\n"
        "/security — Security audit\n"
        "/logs — Log analysis\n"
        "/model — Switch AI model\n"
        "/clear — Clear AI memory\n"
        "/help — This menu\n\n"
        "*Just talk to me:*\n"
        "`install nginx`\n"
        "`block IP 1.2.3.4`\n"
        "`restart docker`\n"
        "`update all packages`\n"
        "`show who is logged in`\n"
        "`enable fail2ban`\n"
        "`check open ports`\n"
        "`show top processes`\n\n"
        "_Bangla তেও বলতে পারো।_",
        parse_mode="Markdown"
    )

# ─────────────────────────────────────────────
#  MAIN MESSAGE HANDLER
# ─────────────────────────────────────────────
async def handle_confirm(update: Update, uid: int, text: str):
    text_l = text.lower().strip()
    yes_words = {"yes", "y", "হ্যাঁ", "ha", "haan", "ok", "করো", "koro", "hya"}
    no_words  = {"no", "n", "না", "na", "nope", "cancel", "বাতিল"}

    if text_l in yes_words:
        commands = pending_confirm.pop(uid)
        await update.message.reply_text("⚡ Executing...")
        results = await run_commands(commands)
        await update.message.reply_text(format_results(results)[:3000], parse_mode="Markdown")

        # AI summary
        ai = await ask_gemini(uid, "Briefly summarize what was done and if it succeeded:", str(results))
        if ai.get("action") == "reply":
            await update.message.reply_text(f"🤖 {ai['message'][:500]}", parse_mode="Markdown")

    elif text_l in no_words:
        pending_confirm.pop(uid)
        await update.message.reply_text("❌ Cancelled.")
    else:
        await update.message.reply_text("Reply *yes* or *no*.", parse_mode="Markdown")

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        await update.message.reply_text("🔒 Access Denied.")
        return

    uid = update.effective_user.id
    text = update.message.text

    if uid in pending_confirm:
        await handle_confirm(update, uid, text)
        return

    thinking_msg = await update.message.reply_text("⚡ Processing...")

    snapshot = get_system_snapshot()
    ai = await ask_gemini(uid, text, f"Server state: {snapshot}")

    action = ai.get("action", "reply")
    model_used = ai.get("_model", current_model)

    try:
        await thinking_msg.delete()
    except:
        pass

    if action == "reply":
        await update.message.reply_text(
            f"{ai.get('message', '')}\n\n_🧠 {model_used}_",
            parse_mode="Markdown"
        )

    elif action == "execute":
        commands = ai.get("commands", [])
        explanation = ai.get("explanation", "Running commands")

        if not commands:
            await update.message.reply_text("⚠️ No commands to run.")
            return

        await update.message.reply_text(f"🔧 *{explanation}*", parse_mode="Markdown")
        results = await run_commands(commands)
        failed = [r for r in results if not r["success"]]

        await update.message.reply_text(format_results(results)[:3500], parse_mode="Markdown")

        # Auto-retry failed commands
        if failed:
            failed_info = "\n".join([f"Failed: {r['cmd']}\nError: {r['stderr']}" for r in failed])
            retry_ai = await ask_gemini(uid,
                f"These commands failed. Give alternative commands to fix it:\n{failed_info}")

            if retry_ai.get("action") == "execute":
                retry_cmds = retry_ai.get("commands", [])
                await update.message.reply_text(
                    f"🔄 *Retrying with alternative approach...*",
                    parse_mode="Markdown"
                )
                retry_results = await run_commands(retry_cmds)
                await update.message.reply_text(format_results(retry_results)[:2000], parse_mode="Markdown")
            elif retry_ai.get("action") == "reply":
                await update.message.reply_text(
                    f"🤖 {retry_ai.get('message', '')[:500]}",
                    parse_mode="Markdown"
                )

    elif action == "confirm":
        commands = ai.get("commands", [])
        message = ai.get("message", "")
        pending_confirm[uid] = commands
        cmd_preview = "\n".join([f"`{c}`" for c in commands])
        await update.message.reply_text(
            f"⚠️ *Confirmation Required*\n\n{message}\n\nCommands:\n{cmd_preview}\n\nReply *yes* or *no*.",
            parse_mode="Markdown"
        )

# ─────────────────────────────────────────────
#  BACKGROUND MONITOR
# ─────────────────────────────────────────────
async def background_monitor(app: Application):
    await asyncio.sleep(120)
    while True:
        try:
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            cpu = psutil.cpu_percent(interval=3)
            alerts = []
            if mem.percent > 90:
                alerts.append(f"🔴 RAM: {mem.percent}%")
            if disk.percent > 85:
                alerts.append(f"🔴 Disk: {disk.percent}%")
            if cpu > 95:
                alerts.append(f"🔴 CPU: {cpu}%")
            if alerts:
                for uid in ALLOWED_USER_IDS:
                    try:
                        await app.bot.send_message(
                            chat_id=uid,
                            text="🚨 *EDITH Alert — orko-server*\n\n" + "\n".join(alerts),
                            parse_mode="Markdown"
                        )
                    except:
                        pass
        except Exception as e:
            log.error(f"Monitor error: {e}")
        await asyncio.sleep(1800)

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    log.info("EDITH starting...")
    log.info(f"Authorized users: {ALLOWED_USER_IDS}")
    log.info(f"Default model: {current_model}")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("security", cmd_security))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    async def post_init(app):
        asyncio.create_task(background_monitor(app))
    app.post_init = post_init

    log.info("EDITH is online! 🦾")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
