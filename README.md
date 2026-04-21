# WellHabit

A green-themed Flask wellness dashboard with:
- register / login
- SQLite database
- daily log for water, sleep, exercise, steps
- journal + "what did you just do" text
- Tomato Clock / Pomodoro tracking
- monthly calendar and todo list
- today's todo list on the dashboard
- hydration reminder modal
- meal detection from activity text using the OpenAI Responses API when `OPENAI_API_KEY` is set
- keyword fallback meal detection when no API key is available

## Run

```bash
pip install -r requirements.txt
python app.py
```

Open:

```bash
http://127.0.0.1:5000
```

## Optional OpenAI setup

Windows PowerShell:

```powershell
setx OPENAI_API_KEY "your_api_key_here"
```

Then reopen the terminal and run the app again.

Optional custom model:

```powershell
setx OPENAI_MODEL "gpt-5.4"
```


For safer local configuration, you can also set a secret key and enable debug only when needed:

```powershell
setx FLASK_SECRET_KEY "your_long_random_secret"
setx FLASK_DEBUG "1"
```


## Care AI boundaries and support

WellHabit's Care AI is designed for habit support.

- This is habit support, not medical advice.
- Wellness scores are behavioral estimates, not clinical metrics.
- Care AI is not therapy.
- If emotions feel high-risk or unsafe, contact real-person support now.

The Care AI screen shows these limits in the UI, and when a care chat ends on a negative tone the app can also show a region-matched crisis support contact based on the browser's locale/time zone when available.
