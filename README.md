# 🦾 EDITH — AI Server Agent for orko-server

> *"Even Dead, I'm The Hero"*  
> Your personal Iron Man AI — for your Ubuntu server.

---

## What EDITH Can Do

- 💬 **Natural language control** — just talk to it via Telegram
- 🔧 **Execute any server task** — install, configure, manage services
- 🛡️ **Security management** — fail2ban, UFW, auth log monitoring
- 🔄 **Self-healing** — if a command fails, it retries with an alternative
- 📊 **System monitoring** — auto-alerts if CPU/RAM/Disk goes critical
- 📋 **Log analysis** — AI-powered log reading
- 🔒 **Whitelist-only** — only YOUR Telegram account can control it
- 🧠 **Multi-model** — switch between Gemini models on the fly

---

## Installation

### 1. Clone / Copy to your server

```bash
mkdir -p ~/edith
# Copy edith.py and edith_setup.py to ~/edith/
```

### 2. Run setup wizard

```bash
cd ~/edith
python3 edith_setup.py
```

The wizard will ask for:
- **Telegram Bot Token** → get from @BotFather
- **Your Telegram User ID** → get from @userinfobot
- **Gemini API Key** → https://aistudio.google.com/app/apikey

### 3. Start EDITH

```bash
sudo systemctl start edith
sudo systemctl status edith
```

### 4. Message your bot on Telegram!

---

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Wake EDITH, see system info |
| `/status` | Full system status |
| `/security` | Run security audit |
| `/logs` | AI-powered log analysis |
| `/model` | Switch Gemini model |
| `/clear` | Clear AI conversation memory |
| `/help` | Full help |

## Natural Language Examples

Just type these to your bot:

```
install nginx
block IP 192.168.1.100
restart docker
show disk usage
update all packages
who is logged in?
enable fail2ban
check if port 8080 is open
show failed login attempts
check SSL certificate expiry
```

---

## Available Gemini Models

| Model | Speed | Quality |
|-------|-------|---------|
| `gemini-2.5-flash-preview-04-17` | Fast | Best |
| `gemini-2.0-flash` | Fast | Great (default) |
| `gemini-2.0-flash-lite` | Fastest | Good |
| `gemini-1.5-flash` | Fast | Good |
| `gemini-1.5-flash-8b` | Fastest | Basic |

Switch with: `/model gemini-2.5-flash-preview-04-17`

---

## Security

- Only whitelisted Telegram User IDs can send commands
- Unauthorized access attempts are logged
- Config file is stored at `~/.edith/config.json` (chmod 600)
- All commands are logged to `~/.edith/edith.log`

---

## Files

```
~/edith/
├── edith.py          # Main agent
├── edith_setup.py    # Setup wizard
└── README.md         # This file

~/.edith/
├── config.json       # Your credentials (chmod 600)
└── edith.log         # Activity log
```

---

## Troubleshooting

**EDITH not responding?**
```bash
journalctl -u edith -f
```

**Restart EDITH:**
```bash
sudo systemctl restart edith
```

**Check config:**
```bash
cat ~/.edith/config.json
```

**Manual run (for debugging):**
```bash
python3 ~/edith/edith.py
```
