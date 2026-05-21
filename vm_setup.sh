#!/bin/bash
# Run this ON THE ORACLE VM after first SSH in.
# Usage: bash vm_setup.sh
# Expects: linkedin_storage.json already scp'd to ~/scraper/

set -e

echo "=== Oracle VM setup for hiring-agent ==="

# 1. System deps
sudo apt-get update -q
sudo apt-get install -y python3 python3-pip python3-venv wget curl unzip git

# 2. Install Chromium dependencies (ARM64 — Google Chrome has no ARM Linux build)
sudo apt-get install -y libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libgbm1

# 3. Clone the repo
git clone https://github.com/pranav6082/claude-work.git ~/claude-work
cd ~/claude-work/pranav-personal/product-hiring-stuff/scraper

# 4. Python venv + deps
python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt
playwright install chromium  # fallback; Chrome is primary

# 5. .env file — fill in real values before running. NEVER commit credentials.
if [ ! -f .env ]; then
  cat > .env <<'ENVEOF'
DATABASE_URL=
LINKEDIN_COOKIES_FILE=linkedin_cookies.json
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ENVEOF
  echo "Created .env template — fill in DATABASE_URL (and tokens), then re-run."
  exit 1
fi

# 6. Verify linkedin_storage.json is present
if [ ! -f linkedin_storage.json ]; then
  echo "ERROR: linkedin_storage.json not found."
  echo "Copy it from your Mac: scp linkedin_storage.json ubuntu@<VM_IP>:~/claude-work/pranav-personal/product-hiring-stuff/scraper/"
  exit 1
fi

# 7. Test run
echo "Running scraper test..."
source venv/bin/activate
python3 scraper.py

# 8. Set up cron — runs at 2 AM IST (8:30 PM UTC)
SCRAPER_DIR="$HOME/claude-work/pranav-personal/product-hiring-stuff/scraper"
CRON_CMD="30 20 * * * cd $SCRAPER_DIR && source venv/bin/activate && python3 scraper.py >> ~/scraper.log 2>&1"
(crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -

echo ""
echo "=== Setup complete ==="
echo "Scraper will run nightly at 2:00 AM IST."
echo "Logs: tail -f ~/scraper.log"
echo "Manual run: cd $SCRAPER_DIR && source venv/bin/activate && python3 scraper.py"
