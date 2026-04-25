# WellHabit

A minimal Flask wellness dashboard with:
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

First, set a persistent secret key for your local environment.

Windows PowerShell:

```powershell
setx FLASK_SECRET_KEY "your_long_random_secret"
```

Then install dependencies, seed local demo data if you want it, and run the app:

```bash
pip install -r requirements.txt
python seed_demo.py
python app.py
```

Open:

```bash
http://127.0.0.1:5000
```

The repository and release zip do not ship with `instance/wellhabit.db`.
If you want local demo data, seed it into your own local database with `python seed_demo.py`.
That script recreates the three demo users with fresh passwords on each run and prints the new password once.

## Optional OpenAI setup

Windows PowerShell:

```powershell
setx OPENAI_API_KEY "your_api_key_here"
```

Then reopen the terminal and run the app again.

Optional custom model:

```powershell
setx OPENAI_MODEL "gpt-4o-mini"
```


For safer local configuration, set a persistent secret key for normal runs:

```powershell
setx FLASK_SECRET_KEY "your_long_random_secret"
```

Use debug only when needed:

```powershell
setx FLASK_DEBUG "1"
```

Behavior:
- when `FLASK_DEBUG=1` and no secret key is set, the app generates an ephemeral secret key for that process only
- when `FLASK_DEBUG` is not `1` and no secret key is set, the app raises an error and refuses to start


## Care AI boundaries and support

WellHabit's Care AI is designed for habit support.

- This is habit support, not medical advice.
- Wellness scores are behavioral estimates, not clinical metrics.
- Care AI is not therapy.
- If emotions feel high-risk or unsafe, contact real-person support now.

The Care AI screen shows these limits in the UI, and when a care chat ends on a negative tone the app can also show a region-matched crisis support contact based on the browser's locale/time zone when available.

## Packaging

Create a clean release zip with:

```bash
bash scripts/package.sh
```

This removes Python cache files before packaging and excludes `instance/`, `.env`, local databases, logs, virtual environments, and IDE folders from the archive.


## Guided break mode

- Guided breathing and posture breaks with optional camera-based feedback.
- Pose detection is intended as weak habit support, not medical advice or diagnosis.
- To enable local pose feedback, place `pose_landmarker_lite.task` in `app/static/break_assets/`. The page degrades to visual-only breathing guidance when the model or camera is unavailable.
- Uses MediaPipe Tasks Vision in the browser for optional pose landmarks. Video frames are not uploaded by the break page.
