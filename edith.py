#!/usr/bin/env python3
"""
EDITH - Enhanced Defense Intelligence for orko-server
Your personal AI server guardian. Powered by Gemini.
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
from typing import Optional

import google.generativeai as genai
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

TELEGRAM_TOKEN      = CFG["telegram_token"]
ALLOWED_USER_IDS    = set(CFG["allowed_user_ids"])   # list of int
GEMINI_API_KEY      = CFG["gemini_api_key"]
DEFAULT_MODEL       = CFG.get("default_model", "gemini-2.0-flash")
LOG_FILE            = Path.home() / ".edith" / "edith.log"

AVAILABLE_MODELS = [
    "gemini-2.5-flash-preview-04-17",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
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
#  GEMINI SETUP
# ─────────────────────────────────────────────
genai.configure(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """You are EDITH (Enhanced Defense Intelligence for Terminal & Hardware), 
a powerful AI agent running on a Ubuntu home server called orko-server.
You have full sudo access and can run any shell command.

Your personality: Confident, precise, like Tony Stark's EDITH. Occasionally witty but always professional.
You respond in the same language the user uses (Bangla or English).

Your capabilities:
- Execute shell commands (always use sudo when needed)
- Install/remove packages (apt)
- Manage services (systemctl)
- Configure firewall (ufw)
- Manage fail2ban
- Monitor system resources
- Analyze logs
- Perform security tasks
- Automate server maintenance

When given a task:
1. Think step by step
2. Generate the shell commands needed
3. If something fails, try an alternative approach
4. Always report results clearly

When you need to run commands, respond ONLY with this JSON format:
{"action": "execute", "commands": ["cmd1", "cmd2"], "explanation": "what you're doing"}

When you just want to reply with text (no commands):
{"action": "reply", "message": "your response here"}

When you need to ask for confirmation before doing something destructive:
{"action": "confirm", "message": "what you want to do", "commands": ["cmd1"]}

