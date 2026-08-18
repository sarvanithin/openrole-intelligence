# Google Sheets API Setup Guide

## Step 1: Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click "Select a project" → "New Project"
3. Name it: `fortune-job-scraper`
4. Click "Create"

## Step 2: Enable Google Sheets API

1. In the Cloud Console, go to "APIs & Services" → "Library"
2. Search for "Google Sheets API"
3. Click on it and press "Enable"

## Step 3: Create Service Account

1. Go to "APIs & Services" → "Credentials"
2. Click "Create Credentials" → "Service Account"
3. Name: `job-scraper-service`
4. Click "Create and Continue"
5. Skip the optional steps, click "Done"

## Step 4: Download Credentials

1. Click on your new service account
2. Go to "Keys" tab
3. Click "Add Key" → "Create new key"
4. Select "JSON" format
5. Download and save as `credentials.json`

## Legacy Google Sheets setup

This setup applies only to the retained legacy exporter. The normalized database,
API, and dashboard use the `job-intel` commands documented in `README.md`.

### Step 5: Create Google Sheets

### Sheet 1: Company URLs (Input)
1. Create a new Google Sheet
2. Name it: `Fortune Job Scraper - Companies`
3. Add headers in Row 1:
   - A1: `Company Name`
   - B1: `Career Search URL`
   - C1: `Platform Type`
   - D1: `Last Scraped`
   - E1: `Status`

### Sheet 2: Jobs Database (Output)
1. Create another Google Sheet
2. Name it: `Fortune Job Scraper - Jobs`
3. Add headers in Row 1:
   - A1: `Job ID`
   - B1: `Job Title`
   - C1: `Company Name`
   - D1: `Job URL`
   - E1: `Company Career URL`
   - F1: `Location`
   - G1: `Posted Date`
   - H1: `Date Added`
   - I1: `Last Seen`
   - J1: `Keywords Matched`
   - K1: `Status`

## Step 6: Share Sheets with Service Account

1. Open each Google Sheet
2. Click "Share"
3. Add the service account email (found in `credentials.json` as `client_email`)
4. Give "Editor" access

## Step 7: Get Sheet IDs

1. Open your Google Sheet
2. Look at the URL: `https://docs.google.com/spreadsheets/d/XXXXXXXXX/edit`
3. Copy the ID (the `XXXXXXXXX` part)

## Step 8: Configure Environment

Create a `.env` file in the project root:

```bash
COMPANIES_SHEET_ID=your_companies_sheet_id_here
JOBS_SHEET_ID=your_jobs_sheet_id_here
```

## Step 9: Add Credentials for GitHub Actions

1. Open `credentials.json`
2. Copy entire contents
3. Go to your GitHub repo → Settings → Secrets → Actions
4. Create secret: `GOOGLE_CREDENTIALS` with the JSON content
5. Create secret: `COMPANIES_SHEET_ID` with your companies sheet ID
6. Create secret: `JOBS_SHEET_ID` with your jobs sheet ID

## Testing Locally

```bash
# Set environment variables
export COMPANIES_SHEET_ID="your_sheet_id"
export JOBS_SHEET_ID="your_sheet_id"

# Run the scraper
python src/main.py --test
```
