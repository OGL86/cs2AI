# CS2 AI Demo Review

AI-powered CS2 demo analysis that parses your match replays and delivers detailed coaching feedback.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Flask](https://img.shields.io/badge/Flask-Web_App-green)
![Groq](https://img.shields.io/badge/Groq-LLM_API-orange)

## Features

- **Upload & parse** CS2 demo files (`.dem`) using [demoparser2](https://github.com/LaihoE/demoparser)
- **Rich data extraction** — kills, deaths, economy, grenades, trade kills, entry kills, positions, bomb events
- **AI coaching analysis** via Groq (llama-3.3-70b-versatile) with 5 structured sections:
  - Economy & Buy Decisions
  - Positioning & Duels
  - Trade Kills & Teamplay
  - Aim & Mechanics
  - Top 3 Improvement Points
- **Skill score bars** — visual breakdown of aim, positioning, utility, economy, and teamplay
- **Match history** — browse and revisit previous analyses
- **Markdown export** — download analysis as `.md` files
- **Scan local demos** — auto-detect `.dem` files in the uploads folder

## Screenshots

> Dark CS2-inspired UI with orange accents

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/OGL86/cs2AI.git
cd cs2AI
```

### 2. Create virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set up your API key

Create a `.env` file:

```bash
cp .env.example .env
```

Edit `.env` and add your [Groq API key](https://console.groq.com/keys):

```
GROQ_API_KEY=your_key_here
```

### 5. Run the app

```bash
python app.py
```

Open **http://localhost:5000** in your browser.

## Usage

1. Upload a `.dem` file (or place it in the `uploads/` folder and use "Scan Demos")
2. Enter the player name you want to analyze
3. Click **Analyze** and wait for the AI coaching report
4. Review skill scores, per-round breakdown, and improvement tips
5. Export to Markdown or revisit from history

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Flask (Python) |
| Demo Parser | [demoparser2](https://github.com/LaihoE/demoparser) (Rust-based) |
| AI Model | Groq API — llama-3.3-70b-versatile |
| Frontend | Tailwind CSS (CDN), vanilla JS |
| Config | python-dotenv |

## Project Structure

```
cs2AI/
├── app.py              # Flask backend + demo parsing + AI analysis
├── templates/
│   └── index.html      # Single-page frontend
├── uploads/            # Demo files (.dem)
├── analyses/           # Exported markdown reports
├── history.json        # Analysis history
├── requirements.txt
├── .env.example
└── SPEC.md
```

## API Key

This app uses [Groq's free API](https://console.groq.com/) with the `llama-3.3-70b-versatile` model. The free tier allows ~30 requests/minute which is plenty for demo analysis.

## License

MIT
