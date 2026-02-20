# CS2 AI Demo Review - Specification

## Project Overview
- **Name:** CS2 AI Demo Review
- **Type:** Web Application (Python/Flask)
- **Core Functionality:** Upload CS2 demo files (.dem) and get AI-powered analysis of your gameplay
- **Target Users:** CS2 players wanting to improve their gameplay

## Tech Stack
- Flask (Python backend)
- Tailwind CSS (via CDN)
- Groq API (free AI)
- Python demo parsing (custom)

## UI/UX Specification

### Layout
- Single page app with upload section and results section
- Dark theme (CS2-inspired: dark grays, orange accents)
- Responsive design

### Colors
- Background: #1a1a1a
- Card: #252525
- Primary: #de9b35 (CS2 orange)
- Text: #ffffff
- Text secondary: #888888

### Components
1. **Header** - App title with CS2 styling
2. **Upload Area** - Drag & drop zone for .dem files
3. **Status Indicator** - Shows parsing/analysis progress
4. **Results Panel** - AI analysis output

## Acceptance Criteria
- [x] User can upload .dem file
- [x] App parses demo file
- [x] AI generates analysis
- [x] Results displayed to user
- [x] Clean, dark CS2-inspired UI
