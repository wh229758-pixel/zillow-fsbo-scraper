Agent 1 — Zillow Scraper:
Uses Playwright (headless browser) to automatically browse Zillow, filter For Sale By Owner (FSBO) listings in a configurable location (changeable anytime via a config.json file), click each listing, and extract raw data including address, price, phone number, and property details. Should use human-like delays and stealth settings to avoid being blocked. Scrapes up to 10 pages per run.

Agent 2 — AI Data Extractor:
Takes the raw scraped listings and uses the Claude API (claude-sonnet-4-6) to clean and structure the data into consistent records with fields: address, city, state, zip code, price, phone number, seller name, bedrooms, bathrooms, square footage, listing URL, and notes. Should process in batches, deduplicate by address, and validate records.

Agent 3 — Google Sheets Writer:
Takes the clean structured listings and appends them to a Google Sheet using a service account. Skips duplicates already in the sheet. Adds columns for Status and Follow Up so the user can track outreach.

Requirements:

All agents run in sequence via a single main.py orchestrator
Location is configurable in config.json without touching any code
Runs automatically once a day via cron job (time configurable)
Logs all activity to run.log
One-click setup via setup.sh for Mac Mini (installs dependencies, sets up cron)
Full README with step-by-step instructions for a non-technical user
API key stored securely in environment variables, never hardcoded
Google credentials stored in google_credentials.json service account file
