# WellHabit

A green-themed Flask wellness dashboard with basic register and login functions, a built-in Tomato Clock for focus sessions, daily wellness tracking, and simple task management.

## Features

- Basic user registration and login
- Green-themed UI
- SQLite database
- Daily log for:
  - water intake
  - sleep
  - exercise
  - steps
- Journal and “what did you just do” text input
- Tomato Clock / Pomodoro tracking
- Monthly calendar and todo list
- Today’s todo list on the dashboard
- Hydration reminder modal
- Meal detection from activity text using the OpenAI Responses API when `OPENAI_API_KEY` is set
- Keyword-based fallback meal detection when no API key is available

## Run

```bash
pip install -r requirements.txt
python app.py
```

Open in your browser:

```bash
http://127.0.0.1:5000
```

## Optional OpenAI Setup

Windows PowerShell:

```powershell
setx OPENAI_API_KEY "your_api_key_here"
```

Then reopen the terminal and run the app again.

Optional custom model:

```powershell
setx OPENAI_MODEL "gpt-5.4"
```
