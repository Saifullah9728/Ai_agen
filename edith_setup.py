#!/usr/bin/env python3
"""
EDITH Setup — First-time configuration wizard
"""

import json
import os
import sys
import subprocess
from pathlib import Path

CONFIG_DIR = Path.home() / ".edith"
CONFIG_FILE = CONFIG_DIR / "config.json"

AVAILABLE_MODELS = [
    "gemini-2.5-flash-preview-04-17",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]

def print_banner():
    print("""
╔═══════════════════════════════════════════╗
║   EDITH — Setup Wizard                   ║
║   Enhanced Defense Intelligence          ║
║   for orko-server                        ║
╚═══════════════════════════════════════════╝
""")

def install_dependencies():
    print("[*] Installing Python dependencies...")
    packages = [
        "python-telegram-bot",
        "google-generativeai",
        "psutil",
    ]
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--quiet", "--break-system-packages"
    ] + packages)
    print("[✓] Dependencies installed.\n")

def get_input(prompt, default=None, secret=False):
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "

    if secret:
        import getpass
        val = getpass.getpass(prompt)
    else:
        val = input(prompt).strip()

    if not val and default:
        return default
    return val

def setup():
    print_banner()

    # Install deps
    install_dependencies()

    print("Let's configure EDITH for orko-server.\n")

    # Telegram Bot Token
    print("Step 1: Telegram Bot")
    print("  → Go to @BotFather on Telegram")
    print("  → Send /newbot and follow instructions")
    print("  → Copy the bot token\n")
    telegram_token = get_input("Telegram Bot Token", secret=True)

    # Allowed User IDs
    print("\nStep 2: Your Telegram User ID")
    print("  → Message @userinfobot on Telegram to get your ID")
    print("  → You can add multiple IDs separated by comma\n")
    user_ids_raw = get_input("Your Telegram User ID(s)")
    allowed_user_ids = [int(x.strip()) for x in user_ids_raw.split(",")]

    # Gemini API Key
    print("\nStep 3: Gemini API Key")
    print("  → Go to: https://aistudio.google.com/app/apikey")
    print("  → Create a free API key\n")
    gemini_api_key = get_input("Gemini API Key", secret=True)

    # Default Model
    print("\nStep 4: Default Gemini Model")
    for i, m in enumerate(AVAILABLE_MODELS):
        print(f"  {i+1}. {m}")
    model_choice = get_input("Choose model number", default="2")
    try:
        default_model = AVAILABLE_MODELS[int(model_choice) - 1]
    except (ValueError, IndexError):
        default_model = "gemini-2.0-flash"
    print(f"  → Selected: {default_model}")

    # Save config
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        "telegram_token": telegram_token,
        "allowed_user_ids": allowed_user_ids,
        "gemini_api_key": gemini_api_key,
        "default_model": default_model,
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    os.chmod(CONFIG_FILE, 0o600)  # Secure the config file
    print(f"\n[✓] Config saved to {CONFIG_FILE}")

    # Create systemd service
    print("\n[*] Creating systemd service...")
    edith_path = Path(__file__).parent / "edith.py"
    service_content = f"""[Unit]
Description=EDITH AI Server Agent
After=network.target

[Service]
Type=simple
User={os.getenv('USER', 'ubuntu')}
WorkingDirectory={Path(__file__).parent}
ExecStart={sys.executable} {edith_path}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    service_path = Path("/etc/systemd/system/edith.service")
    try:
        subprocess.run(["sudo", "tee", str(service_path)], input=service_content.encode(), check=True, capture_output=True)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        subprocess.run(["sudo", "systemctl", "enable", "edith"], check=True)
        print("[✓] Systemd service created and enabled.")
        print("\nTo start EDITH:")
        print("  sudo systemctl start edith")
        print("\nTo check status:")
        print("  sudo systemctl status edith")
        print("\nTo view logs:")
        print("  journalctl -u edith -f")
    except subprocess.CalledProcessError as e:
        print(f"[!] Could not create systemd service automatically.")
        print(f"    Run manually: sudo nano /etc/systemd/system/edith.service")
        print(f"\nService content:\n{service_content}")

    print(f"""
╔═══════════════════════════════════════════╗
║   EDITH Setup Complete!                  ║
╠═══════════════════════════════════════════╣
║                                          ║
║  Start:   sudo systemctl start edith     ║
║  Stop:    sudo systemctl stop edith      ║
║  Logs:    journalctl -u edith -f         ║
║                                          ║
║  Then message your bot on Telegram!      ║
╚═══════════════════════════════════════════╝
""")

if __name__ == "__main__":
    setup()
