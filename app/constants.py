from __future__ import annotations

APP_TIMEZONE = "America/Los_Angeles"
UTC_TIMEZONE = "UTC"

GLASS_VOLUME_ML = 250
MAX_SINGLE_WATER_ML = 2000
MAX_DAILY_WATER_ML = 8000
HYDRATION_DUE_GRACE_MINUTES = 75
HYDRATION_EXTRA_MIN_GAP_MINUTES = 45
EYE_EXERCISE_THRESHOLD_MINUTES = 25
DEFAULT_POMODORO_FOCUS_MINUTES = 25
DEFAULT_POMODORO_BREAK_MINUTES = 5
DEFAULT_POMODORO_ACTIVITY_LABEL = "work"

AI_MAX_MESSAGE_CHARS = 1200
WELLNESS_BLEND_FACTOR = 0.12

# Pattern recognition configuration. Keep these outside scoring functions so mood labels
# and evidence text can be expanded without rewriting the scoring logic.
MOOD_SCORE_MAP = {
    "happy": 82,
    "calm": 72,
    "normal": 58,
    "focused": 68,
    "energetic": 76,
    "tired": 42,
    "exhausted": 38,
    "sad": 32,
    "anxious": 30,
    "stressed": 28,
    "custom": 50,
    # Simple Chinese aliases for future bilingual UI support.
    "开心": 82,
    "平静": 72,
    "普通": 58,
    "专注": 68,
    "有活力": 76,
    "累": 42,
    "疲惫": 38,
    "难过": 32,
    "焦虑": 30,
    "压力大": 28,
}

PATTERN_MIN_ACTIVE_DAYS_FOR_READY = 7

PATTERN_EVIDENCE_MESSAGES = {
    "water_below_goal_pct": "Water intake is below {pct}% of today’s goal on average.",
    "water_below_baseline_pct": "Recent hydration is at least {pct}% lower than your own baseline.",
    "water_under_ml": "Recent water amount is under about {ml} ml per day.",
    "focus_time_high": "Recent focus time is high for a single day rhythm.",
    "focus_above_baseline_pct": "Recent focus minutes are more than {pct}% above your baseline.",
    "eye_breaks_low": "Multiple focus sessions are not matched with enough eye breaks.",
    "rest_breaks_low_for_focus": "Rest-break records are low compared with focus workload.",
    "sleep_below_hours": "Average logged sleep is below {hours:g} hours in the recent window.",
    "mood_below_baseline_pct": "Mood score is about {pct}% lower than your own baseline.",
    "focus_below_baseline_pct": "Focus minutes are about {pct}% lower than your baseline.",
    "rest_activity_higher_pct": "Rest-break activity is higher than usual, which may show reduced energy.",
    "sleep_below_target_pct": "Recent sleep is below {pct}% of your personal sleep target.",
    "workload_high_recovery_low": "Workload is high while recovery breaks are low.",
    "exercise_below_baseline_pct": "Exercise minutes are lower than your usual rhythm.",
    "mood_lower_than_baseline": "Mood score is lower than your recent baseline.",
    "camera_fatigue_score": "Camera-based focus signal reached about {pct}% possible fatigue.",
    "camera_perclos_pct": "Recent eye-closure time was about {pct}% of the camera window.",
    "camera_microsleep": "A long eye-closure signal was detected during a focus round.",
    "camera_yawns": "Camera signal counted {count} possible yawn(s) in the recent window.",
    "camera_head_signal": "Head posture or nodding changed compared with the calibrated baseline.",
    "camera_gaze_down": "Gaze looked downward for several seconds during the focus round.",
}

TASK_ROLLOVER_MAX_PER_DAY = 12
ACTIVITY_ENTRY_RETENTION_DAYS = 180
ACTIVITY_ENTRY_MAX_ROWS = 1000
ACTIVITY_PRUNE_INTERVAL_HOURS = 6
CLIENT_STATE_RETENTION_DAYS = 3
AI_INTERVENTION_HISTORY_DAYS = 45

MINIMUM_AI_WATER_GOAL_ML = 1200

LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3
DEFAULT_LOG_LEVEL = "INFO"

HISTORY_PAGE_SIZE = 20
POMODORO_STATE_CLIENT_KEY = "pomodoro_active_state"
POMODORO_STATE_MAX_AGE_DAYS = 7
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"
OPENAI_MODEL_FALLBACKS = ("gpt-4.1-mini", "gpt-4o")