IMPORTANT: 
- Always prefer non-destructive approaches first
- For security tasks, be thorough
- If a command fails, automatically retry with alternative
- Keep responses concise but informative
"""

# ─────────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────────
current_model = DEFAULT_MODEL
conversation_history = {}   # user_id -> list of messages
pending_confirm = {}        # user_id -> commands to confirm

# ─────────────────────────────────────────────
#  SECURITY GUARD
# ─────────────────────────────────────────────
def is_authorized(user_id: int) -> bool:
    return user_id in ALLOWED_USER_IDS

def security_check(update: Update) -> bool:
    uid = update.effective_user.id
    if not is_authorized(uid):
        log.warning(f"UNAUTHORIZED ACCESS ATTEMPT: user_id={uid} username={update.effective_user.username}")
        return False
    return True

# ─────────────────────────────────────────────
#  SHELL EXECUTOR
# ─────────────────────────────────────────────
async def run_command(cmd: str, timeout: int = 60) -> dict:
    """Run a shell command and return result."""
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
        return {"success": False, "returncode": -1, "stdout": "", "stderr": f"Command timed out after {timeout}s", "cmd": cmd}
    except Exception as e:
        return {"success": False, "returncode": -1, "stdout": "", "stderr": str(e), "cmd": cmd}

async def run_commands_with_retry(commands: list, max_retries: int = 2) -> list:
    """Run commands, retry on failure with AI fallback."""
    results = []
    for cmd in commands:
        result = await run_command(cmd)
        results.append(result)
        if not result["success"]:
            log.warning(f"Command failed: {cmd} | stderr: {result['stderr']}")
    return results

def format_results(results: list) -> str:
    """Format command results for Telegram."""
    out = []
    for r in results:
        status = "✅" if r["success"] else "❌"
        out.append(f"{status} `{r['cmd']}`")
        if r["stdout"]:
            out.append(f"```\n{r['stdout'][:800]}\n```")
        if not r["success"] and r["stderr"]:
            out.append(f"⚠️ `{r['stderr'][:300]}`")
    return "\n".join(out)

# ─────────────────────────────────────────────
#  GEMINI AI BRAIN
# ─────────────────────────────────────────────
async def ask_gemini(user_id: int, user_message: str, context_data: str = "") -> dict:
    """Send message to Gemini and get structured response."""
    global current_model

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    full_message = user_message
    if context_data:
        full_message += f"\n\n[System Context]\n{context_data}"

    conversation_history[user_id].append({"role": "user", "parts": [full_message]})

    # Keep last 20 messages to avoid token overflow
    history = conversation_history[user_id][-20:]

    for model_name in [current_model] + [m for m in AVAILABLE_MODELS if m != current_model]:
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=SYSTEM_PROMPT
            )
            chat = model.start_chat(history=history[:-1])
            response = chat.send_message(full_message)
            reply_text = response.text.strip()

            conversation_history[user_id].append({"role": "model", "parts": [reply_text]})

            # Parse JSON response
            json_match = re.search(r'\{.*\}', reply_text, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group())
                    parsed["_model_used"] = model_name
                    return parsed
                except json.JSONDecodeError:
                    pass

            return {"action": "reply", "message": reply_text, "_model_used": model_name}

        except Exception as e:
            log.warning(f"Model {model_name} failed: {e}")
            continue

    return {"action": "reply", "message": "⚠️ All Gemini models failed. Please check your API key.", "_model_used": "none"}

# ─────────────────────────────────────────────
#  SYSTEM INFO COLLECTOR
# ─────────────────────────────────────────────
def get_system_snapshot() -> str:
    """Collect quick system stats."""
    try:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        boot = datetime.datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.datetime.now() - boot

        return (
            f"CPU: {cpu}% | "
            f"RAM: {mem.percent}% ({mem.used//1024//1024}MB/{mem.total//1024//1024}MB) | "
            f"Disk: {disk.percent}% used | "
            f"Uptime: {str(uptime).split('.')[0]}"
        )
    except Exception as e:
        return f"Could not collect system info: {e}"

# ─────────────────────────────────────────────
#  TELEGRAM HANDLERS
# ─────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        return
    await update.message.reply_text(
        "🦾 *EDITH Online*\n\n"
        "Even Dead, I'm The Hero.\n\n"
        f"Server: `orko-server`\n"
        f"Model: `{current_model}`\n"
        f"Status: `{get_system_snapshot()}`\n\n"
        "Just tell me what to do. I'll handle the rest.\n\n"
        "Commands:\n"
        "/status — System overview\n"
        "/model — Switch Gemini model\n"
        "/security — Run security audit\n"
        "/logs — Recent log analysis\n"
        "/clear — Clear conversation\n"
        "/help — Full command list",
        parse_mode="Markdown"
    )

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        return
    await update.message.reply_text("🔍 Gathering intel...", parse_mode="Markdown")

    commands = [
        "uptime",
        "free -h",
        "df -h /",
        "sudo systemctl list-units --state=failed --no-pager --no-legend | head -10",
        "sudo ufw status",
        "sudo fail2ban-client status 2>/dev/null | head -20 || echo 'fail2ban not running'",
        "who",
        "last -n 5 --time-format iso",
    ]
    results = await run_commands_with_retry(commands)
    text = "📊 *System Status — orko-server*\n\n" + format_results(results)
    await update.message.reply_text(text[:4000], parse_mode="Markdown")

async def cmd_security(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        return
    await update.message.reply_text("🛡️ *Running security audit...*", parse_mode="Markdown")

    commands = [
        "sudo apt list --upgradable 2>/dev/null | head -20",
        "sudo ss -tlnp",
        "sudo fail2ban-client status 2>/dev/null || echo 'fail2ban not installed'",
        "sudo ufw status verbose",
        "sudo last -n 10 --time-format iso",
        "sudo grep 'Failed password' /var/log/auth.log 2>/dev/null | tail -10 || sudo grep 'Failed password' /var/log/secure 2>/dev/null | tail -10 || echo 'No auth log found'",
        "sudo find /tmp -type f -newer /tmp -mmin -60 2>/dev/null | head -10",
    ]
    results = await run_commands_with_retry(commands)

    # Ask Gemini to analyze
    result_text = "\n".join([f"{r['cmd']}: {r['stdout']}" for r in results])
    ai_response = await ask_gemini(
        update.effective_user.id,
        "Analyze this security scan and give me a brief threat assessment with recommendations:",
        result_text
    )

    text = "🛡️ *Security Audit*\n\n" + format_results(results)
    await update.message.reply_text(text[:3500], parse_mode="Markdown")

    if ai_response.get("action") == "reply":
        await update.message.reply_text(
            f"🤖 *EDITH Analysis:*\n\n{ai_response.get('message', '')[:1500]}",
            parse_mode="Markdown"
        )

async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        return
    await update.message.reply_text("📋 *Analyzing logs...*", parse_mode="Markdown")

    commands = [
        "sudo journalctl -n 50 --no-pager -p err",
        "sudo tail -n 30 /var/log/syslog 2>/dev/null || sudo journalctl -n 30 --no-pager",
    ]
    results = await run_commands_with_retry(commands)
    result_text = "\n".join([r["stdout"] for r in results])

    ai_response = await ask_gemini(
        update.effective_user.id,
        "Analyze these server logs. Identify any errors, warnings, or security issues:",
        result_text[:3000]
    )

    if ai_response.get("action") == "reply":
        await update.message.reply_text(
            f"📋 *Log Analysis:*\n\n{ai_response.get('message', '')[:3000]}",
            parse_mode="Markdown"
        )

async def cmd_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        return
    global current_model
    args = ctx.args
    if not args:
        models_list = "\n".join([f"{'→' if m == current_model else '  '} `{m}`" for m in AVAILABLE_MODELS])
        await update.message.reply_text(
            f"🧠 *Gemini Models*\n\nCurrent: `{current_model}`\n\nAvailable:\n{models_list}\n\n"
            f"Usage: `/model gemini-2.5-flash-preview-04-17`",
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
        await update.message.reply_text(f"❌ Unknown model. Use `/model` to see options.", parse_mode="Markdown")

async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        return
    uid = update.effective_user.id
    conversation_history[uid] = []
    await update.message.reply_text("🧹 Conversation cleared. Fresh start.", parse_mode="Markdown")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        return
    await update.message.reply_text(
        "🦾 *EDITH — Command Reference*\n\n"
        "*Built-in Commands:*\n"
        "/start — Wake EDITH\n"
        "/status — Full system status\n"
        "/security — Security audit\n"
        "/logs — Log analysis\n"
        "/model — Switch Gemini model\n"
        "/clear — Clear AI memory\n"
        "/help — This menu\n\n"
        "*Just talk to me:*\n"
        "› `install nginx`\n"
        "› `block IP 1.2.3.4`\n"
        "› `restart docker`\n"
        "› `show disk usage`\n"
        "› `update all packages`\n"
        "› `who is logged in`\n"
        "› `enable fail2ban`\n"
        "› `check if port 8080 is open`\n\n"
        "_I understand natural language. Just tell me what you need._",
        parse_mode="Markdown"
    )

async def handle_confirm(update: Update, uid: int, text: str):
    """Handle yes/no confirmation."""
    text_lower = text.lower().strip()
    if text_lower in ["yes", "y", "হ্যাঁ", "ha", "haan", "ok", "করো", "koro"]:
        commands = pending_confirm.pop(uid)
        await update.message.reply_text("⚡ Executing...", parse_mode="Markdown")
        results = await run_commands_with_retry(commands)
        text_out = "✅ *Done!*\n\n" + format_results(results)

        # Ask AI to summarize result
        ai_resp = await ask_gemini(uid, "Summarize what happened:", str(results))
        await update.message.reply_text(text_out[:3000], parse_mode="Markdown")
        if ai_resp.get("action") == "reply":
            await update.message.reply_text(f"🤖 {ai_resp['message'][:500]}", parse_mode="Markdown")

    elif text_lower in ["no", "n", "না", "na", "nope", "cancel", "বাতিল"]:
        pending_confirm.pop(uid)
        await update.message.reply_text("❌ Cancelled.", parse_mode="Markdown")
    else:
        await update.message.reply_text("Please reply *yes* or *no*.", parse_mode="Markdown")

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not security_check(update):
        await update.message.reply_text("🔒 Access Denied. You are not authorized.")
        return

    uid = update.effective_user.id
    text = update.message.text

    # Handle pending confirmation
    if uid in pending_confirm:
        await handle_confirm(update, uid, text)
        return

    await update.message.reply_text("⚡ Processing...", parse_mode="Markdown")

    # Add system snapshot to context
    snapshot = get_system_snapshot()
    ai_response = await ask_gemini(uid, text, f"Current system state: {snapshot}")

    action = ai_response.get("action", "reply")
    model_used = ai_response.get("_model_used", current_model)

    if action == "reply":
        msg = ai_response.get("message", "No response.")
        await update.message.reply_text(
            f"{msg}\n\n_Model: {model_used}_",
            parse_mode="Markdown"
        )

    elif action == "execute":
        commands = ai_response.get("commands", [])
        explanation = ai_response.get("explanation", "")

        if not commands:
            await update.message.reply_text("⚠️ No commands generated.", parse_mode="Markdown")
            return

        await update.message.reply_text(
            f"🔧 *{explanation}*\n\nRunning {len(commands)} command(s)...",
            parse_mode="Markdown"
        )

        results = await run_commands_with_retry(commands)
        failed = [r for r in results if not r["success"]]

        text_out = format_results(results)
        await update.message.reply_text(text_out[:3500], parse_mode="Markdown")

        # If any failed, ask AI for retry
        if failed:
            failed_info = "\n".join([f"Failed: {r['cmd']}\nError: {r['stderr']}" for r in failed])
            retry_response = await ask_gemini(
                uid,
                f"These commands failed. Suggest alternative approach:\n{failed_info}"
            )
            if retry_response.get("action") == "execute":
                retry_cmds = retry_response.get("commands", [])
                await update.message.reply_text(
                    f"🔄 *Retrying with alternative approach...*\n`{retry_cmds}`",
                    parse_mode="Markdown"
                )
                retry_results = await run_commands_with_retry(retry_cmds)
                await update.message.reply_text(format_results(retry_results)[:2000], parse_mode="Markdown")
            elif retry_response.get("action") == "reply":
                await update.message.reply_text(
                    f"🤖 *EDITH:* {retry_response.get('message', '')[:500]}",
                    parse_mode="Markdown"
                )

    elif action == "confirm":
        commands = ai_response.get("commands", [])
        message = ai_response.get("message", "")
        pending_confirm[uid] = commands
        cmd_preview = "\n".join([f"`{c}`" for c in commands])
        await update.message.reply_text(
            f"⚠️ *Confirmation Required*\n\n{message}\n\nCommands to run:\n{cmd_preview}\n\nReply *yes* to proceed or *no* to cancel.",
            parse_mode="Markdown"
        )

# ─────────────────────────────────────────────
#  BACKGROUND TASKS
# ─────────────────────────────────────────────
async def background_monitor(app: Application):
    """Periodic health check every 30 minutes."""
    await asyncio.sleep(60)  # Wait 1 min after startup
    while True:
        try:
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            cpu = psutil.cpu_percent(interval=2)

            alerts = []
            if mem.percent > 90:
                alerts.append(f"🔴 *HIGH RAM:* {mem.percent}%")
            if disk.percent > 85:
                alerts.append(f"🔴 *HIGH DISK:* {disk.percent}%")
            if cpu > 95:
                alerts.append(f"🔴 *HIGH CPU:* {cpu}%")

            if alerts:
                for uid in ALLOWED_USER_IDS:
                    try:
                        await app.bot.send_message(
                            chat_id=uid,
                            text="🚨 *EDITH Alert — orko-server*\n\n" + "\n".join(alerts),
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass

        except Exception as e:
            log.error(f"Monitor error: {e}")

        await asyncio.sleep(1800)  # 30 minutes

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    log.info("EDITH starting up...")
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

    # Background monitor
    async def post_init(app):
        asyncio.create_task(background_monitor(app))

    app.post_init = post_init

    log.info("EDITH is online. Listening for commands...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
