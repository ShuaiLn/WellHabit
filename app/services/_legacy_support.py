from __future__ import annotations

from calendar import Calendar
import json
import logging
import math
import re
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from flask import current_app, flash, has_request_context, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from .. import db
from ..constants import (
    ACTIVITY_ENTRY_MAX_ROWS,
    ACTIVITY_ENTRY_RETENTION_DAYS,
    ACTIVITY_PRUNE_INTERVAL_HOURS,
    AI_INTERVENTION_HISTORY_DAYS,
    AI_MAX_MESSAGE_CHARS,
    APP_TIMEZONE,
    CLIENT_STATE_RETENTION_DAYS,
    DEFAULT_POMODORO_ACTIVITY_LABEL,
    DEFAULT_POMODORO_BREAK_MINUTES,
    DEFAULT_POMODORO_FOCUS_MINUTES,
    EYE_EXERCISE_THRESHOLD_MINUTES,
    GLASS_VOLUME_ML,
    MAX_DAILY_WATER_ML,
    MAX_SINGLE_WATER_ML,
    HYDRATION_DUE_GRACE_MINUTES,
    HYDRATION_EXTRA_MIN_GAP_MINUTES,
    HISTORY_PAGE_SIZE,
    POMODORO_STATE_CLIENT_KEY,
    TASK_ROLLOVER_MAX_PER_DAY,
    UTC_TIMEZONE,
)
from ..event_impact import EVENT_IMPACT_LABELS, history_payload_from_impacts, infer_event_impacts
from ..ai_services import (
    analyze_meal_text,
    analyze_text_mood,
    care_chat_reply,
    convert_drink_amount_to_ml,
    mood_display_label,
    mood_value_for_label,
    recommend_micro_intervention,
    suggest_personal_goals,
    summarize_care_chat_session,
    update_wellness_scores,
)
from ..models import ActivityEntry, CalendarEvent, CareChatMessage, CareChatSession, ClientState, DailyLog, EyeExercisePrompt, EyeExerciseState, HydrationPrompt, MoodEntry, PomodoroSession, Task, User

LOCAL_TZ = ZoneInfo(APP_TIMEZONE)
EYE_EXERCISE_VIDEO_URL = 'https://www.youtube.com/watch?v=iVb4vUp70zY'
GOAL_INTENSITY_CHOICES = [('easy', 'Easy'), ('medium', 'Medium'), ('hard', 'Hard')]

UTC_TZ = ZoneInfo(UTC_TIMEZONE)

CARE_BOUNDARY_LINES = [
    'This is habit support, not medical advice.',
    'Scores are behavioral estimates, not clinical metrics.',
    'Care AI is not therapy.',
    'If emotions feel high-risk or unsafe, contact real-person support now.',
]

NEGATIVE_CARE_MOOD_LABELS = {'sad', 'anxious', 'stressed', 'exhausted', 'overwhelmed', 'mixed'}
logger = logging.getLogger(__name__)

HIGH_RISK_SUPPORT_HINTS = {
    'suicide', 'suicidal', 'kill myself', 'want to die', 'self harm', 'self-harm', 'hurt myself',
    'end my life', 'not safe', 'unsafe', 'panic attack', "can't go on", 'cannot go on', 'hopeless',
    '轻生', '自杀', '不想活', '伤害自己', '结束生命', '不安全', '撑不住'
}

CRISIS_SUPPORT_BY_REGION = {
    'US': {
        'region_label': 'United States',
        'service_name': '988 Suicide & Crisis Lifeline',
        'contact_line': 'Call or text: 988',
        'chat_line': 'Chat: 988lifeline.org',
        'urgent_line': 'If there is immediate danger, call 911.',
    },
    'CA': {
        'region_label': 'Canada',
        'service_name': '9-8-8 Suicide Crisis Helpline',
        'contact_line': 'Call or text: 9-8-8',
        'chat_line': 'More help: 988.ca',
        'urgent_line': 'If there is immediate danger, call 911.',
    },
    'GB': {
        'region_label': 'United Kingdom',
        'service_name': 'Samaritans',
        'contact_line': 'Call: 116 123',
        'chat_line': 'More help: samaritans.org',
        'urgent_line': 'If there is immediate danger, call 999.',
    },
    'IE': {
        'region_label': 'Ireland',
        'service_name': 'Samaritans',
        'contact_line': 'Call: 116 123',
        'chat_line': 'More help: samaritans.org/samaritans-ireland',
        'urgent_line': 'If there is immediate danger, call 112 or 999.',
    },
    'AU': {
        'region_label': 'Australia',
        'service_name': 'Lifeline',
        'contact_line': 'Call: 13 11 14 · Text: 0477 13 11 14',
        'chat_line': 'More help: lifeline.org.au',
        'urgent_line': 'If there is immediate danger, call 000.',
    },
    'NZ': {
        'region_label': 'New Zealand',
        'service_name': 'Lifeline Aotearoa / Suicide Crisis Helpline',
        'contact_line': 'Call: 0800 543 354 · Text: 4357 · Suicide Crisis Helpline: 0508 828 865',
        'chat_line': 'More help: lifeline.org.nz',
        'urgent_line': 'If there is immediate danger, call 111.',
    },
}


WELLNESS_META = [
    ('hydration', 'Hydration', 'Water goal progress'),
    ('energy', 'Energy', 'Sleep duration'),
    ('fitness', 'Fitness', 'Exercise, steps, and stretching'),
    ('focus', 'Focus', 'Pomodoro sessions and balance'),
    ('mood', 'Mood', 'Journal, feelings, and stress'),
    ('overall', 'Overall Wellness', 'Combined daily wellness'),
]

MEAL_TASKS = [
    ('Breakfast', 'breakfast'),
    ('Lunch', 'lunch'),
    ('Dinner', 'dinner'),
]

MOOD_OPTIONS = [
    ('happy', 'Happy'),
    ('calm', 'Calm'),
    ('normal', 'Normal'),
    ('anxious', 'Anxious'),
    ('sad', 'Sad'),
    ('exhausted', 'Exhausted'),
    ('stressed', 'Stressed'),
    ('custom', 'Custom'),
]

MOOD_CHIP_CHOICES = [
    {'label': 'happy', 'title': 'Happy'},
    {'label': 'calm', 'title': 'Calm'},
    {'label': 'normal', 'title': 'Normal'},
    {'label': 'anxious', 'title': 'Anxious'},
    {'label': 'sad', 'title': 'Sad'},
    {'label': 'exhausted', 'title': 'Exhausted'},
    {'label': 'stressed', 'title': 'Stressed'},
    {'label': 'custom', 'title': 'Custom'},
]

LEGACY_EMOJI_MOOD_MAP = {
    '😁': 'happy',
    '😄': 'happy',
    '🤩': 'happy',
    '🥳': 'happy',
    '😊': 'happy',
    '😌': 'calm',
    '🥹': 'calm',
    '🌤️': 'calm',
    '🙂': 'normal',
    '😶': 'normal',
    '😴': 'exhausted',
    '🥱': 'exhausted',
    '😮‍💨': 'exhausted',
    '😢': 'sad',
    '😭': 'sad',
    '💔': 'sad',
    '😰': 'anxious',
    '😟': 'anxious',
    '😣': 'stressed',
    '😤': 'stressed',
    '😡': 'stressed',
    '😵‍💫': 'stressed',
    '🤯': 'stressed',
}

MOOD_COLOR_MAP = {
    'happy': '#5f7f69',
    'calm': '#5f7688',
    'normal': '#78716c',
    'anxious': '#9a7d53',
    'sad': '#a06565',
    'exhausted': '#8b7a9b',
    'stressed': '#8f625d',
    'custom': '#78716c',
}

NEGATIVE_MOOD_LABELS = {'sad', 'anxious', 'exhausted', 'stressed'}

CARE_WATER_INTENT_PATTERNS = [re.compile(p) for p in (
    r'\bi want to drink water\b',
    r'\bi need water\b',
    r'\bi need to drink\b',
    r'\bi should drink water\b',
    r'\blet me drink water\b',
    r'\bgo drink water\b',
    r'\bdrink some water\b',
    r'\bhave some water\b',
    r'\bremind me to drink water\b',
    r'\bi am thirsty\b',
    r'想喝水',
    r'我要喝水',
    r'我想喝水',
    r'去喝水',
    r'喝点水',
    r'提醒我喝水',
    r'口渴',
)]

CARE_EYE_EXERCISE_INTENT_PATTERNS = [re.compile(p) for p in (
    r'\bi want to do an eye exercise\b',
    r'\bi want to do eye exercises\b',
    r'\bi need an eye break\b',
    r'\bi need to rest my eyes\b',
    r'\bmy eyes are tired\b',
    r'\bmy eyes hurt\b',
    r'\bdo an eye exercise\b',
    r'\brest my eyes\b',
    r'\beye exercise\b',
    r'眼保健操',
    r'休息眼睛',
    r'眼睛累',
    r'眼睛酸',
    r'护眼',
)]


EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{3,30}$')


def _clean_text(value: str | None, max_length: int | None = None) -> str:
    cleaned = (value or '').strip()
    if max_length is not None:
        cleaned = cleaned[:max_length]
    return cleaned


def _is_valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match((value or '').strip()))



def _normalize_mood_choice(mood_label: str | None, custom_text: str | None = None) -> tuple[str, str | None]:
    raw = (mood_label or '').strip().lower()
    aliases = {
        '开心': 'happy',
        '普通': 'normal',
        '伤心': 'sad',
        'custom': 'custom',
        'other': 'custom',
        'okay': 'normal',
        'quiet': 'normal',
        'overwhelmed': 'stressed',
        'mixed': 'stressed',
        'hopeful': 'calm',
        'excited': 'happy',
        'warm': 'happy',
        'drained': 'exhausted',
        'worried': 'anxious',
        'frustrated': 'stressed',
        'angry': 'stressed',
    }
    normalized = LEGACY_EMOJI_MOOD_MAP.get(raw, aliases.get(raw, raw or 'normal'))
    custom_clean = (custom_text or '').strip()[:120] or None
    if normalized not in {'happy', 'calm', 'normal', 'anxious', 'sad', 'exhausted', 'stressed', 'custom'}:
        normalized = 'custom' if custom_clean else 'normal'
    if normalized != 'custom':
        custom_clean = None
    return normalized, custom_clean


def _mood_badge_payload(mood_label: str | None, custom_text: str | None = None) -> dict:
    normalized, custom_clean = _normalize_mood_choice(mood_label, custom_text)
    display = mood_display_label(normalized, custom_clean)
    color = MOOD_COLOR_MAP.get(normalized, '#64748b')
    return {
        'label': normalized,
        'display': display,
        'value': mood_value_for_label(normalized, custom_clean),
        'color': color,
    }


def _selected_mood_emoji(mood_label: str | None) -> str:
    normalized, _ = _normalize_mood_choice(mood_label, None)
    return normalized if normalized in {item['label'] for item in MOOD_CHIP_CHOICES} else ''


def _choose_avatar_emoji_from_scores(user: User, after_scores: dict[str, int], feedback: dict | None = None) -> str:
    hydration = int(after_scores.get('hydration') or 50)
    energy = int(after_scores.get('energy') or 50)
    fitness = int(after_scores.get('fitness') or 50)
    focus = int(after_scores.get('focus') or 50)
    mood = int(after_scores.get('mood') or 50)
    overall = int(after_scores.get('overall') or 50)

    if overall >= 85 and mood >= 80:
        return '🤩'
    if overall >= 75 and focus >= 70 and energy >= 70:
        return '😄'
    if hydration < 35 and energy < 40:
        return '🥵'
    if energy < 35:
        return '😴'
    if mood < 35 and overall < 45:
        return '😢'
    if focus < 35 and energy < 50:
        return '😵‍💫'
    if mood >= 70 and overall >= 60:
        return '😊'
    if focus >= 75 and overall >= 60:
        return '🧠'
    if fitness >= 75 and overall >= 60:
        return '💪'
    if hydration >= 75 and mood >= 60:
        return '💧'
    if overall >= 60:
        return '🙂'
    if overall >= 45:
        return '😐'
    return '😮‍💨'


def _record_mood_entry(user_id: int, source: str, mood_label: str | None, custom_text: str | None = None, summary: str | None = None, log: DailyLog | None = None, event_at: datetime | None = None, detected_by: str = 'user') -> MoodEntry:
    normalized, custom_clean = _normalize_mood_choice(mood_label, custom_text)
    mood_entry = MoodEntry(
        user_id=user_id,
        log_id=getattr(log, 'id', None),
        source=(source or 'journal')[:30],
        mood_label=normalized,
        mood_custom_text=custom_clean,
        mood_value=mood_value_for_label(normalized, custom_clean),
        summary=(summary or '').strip() or None,
        detected_by=(detected_by or 'user')[:20],
        event_at=(event_at or local_now()).replace(tzinfo=None),
    )
    db.session.add(mood_entry)
    db.session.flush()
    return mood_entry


def _mood_is_negative(mood_label: str | None, custom_text: str | None = None) -> bool:
    normalized, custom_clean = _normalize_mood_choice(mood_label, custom_text)
    if normalized == 'custom' and custom_clean:
        analysis = analyze_text_mood(custom_clean, preferred=custom_clean)
        normalized = _normalize_mood_choice(analysis.get('mood_label'), '')[0]
    return normalized in NEGATIVE_MOOD_LABELS



AI_INTERVENTION_COOLDOWN_MINUTES = 60
AI_INTERVENTION_STALE_HOURS = 8
AI_INTERVENTION_RECENT_POMODORO_MINUTES = 20

AI_INTERVENTION_CATALOG = {
    'breathing_1min': {
        'title': 'Do a 1-minute breathing reset',
        'description': 'Sit down, loosen your shoulders, and take five slow breaths.',
        'follow_up_question': 'After the 1-minute breathing reset, how much better do you feel out of 10 regarding the negativity detected earlier?',
        'chat_action': 'Try a 1-minute breathing reset: loosen your shoulders and take five slow breaths.',
    },
    'quiet_reset_5min': {
        'title': 'Take a 5-minute quiet reset',
        'description': 'Step away for 5 minutes, rest your eyes, and let your body settle a little.',
        'follow_up_question': 'After the quiet reset, how much better do you feel out of 10 regarding the negativity detected earlier?',
        'chat_action': 'Take a 5-minute quiet reset: step away, rest your eyes, and let your body settle a little.',
    },
    'drink_water_glass': {
        'title': 'Drink one glass of water slowly',
        'description': 'Drink one glass of water slowly and notice whether your body feels a bit steadier.',
        'follow_up_question': 'After drinking the water, how much better do you feel out of 10 regarding the negativity detected earlier?',
        'chat_action': 'Drink one glass of water slowly, then notice whether your body feels even a little steadier.',
    },
    'eye_reset_2min': {
        'title': 'Do a 2-minute eye reset',
        'description': 'Look away from the screen, blink slowly, and rest your eyes for 2 minutes.',
        'follow_up_question': 'After the eye reset, how much better do you feel out of 10 regarding the negativity detected earlier?',
        'chat_action': 'Do a 2-minute eye reset: look away from the screen, blink slowly, and rest your eyes.',
    },
    'stretch_2min': {
        'title': 'Do a 2-minute stretch break',
        'description': 'Stand up, roll your shoulders, and stretch your neck and back for 2 minutes.',
        'follow_up_question': 'After the stretch break, how much better do you feel out of 10 regarding the negativity detected earlier?',
        'chat_action': 'Take a 2-minute stretch break: stand up, roll your shoulders, and loosen your neck and back.',
    },
    'tiny_next_step': {
        'title': 'Do one tiny next step',
        'description': 'Pick one next step that takes under 3 minutes and do only that.',
        'follow_up_question': 'After that tiny next step, how much better do you feel out of 10 regarding the negativity detected earlier?',
        'chat_action': 'Pick one next step that takes under 3 minutes and do only that one thing.',
    },
    'reset_walk_3min': {
        'title': 'Take a 3-minute reset walk',
        'description': 'Stand up, walk for 3 minutes, and come back with a slower breath.',
        'follow_up_question': 'After the reset walk, how much better do you feel out of 10 regarding the negativity detected earlier?',
        'chat_action': 'Take a 3-minute reset walk, then come back with a slower breath.',
    },
    'kind_line_self': {
        'title': 'Write one kind line to yourself',
        'description': 'Write one short, kind sentence to yourself before you move on.',
        'follow_up_question': 'After writing that kind line, how much better do you feel out of 10 regarding the negativity detected earlier?',
        'chat_action': 'Write one short, kind sentence to yourself before you move on.',
    },
}


def _normalize_ai_text(value: str | None) -> str:
    return re.sub(r'\s+', ' ', (value or '').strip().lower())



def _infer_ai_suggestion_key_from_text(title: str | None, description: str | None = None) -> str:
    text = _normalize_ai_text(' '.join(part for part in [title or '', description or ''] if part))
    if 'breath' in text or '呼吸' in text:
        return 'breathing_1min'
    if 'quiet reset' in text or 'rest your eyes' in text or 'quiet' in text:
        return 'quiet_reset_5min'
    if 'glass of water' in text or 'drink' in text or '喝水' in text:
        return 'drink_water_glass'
    if 'eye reset' in text or 'eye exercise' in text or '眼' in text:
        return 'eye_reset_2min'
    if 'stretch' in text or '拉伸' in text:
        return 'stretch_2min'
    if 'tiny next step' in text or 'under 3 minutes' in text:
        return 'tiny_next_step'
    if 'walk' in text or '散步' in text:
        return 'reset_walk_3min'
    if 'kind line' in text or 'kind sentence' in text or '写一句' in text:
        return 'kind_line_self'
    return 'tiny_next_step'



def _task_ai_suggestion_key(task: Task | None) -> str | None:
    if not task:
        return None
    stored = _clean_text(getattr(task, 'ai_suggestion_key', None), 40)
    if stored:
        return stored
    return _infer_ai_suggestion_key_from_text(getattr(task, 'title', None), getattr(task, 'description', None))



def _serialize_ai_suggestion_from_task(task: Task | None) -> dict | None:
    if not task:
        return None
    key = _task_ai_suggestion_key(task)
    template = AI_INTERVENTION_CATALOG.get(key or '', {})
    return {
        'key': key,
        'title': task.title,
        'description': task.description or template.get('description') or '',
        'follow_up_question': task.ai_followup_question or template.get('follow_up_question') or '',
        'reason': f'Existing unfinished AI suggestion ({key or "custom"}).',
        'chat_action': template.get('chat_action') or task.title,
    }



def _build_ai_intervention_history(user_id: int, now: datetime | None = None) -> dict[str, dict]:
    current_dt = (now or local_now()).replace(tzinfo=None)
    start_dt = current_dt - timedelta(days=AI_INTERVENTION_HISTORY_DAYS)
    rows = Task.query.filter(
        Task.user_id == user_id,
        Task.task_type == 'ai_suggestion',
        Task.created_at >= start_dt,
    ).order_by(Task.created_at.desc()).all()

    history: dict[str, dict] = {}
    for row in rows:
        key = _task_ai_suggestion_key(row)
        if not key:
            continue
        bucket = history.setdefault(key, {
            'suggested_count': 0,
            'completed_count': 0,
            'rated_count': 0,
            'rating_total': 0,
            'stale_open_count': 0,
            'last_rating': None,
            'last_suggested_at': None,
            'last_completed_at': None,
        })
        bucket['suggested_count'] += 1
        if not bucket['last_suggested_at']:
            bucket['last_suggested_at'] = row.created_at
        if row.completed:
            bucket['completed_count'] += 1
            if row.completed_at and not bucket['last_completed_at']:
                bucket['last_completed_at'] = row.completed_at
        if row.ai_followup_rating is not None:
            bucket['rated_count'] += 1
            bucket['rating_total'] += int(row.ai_followup_rating)
            if bucket['last_rating'] is None:
                bucket['last_rating'] = int(row.ai_followup_rating)
        if (not row.completed) and row.created_at <= current_dt - timedelta(hours=AI_INTERVENTION_STALE_HOURS):
            bucket['stale_open_count'] += 1

    for key, bucket in history.items():
        suggested = max(1, int(bucket['suggested_count']))
        completed = int(bucket['completed_count'])
        rated = int(bucket['rated_count'])
        stale_open = int(bucket['stale_open_count'])
        avg_rating = (float(bucket['rating_total']) / rated) if rated else None
        completion_rate = completed / suggested
        profile_score = 0.0
        if avg_rating is not None:
            profile_score += (avg_rating - 5.5) * 1.6
        elif completed:
            profile_score += min(2.0, completed * 0.35)
        profile_score += (completion_rate - 0.45) * 2.5
        profile_score -= stale_open * 2.0
        if bucket.get('last_rating') is not None and int(bucket['last_rating']) >= 7:
            profile_score += 0.8
        bucket['avg_rating'] = round(avg_rating, 2) if avg_rating is not None else None
        bucket['completion_rate'] = round(completion_rate, 2)
        bucket['profile_score'] = max(-6.0, min(6.0, round(profile_score, 2)))
    return history



def _recent_ai_prompt_signals(user_id: int, now: datetime | None = None) -> dict[str, bool]:
    current_dt = (now or local_now()).replace(tzinfo=None)
    since = current_dt - timedelta(minutes=AI_INTERVENTION_COOLDOWN_MINUTES)
    recent_hydration = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user_id,
        HydrationPrompt.due_at >= since,
    ).count() > 0
    recent_eye = EyeExercisePrompt.query.filter(
        EyeExercisePrompt.user_id == user_id,
        EyeExercisePrompt.due_at >= since,
    ).count() > 0
    recent_pomodoro = PomodoroSession.query.filter(
        PomodoroSession.user_id == user_id,
        PomodoroSession.completed_at >= current_dt - timedelta(minutes=AI_INTERVENTION_RECENT_POMODORO_MINUTES),
    ).order_by(PomodoroSession.completed_at.desc()).first()
    return {
        'hydration_prompt_recent': recent_hydration,
        'eye_prompt_recent': recent_eye,
        'recent_pomodoro': recent_pomodoro is not None,
    }



def _rank_personalized_interventions(
    user: User,
    context_text: str,
    detected_mood: str | None = None,
    target_date: date | None = None,
) -> dict:
    cleaned = _clean_text(context_text, 3000)
    lowered = _normalize_ai_text(cleaned)
    chosen_date = target_date or local_today()
    current_dt = local_now().replace(tzinfo=None)
    scores = _score_snapshot(user)
    hydration = int(scores.get('hydration') or 50)
    energy = int(scores.get('energy') or 50)
    focus = int(scores.get('focus') or 50)
    mood = int(scores.get('mood') or 50)

    mood_hint = _normalize_mood_choice(detected_mood, '')[0] if detected_mood else ''
    if not mood_hint and cleaned:
        mood_hint = _normalize_mood_choice(analyze_text_mood(cleaned, preferred='').get('mood_label'), '')[0]

    history = _build_ai_intervention_history(user.id, current_dt)
    prompt_signals = _recent_ai_prompt_signals(user.id, current_dt)
    active_tasks = Task.query.filter_by(user_id=user.id, task_type='ai_suggestion', completed=False).order_by(Task.created_at.desc()).all()
    active_same_day = next((task for task in active_tasks if task.task_date == chosen_date), None)
    active_keys = {_task_ai_suggestion_key(task) for task in active_tasks if _task_ai_suggestion_key(task)}
    recent_rows = Task.query.filter(
        Task.user_id == user.id,
        Task.task_type == 'ai_suggestion',
        Task.created_at >= current_dt - timedelta(minutes=AI_INTERVENTION_COOLDOWN_MINUTES),
    ).all()
    recent_keys = {_task_ai_suggestion_key(row) for row in recent_rows if _task_ai_suggestion_key(row)}

    flags = {
        'anxious': mood_hint in {'anxious', 'stressed', 'overwhelmed'} or any(token in lowered for token in ['anxious', 'stress', 'stressed', 'panic', 'overwhelmed', '焦虑', '压力', '紧张', '崩溃']),
        'sad': mood_hint == 'sad' or any(token in lowered for token in ['sad', 'down', 'lonely', 'cry', '难过', '伤心']),
        'exhausted': mood_hint == 'exhausted' or any(token in lowered for token in ['tired', 'exhausted', 'drained', 'burned out', 'sleepy', '累', '疲劳', '没力气']),
        'overwhelmed': mood_hint in {'overwhelmed', 'stressed'} or any(token in lowered for token in ['too much', 'behind', 'deadline', "can't keep up", '压得', '来不及']),
        'thirsty': any(token in lowered for token in ['thirsty', 'drink water', 'need water', '喝水', '口渴']),
        'eyes': any(token in lowered for token in ['eye', 'eyes', 'screen', 'headache', 'vision', '眼', '屏幕']),
        'school': any(token in lowered for token in ['study', 'school', 'homework', 'exam', 'assignment', 'class', '学习', '作业', '考试']),
        'restless': any(token in lowered for token in ['angry', 'frustrated', 'restless', 'stuck', '烦', '生气', '坐不住']),
        'self_critical': any(token in lowered for token in ['my fault', 'hate myself', 'useless', 'worthless', 'I failed', '自责', '没用']),
    }

    candidates = []
    for key, template in AI_INTERVENTION_CATALOG.items():
        score = 0.0
        reasons = []

        if key == 'breathing_1min':
            if flags['anxious']:
                score += 6.0
                reasons.append('stress/anxiety signal')
            if mood < 45:
                score += 1.2
                reasons.append('mood is low')
        elif key == 'quiet_reset_5min':
            if flags['exhausted'] or energy < 45:
                score += 5.5
                reasons.append('energy looks low')
            if flags['eyes']:
                score += 1.0
                reasons.append('eyes may need rest')
        elif key == 'drink_water_glass':
            if flags['thirsty']:
                score += 6.0
                reasons.append('the user asked for water')
            if hydration < 42:
                score += 4.5
                reasons.append('hydration looks low')
            elif hydration < 55:
                score += 2.0
                reasons.append('hydration is below steady range')
            if prompt_signals['hydration_prompt_recent']:
                score -= 4.0
                reasons.append('water was already prompted recently')
        elif key == 'eye_reset_2min':
            if flags['eyes']:
                score += 5.5
                reasons.append('eye strain or screen cue')
            if prompt_signals['recent_pomodoro']:
                score += 2.5
                reasons.append('just finished focused work')
            if prompt_signals['eye_prompt_recent']:
                score -= 4.0
                reasons.append('eye exercise was already prompted recently')
        elif key == 'stretch_2min':
            if prompt_signals['recent_pomodoro']:
                score += 3.5
                reasons.append('just finished a pomodoro block')
            if flags['restless']:
                score += 1.5
                reasons.append('body reset may help')
        elif key == 'tiny_next_step':
            if flags['overwhelmed'] or flags['school']:
                score += 5.0
                reasons.append('the user sounds overwhelmed by tasks')
            if focus < 46:
                score += 2.5
                reasons.append('focus looks low')
        elif key == 'reset_walk_3min':
            if flags['restless']:
                score += 4.5
                reasons.append('a movement reset may fit better')
            if energy >= 40:
                score += 0.8
                reasons.append('energy is high enough for a short walk')
        elif key == 'kind_line_self':
            if flags['sad'] or flags['self_critical']:
                score += 5.0
                reasons.append('sadness or self-criticism signal')
            if mood < 42:
                score += 1.5
                reasons.append('mood looks quite low')

        history_entry = history.get(key, {})
        history_score = float(history_entry.get('profile_score') or 0.0)
        if history_score:
            score += history_score
            reasons.append(f'personal history score {history_score:+.1f}')

        if key in recent_keys:
            score -= 3.5
            reasons.append('same intervention was suggested recently')
        if key in active_keys:
            score -= 100.0
            reasons.append('same unfinished suggestion already exists')

        candidates.append({
            'key': key,
            'score': round(score, 2),
            'reason': '; '.join(reasons[:4]) or 'general reset fit',
            'history': {
                'avg_rating': history_entry.get('avg_rating'),
                'completion_rate': history_entry.get('completion_rate'),
                'profile_score': history_entry.get('profile_score', 0),
            },
            **template,
        })

    candidates.sort(key=lambda item: (item.get('score', 0), item.get('history', {}).get('avg_rating') or 0), reverse=True)
    preferred = candidates[0] if candidates else None
    history_rank = sorted(
        [
            {
                'key': key,
                'avg_rating': value.get('avg_rating'),
                'completion_rate': value.get('completion_rate'),
                'profile_score': value.get('profile_score', 0),
            }
            for key, value in history.items()
        ],
        key=lambda item: (item.get('profile_score') or 0, item.get('avg_rating') or 0),
        reverse=True,
    )[:5]
    return {
        'preferred_candidate': preferred,
        'ranked_candidates': candidates[:5],
        'history_rank': history_rank,
        'signals': {
            'hydration': hydration,
            'energy': energy,
            'focus': focus,
            'mood': mood,
            'mood_hint': mood_hint or None,
            'recent_pomodoro': prompt_signals['recent_pomodoro'],
            'hydration_prompt_recent': prompt_signals['hydration_prompt_recent'],
            'eye_prompt_recent': prompt_signals['eye_prompt_recent'],
        },
        'active_same_day_task': active_same_day,
        'active_keys': sorted(active_keys),
    }




def _extract_region_from_locale(locale_text: str | None) -> str | None:
    clean = str(locale_text or '').strip()
    if not clean:
        return None
    for token in re.split(r'[;,\s]+', clean):
        match = re.search(r'[-_]([A-Za-z]{2})$', token)
        if not match:
            match = re.search(r'[-_]([A-Za-z]{2})(?:[^A-Za-z].*)?$', token)
        if match:
            region = match.group(1).upper()
            if region == 'UK':
                region = 'GB'
            if region in CRISIS_SUPPORT_BY_REGION:
                return region
    return None


def _extract_region_from_time_zone(timezone_text: str | None) -> str | None:
    clean = str(timezone_text or '').strip()
    if not clean:
        return None
    if clean.startswith(('America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'America/Phoenix', 'Pacific/Honolulu', 'America/Indiana', 'America/Detroit', 'America/Boise', 'America/Anchorage')):
        return 'US'
    if clean.startswith(('America/Toronto', 'America/Vancouver', 'America/Edmonton', 'America/Winnipeg', 'America/Halifax', 'America/St_Johns', 'America/Regina', 'America/Moncton')):
        return 'CA'
    if clean.startswith('Europe/London'):
        return 'GB'
    if clean.startswith('Europe/Dublin'):
        return 'IE'
    if clean.startswith('Australia/'):
        return 'AU'
    if clean.startswith(('Pacific/Auckland', 'Pacific/Chatham')):
        return 'NZ'
    return None


def _resolve_support_region(payload: dict | None = None) -> str | None:
    payload = payload or {}
    locale_candidates = []
    if payload.get('browser_locale'):
        locale_candidates.append(str(payload.get('browser_locale')))
    for item in payload.get('browser_languages') or []:
        if item:
            locale_candidates.append(str(item))
    header_locale = request.headers.get('Accept-Language', '') if has_request_context() else ''
    if header_locale:
        locale_candidates.append(header_locale)

    for candidate in locale_candidates:
        region = _extract_region_from_locale(candidate)
        if region:
            return region

    timezone_candidates = []
    if payload.get('browser_timezone'):
        timezone_candidates.append(str(payload.get('browser_timezone')))
    if has_request_context():
        header_tz = request.headers.get('Time-Zone') or request.headers.get('X-Time-Zone')
        if header_tz:
            timezone_candidates.append(str(header_tz))

    for candidate in timezone_candidates:
        region = _extract_region_from_time_zone(candidate)
        if region:
            return region
    return None


def _care_text_is_high_risk(text: str | None) -> bool:
    lowered = _normalize_ai_text(text or '')
    if not lowered:
        return False
    return any(token in lowered for token in HIGH_RISK_SUPPORT_HINTS)


def _care_crisis_support_payload(payload: dict | None = None, user_text: str | None = None, mood_label: str | None = None) -> dict | None:
    high_risk = _care_text_is_high_risk(user_text)
    if not high_risk:
        return None

    region = _resolve_support_region(payload) or 'US'
    support = dict(CRISIS_SUPPORT_BY_REGION.get(region) or CRISIS_SUPPORT_BY_REGION['US'])
    support['region'] = region
    support['show_now'] = True
    return support


def _prune_client_states() -> None:
    cutoff = local_now().replace(tzinfo=None) - timedelta(days=CLIENT_STATE_RETENTION_DAYS)
    ClientState.query.filter(ClientState.created_at < cutoff).delete(synchronize_session=False)



def _store_client_state(user_id: int, state_key: str, payload: dict | None) -> None:
    if not payload:
        ClientState.query.filter_by(user_id=user_id, state_key=state_key).delete(synchronize_session=False)
        return

    serialized = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    now = local_now().replace(tzinfo=None)
    row = ClientState.query.filter_by(user_id=user_id, state_key=state_key).first()
    if row:
        row.payload_json = serialized
        row.created_at = now
    else:
        db.session.add(
            ClientState(
                user_id=user_id,
                state_key=state_key[:40],
                payload_json=serialized,
                created_at=now,
            )
        )
    _prune_client_states()
    db.session.flush()



def _peek_client_state_for_user(user_id: int, state_key: str) -> dict | None:
    row = ClientState.query.filter_by(user_id=user_id, state_key=state_key).first()
    if not row:
        return None
    try:
        payload = json.loads(row.payload_json)
    except Exception:
        logger.warning('Could not decode client state payload', exc_info=True, extra={'state_key': state_key, 'user_id': user_id})
        return None
    return payload if isinstance(payload, dict) else None


def _peek_client_state(state_key: str) -> dict | None:
    if not has_request_context() or not current_user.is_authenticated:
        return None
    return _peek_client_state_for_user(current_user.id, state_key)


def _consume_client_state(state_key: str) -> dict | None:
    if not has_request_context() or not current_user.is_authenticated:
        return None
    row = ClientState.query.filter_by(user_id=current_user.id, state_key=state_key).first()
    if not row:
        return None
    try:
        payload = json.loads(row.payload_json)
    except Exception:
        logger.warning('Could not decode client state payload', exc_info=True, extra={'state_key': state_key, 'user_id': current_user.id})
        payload = None
    db.session.delete(row)
    db.session.commit()
    return payload if isinstance(payload, dict) else None



def _consume_ai_suggestion_followup() -> dict | None:
    return _consume_client_state('pending_ai_suggestion_followup')



def _queue_ai_suggestion_followup(task: Task, follow_up_question: str | None = None) -> None:
    if not has_request_context() or not task:
        return
    _store_client_state(
        task.user_id,
        'pending_ai_suggestion_followup',
        {
            'task_id': int(task.id),
            'task_title': task.title,
            'question': (follow_up_question or task.ai_followup_question or '').strip() or 'After doing this AI suggestion, how much better do you feel out of 10 regarding the negativity detected earlier?',
        },
    )



def _consume_ai_suggestion_added() -> dict | None:
    return _consume_client_state('pending_ai_suggestion_added')



def _queue_ai_suggestion_added(task: Task, source_label: str | None = None) -> None:
    if not has_request_context() or not task:
        return
    clean_source = str(source_label or task.ai_generated_source or 'ai').replace('_', ' ').strip() or 'ai'
    _store_client_state(
        task.user_id,
        'pending_ai_suggestion_added',
        {
            'task_id': int(task.id),
            'task_title': task.title,
            'source_label': clean_source,
            'message': f"AI suggestion added: {task.title}",
            'detail': f"Added to today's todo list · source: {clean_source}",
        },
    )



def _decorate_feedback_with_ai_task(feedback: dict | None, task: Task | None, status: str = 'added') -> dict | None:
    if not feedback or not task:
        return feedback
    enhanced = dict(feedback)
    enhanced['ai_suggestion_task_title'] = task.title
    enhanced['ai_suggestion_status'] = status
    current_message = str(enhanced.get('message') or '').strip()
    if status == 'added':
        addition = f"I added a personalized todo for you: {task.title}."
    else:
        addition = f"You already have an unfinished AI suggestion: {task.title}. I did not add another one."
    enhanced['message'] = f"{current_message} {addition}".strip() if current_message else addition
    return enhanced



def _create_ai_suggestion_task(user_id: int, task_date: date, suggestion: dict[str, str], source_label: str) -> Task:
    title = _clean_text(suggestion.get('title'), 200) or 'Take a short reset'
    description = _clean_text(suggestion.get('description'), 1000)
    follow_up_question = _clean_text(suggestion.get('follow_up_question'), 240)
    suggestion_key = _clean_text(suggestion.get('suggestion_key'), 40) or _infer_ai_suggestion_key_from_text(title, description)
    source_value = (source_label or 'ai')[:30]
    task = Task(
        user_id=user_id,
        title=title,
        description=description or None,
        task_type='ai_suggestion',
        task_date=task_date,
        completed=False,
        sort_order=_get_next_sort_order(user_id, task_date),
        ai_generated_source=source_value,
        ai_suggestion_key=suggestion_key or None,
        ai_followup_question=follow_up_question or None,
    )
    db.session.add(task)
    db.session.flush()
    return task



def _maybe_create_ai_suggestion_task(
    user: User,
    source_text: str,
    detected_mood: str | None = None,
    target_date: date | None = None,
    source_label: str = 'ai',
) -> dict:
    combined_text = _clean_text(source_text, 3000)
    mood_hint = detected_mood
    if not combined_text and not mood_hint:
        return {'task': None, 'suggestion': None, 'status': None, 'intervention_context': None}

    negative = _mood_is_negative(mood_hint) if mood_hint else False
    if not negative:
        analysis = analyze_text_mood(combined_text, preferred=mood_hint or '')
        mood_hint = _normalize_mood_choice(analysis.get('mood_label'), '')[0]
        negative = mood_hint in NEGATIVE_MOOD_LABELS

    if not negative:
        return {'task': None, 'suggestion': None, 'status': None, 'intervention_context': None}

    chosen_date = target_date or local_today()
    intervention_context = _rank_personalized_interventions(user, combined_text or mood_hint or 'negative mood detected', detected_mood=mood_hint, target_date=chosen_date)
    preferred = intervention_context.get('preferred_candidate') or {}
    preferred_key = str(preferred.get('key') or '').strip() or None

    if preferred_key:
        same_key_open = next(
            (
                task for task in Task.query.filter_by(user_id=user.id, task_type='ai_suggestion', completed=False).order_by(Task.created_at.desc()).all()
                if _task_ai_suggestion_key(task) == preferred_key
            ),
            None,
        )
        if same_key_open:
            return {
                'task': same_key_open,
                'suggestion': _serialize_ai_suggestion_from_task(same_key_open),
                'status': 'existing_same_key',
                'intervention_context': intervention_context,
            }

    active_same_day = intervention_context.get('active_same_day_task')
    if active_same_day:
        return {
            'task': active_same_day,
            'suggestion': _serialize_ai_suggestion_from_task(active_same_day),
            'status': 'existing_same_day',
            'intervention_context': intervention_context,
        }

    suggestion = recommend_micro_intervention(
        combined_text,
        detected_mood=mood_hint,
        wellness_scores=_score_snapshot(user),
        intervention_context=intervention_context,
    )
    task = _create_ai_suggestion_task(user.id, chosen_date, suggestion, source_label)
    _queue_ai_suggestion_added(task, source_label)
    _log_activity_entry(
        user.id,
        'task',
        'AI suggestion added',
        f"{task.title} ({chosen_date.isoformat()}) · source: {source_label} · key: {_task_ai_suggestion_key(task) or 'custom'}",
        impacts=[
            {'key': 'overall', 'label': 'Overall Wellness', 'value': 0, 'signed': '+0'},
        ],
    )
    return {
        'task': task,
        'suggestion': suggestion,
        'status': 'added',
        'intervention_context': intervention_context,
    }


def _build_mood_trend_payload(user_id: int, days: int = 14) -> dict:
    start_dt = (local_now() - timedelta(days=max(1, days) - 1)).replace(tzinfo=None)
    rows = MoodEntry.query.filter(
        MoodEntry.user_id == user_id,
        MoodEntry.event_at >= start_dt,
    ).order_by(MoodEntry.event_at.asc()).all()

    by_day = {}
    for row in rows:
        day_key = row.event_at.date().isoformat()
        bucket = by_day.setdefault(day_key, {'values': [], 'latest': row})
        bucket['values'].append(int(row.mood_value or 50))
        if row.event_at >= bucket['latest'].event_at:
            bucket['latest'] = row

    points = []
    current_day = start_dt.date()
    end_day = local_today()
    last_value = 50
    while current_day <= end_day:
        key = current_day.isoformat()
        bucket = by_day.get(key)
        if bucket:
            last_value = int(round(sum(bucket['values']) / max(1, len(bucket['values']))))
            latest = bucket['latest']
            badge = _mood_badge_payload(latest.mood_label, latest.mood_custom_text)
            points.append({
                'date': key,
                'short_date': current_day.strftime('%m/%d'),
                'value': last_value,
                'display': badge['display'],
                'color': badge['color'],
            })
        else:
            points.append({
                'date': key,
                'short_date': current_day.strftime('%m/%d'),
                'value': last_value,
                'display': 'No new entry',
                'color': '#cbd5e1',
            })
        current_day += timedelta(days=1)

    recent_entries = []
    for row in reversed(rows[-8:]):
        badge = _mood_badge_payload(row.mood_label, row.mood_custom_text)
        recent_entries.append({
            'source': row.source.replace('_', ' '),
            'display': badge['display'],
            'summary': row.summary,
            'event_at': row.event_at,
            'color': badge['color'],
        })

    return {
        'points': points,
        'recent_entries': recent_entries,
        'latest': recent_entries[0] if recent_entries else None,
    }


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)



def local_today() -> date:
    return local_now().date()



def _parse_date(value: str, fallback: date | None = None) -> date:
    clean = (value or '').strip()
    if not clean:
        return fallback or local_today()
    try:
        return datetime.strptime(clean, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return fallback or local_today()



def _parse_time(value: str | None):
    clean = (value or '').strip()
    if not clean:
        return None
    try:
        return datetime.strptime(clean, '%H:%M').time()
    except (TypeError, ValueError):
        return None



def _parse_int(value, default=0, minimum=None, maximum=None):
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed



def _parse_float(value, default=0.0, minimum=None, maximum=None):
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    if math.isnan(parsed) or math.isinf(parsed):
        return default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _normalize_care_messages(raw_messages) -> list[dict[str, str]]:
    normalized = []
    for item in raw_messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get('role') or '').strip().lower()
        content = str(item.get('content') or '').strip()
        if role not in {'user', 'assistant'} or not content:
            continue
        normalized.append({'role': role, 'content': content[:AI_MAX_MESSAGE_CHARS]})
    return normalized[-20:]



def _normalize_task_text(value: str | None) -> str:
    return re.sub(r'\s+', ' ', (value or '').strip().lower())



def _care_intent_match(text: str | None, patterns) -> bool:
    normalized = _normalize_task_text(text)
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in patterns)


def _care_requested_quick_action(text: str | None) -> str | None:
    if _care_intent_match(text, CARE_EYE_EXERCISE_INTENT_PATTERNS):
        return 'eye_exercise'
    if _care_intent_match(text, CARE_WATER_INTENT_PATTERNS):
        return 'hydration'
    return None


def _ensure_care_eye_exercise_prompt(user_id: int) -> EyeExercisePrompt:
    active_prompt = _get_active_eye_exercise_prompt(user_id)
    if active_prompt:
        return active_prompt

    state = _get_or_create_eye_exercise_state(user_id)
    existing = EyeExercisePrompt.query.filter(
        EyeExercisePrompt.user_id == user_id,
        EyeExercisePrompt.response_status.in_(['pending', 'watching', 'not_yet']),
        db.func.date(EyeExercisePrompt.due_at) == local_today(),
    ).order_by(EyeExercisePrompt.created_at.desc(), EyeExercisePrompt.id.desc()).first()
    if existing:
        state.active_prompt_id = existing.id
        state.updated_at = local_now().replace(tzinfo=None)
        return existing

    now = local_now().replace(tzinfo=None)
    prompt = EyeExercisePrompt(
        user_id=user_id,
        focus_minutes_trigger=0,
        threshold_minutes=0,
        video_url=EYE_EXERCISE_VIDEO_URL,
        response_status='pending',
        due_at=now,
        created_at=now,
    )
    db.session.add(prompt)
    db.session.flush()
    state.active_prompt_id = prompt.id
    state.updated_at = now
    return prompt


def _ensure_care_hydration_prompt(user: User) -> HydrationPrompt:
    existing = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user.id,
        HydrationPrompt.prompt_type == 'care_water',
        HydrationPrompt.response_status.in_(['pending', 'not_yet']),
        db.func.date(HydrationPrompt.due_at) == local_today(),
    ).order_by(HydrationPrompt.created_at.desc(), HydrationPrompt.id.desc()).first()
    if existing:
        return existing

    now = local_now().replace(tzinfo=None)
    prompt = HydrationPrompt(
        user_id=user.id,
        log_id=_get_or_create_log_for_today(user.id).id,
        prompt_type='care_water',
        message='You said you want to drink water now. Choose your drink and amount here.',
        beverage='water',
        response_status='pending',
        due_at=now,
        created_at=now,
    )
    db.session.add(prompt)
    db.session.flush()
    return prompt


def _care_quick_action_payload(user: User, text: str | None) -> dict | None:
    action = _care_requested_quick_action(text)
    if action == 'eye_exercise':
        prompt = _ensure_care_eye_exercise_prompt(user.id)
        return {
            'type': 'eye_exercise',
            'prompt': _serialize_eye_exercise_prompt(prompt),
        }
    if action == 'hydration':
        prompt = _ensure_care_hydration_prompt(user)
        return {
            'type': 'hydration',
            'prompt': _serialize_prompt(prompt),
        }
    return None


def _task_type(task: Task) -> str:
    stored = (getattr(task, 'task_type', None) or '').strip().lower()
    if stored:
        return stored
    normalized = _normalize_task_text(task.title)
    if normalized in {item[1] for item in MEAL_TASKS}:
        return 'meal'
    if 'eye exercise' in normalized or '眼保健操' in (task.title or ''):
        return 'eye_exercise'
    return 'regular'



def _task_is_meal(task: Task) -> bool:
    return _task_type(task) == 'meal'



def _task_is_hydration(task: Task) -> bool:
    normalized = _normalize_task_text(task.title)
    return any(word in normalized for word in ['drink', 'water', 'hydration', 'glass of water'])



def _task_is_eye_exercise(task: Task) -> bool:
    return _task_type(task) == 'eye_exercise' or 'eye exercise' in _normalize_task_text(task.title) or '眼保健操' in (task.title or '')



def _task_is_ai_suggestion(task: Task) -> bool:
    return _task_type(task) == 'ai_suggestion'



def _task_is_focus_eligible(task: Task) -> bool:
    return not _task_is_meal(task) and not _task_is_hydration(task) and not _task_is_eye_exercise(task) and not _task_is_ai_suggestion(task)



def _infer_beverage_from_text(text: str | None) -> str:
    normalized = _normalize_task_text(text)
    if 'milk' in normalized:
        return 'milk'
    if 'coke' in normalized or 'cola' in normalized:
        return 'coke'
    return 'water'



def _meal_key_from_text(text: str | None, event_at: datetime | None = None) -> str | None:
    lowered = (text or '').lower()
    if any(word in lowered for word in ['breakfast', '早餐']):
        return 'breakfast'
    if any(word in lowered for word in ['lunch', '午饭', '午餐']):
        return 'lunch'
    if any(word in lowered for word in ['dinner', '晚饭', '晚餐', 'supper']):
        return 'dinner'
    if not any(word in lowered for word in ['meal', 'snack', 'ate', 'eat', 'eating', '吃', '饭', '餐']):
        return None

    hour = (event_at or local_now()).hour
    if hour < 11:
        return 'breakfast'
    if hour < 16:
        return 'lunch'
    return 'dinner'



def _sync_meal_task_completion(user_id: int, task_date: date, text: str | None, event_at: datetime | None = None) -> Task | None:
    meal_key = _meal_key_from_text(text, event_at)
    if not meal_key:
        return None

    task = Task.query.filter_by(user_id=user_id, task_date=task_date).filter(
        db.func.lower(Task.title) == meal_key
    ).first()
    if not task:
        return None

    task.completed = True
    task.completed_at = (event_at or local_now()).replace(tzinfo=None)

    description_bits = []
    if text:
        description_bits.append(text[:120])
    if task.completed_at:
        description_bits.append(task.completed_at.strftime('%H:%M'))
    task.description = ' · '.join(description_bits) if description_bits else task.description
    return task



def _ensure_daily_default_tasks(user_id: int, task_date: date) -> None:
    existing = Task.query.filter_by(user_id=user_id, task_date=task_date).all()
    existing_titles = set()
    for task in existing:
        normalized = _normalize_task_text(task.title)
        existing_titles.add(normalized)
        if normalized in {item[1] for item in MEAL_TASKS} and (task.task_type or '').strip().lower() != 'meal':
            task.task_type = 'meal'
    next_order = _get_next_sort_order(user_id, task_date)
    created = False
    for title, key in MEAL_TASKS:
        if key in existing_titles:
            continue
        db.session.add(
            Task(
                user_id=user_id,
                title=title,
                description=None,
                task_type='meal',
                task_date=task_date,
                completed=False,
                sort_order=next_order,
            )
        )
        next_order += 1
        created = True
    if created:
        db.session.flush()



def _event_sort_key(event: CalendarEvent):
    return (event.event_time is None, event.event_time or datetime.min.time(), event.created_at)



def _get_next_sort_order(user_id: int, task_date: date) -> int:
    current_max = db.session.query(db.func.max(Task.sort_order)).filter_by(user_id=user_id, task_date=task_date).scalar()
    return int(current_max or 0) + 1



def _roll_over_pending_tasks(user_id: int) -> dict[str, int]:
    today = local_today()
    overdue_query = Task.query.filter(
        Task.user_id == user_id,
        Task.task_date < today,
        Task.completed.is_(False),
        db.or_(Task.task_type.is_(None), Task.task_type != 'meal'),
    ).order_by(Task.task_date.asc(), Task.sort_order.asc(), Task.created_at.asc())

    overdue_count = overdue_query.count()
    if not overdue_count:
        return {'moved_count': 0, 'remaining_count': 0}

    overdue_tasks = overdue_query.limit(TASK_ROLLOVER_MAX_PER_DAY).all()
    next_order = _get_next_sort_order(user_id, today)
    for task in overdue_tasks:
        task.task_date = today
        task.sort_order = next_order
        next_order += 1

    remaining_count = max(0, overdue_count - len(overdue_tasks))
    if remaining_count:
        logger.info(
            'Task rollover capped for user',
            extra={
                'user_id': user_id,
                'moved_count': len(overdue_tasks),
                'remaining_count': remaining_count,
                'daily_cap': TASK_ROLLOVER_MAX_PER_DAY,
            },
        )

    db.session.flush()
    return {'moved_count': len(overdue_tasks), 'remaining_count': remaining_count}



def _sync_overdue_tasks_once_per_day(user: User) -> None:
    today = local_today()
    if user.last_task_rollover_on == today:
        return
    rollover_result = _roll_over_pending_tasks(user.id)
    user.last_task_rollover_on = today
    db.session.commit()
    if rollover_result['moved_count'] or rollover_result['remaining_count']:
        logger.info(
            'Overdue task sync complete',
            extra={
                'user_id': user.id,
                'moved_count': rollover_result['moved_count'],
                'remaining_count': rollover_result['remaining_count'],
            },
        )



def _prune_activity_entries_if_needed(user_id: int, now: datetime | None = None) -> None:
    user = db.session.get(User, user_id)
    if not user:
        return

    current_time = (now or local_now()).replace(tzinfo=None)
    last_pruned = user.last_activity_pruned_at
    if last_pruned and (current_time - last_pruned) < timedelta(hours=ACTIVITY_PRUNE_INTERVAL_HOURS):
        return

    cutoff = current_time - timedelta(days=ACTIVITY_ENTRY_RETENTION_DAYS)
    ActivityEntry.query.filter(
        ActivityEntry.user_id == user_id,
        ActivityEntry.event_at < cutoff,
    ).delete(synchronize_session=False)

    overflow_subquery = (
        db.session.query(ActivityEntry.id)
        .filter(ActivityEntry.user_id == user_id)
        .order_by(ActivityEntry.event_at.desc(), ActivityEntry.id.desc())
        .offset(ACTIVITY_ENTRY_MAX_ROWS)
        .subquery()
    )
    ActivityEntry.query.filter(ActivityEntry.id.in_(db.session.query(overflow_subquery.c.id))).delete(synchronize_session=False)

    user.last_activity_pruned_at = current_time
    db.session.flush()



def _goal_completion_percent(user: User, log: DailyLog | None) -> int:
    if not log:
        return 0

    targets = [
        min(max(int(log.water_ml or 0) / max(int(user.daily_water_goal_ml or 2000), 1), 0), 1),
        min(max(float(log.sleep_hours or 0) / max(float(user.daily_sleep_goal_hours or 8.0), 0.1), 0), 1),
        min(max(int(log.steps or 0) / max(int(user.daily_step_goal or 8000), 1), 0), 1),
        min(max(int(log.exercise_minutes or 0) / max(int(user.daily_exercise_goal_minutes or 30), 1), 0), 1),
    ]
    return int(round(sum(targets) / len(targets) * 100))



def _heat_color(percent: int) -> str:
    safe = max(0, min(100, int(percent or 0)))
    red = (255, 232, 232)
    green = (229, 248, 235)
    rgb = tuple(int(round(red[i] + (green[i] - red[i]) * (safe / 100.0))) for i in range(3))
    return '#{:02x}{:02x}{:02x}'.format(*rgb)



def _calendar_preview_title(title: str) -> str:
    clean = (title or '').strip()
    for prefix in ['What just did: ', 'Pomodoro done: ']:
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
    return clean or 'Saved item'



def _selected_day_finished_items(events, tasks):
    combined = []

    for event in events:
        sort_time = event.event_time or datetime.min.time()
        combined.append(
            {
                'kind': 'event',
                'id': event.id,
                'title': _calendar_preview_title(event.title),
                'description': (event.description or '').strip() or None,
                'time_label': event.event_time.strftime('%H:%M') if event.event_time else None,
                'completed': True,
                'sort_key': (0, sort_time, event.created_at or datetime.min),
                'delete_endpoint': 'delete_event',
            }
        )

    for task in tasks:
        if not task.completed:
            continue
        combined.append(
            {
                'kind': 'task',
                'id': task.id,
                'title': task.title,
                'description': (task.description or '').strip() or None,
                'task_type': _task_type(task),
                'time_label': None,
                'completed': True,
                'sort_key': (1, datetime.min.time(), task.sort_order, task.created_at or datetime.min),
                'delete_endpoint': 'delete_task',
            }
        )

    combined.sort(key=lambda item: item['sort_key'])
    return combined



def _month_grid(year: int, month: int, tasks, events, logs, user: User, selected_date: date | None = None):
    cal = Calendar(firstweekday=0)
    task_map: dict[date, list[Task]] = {}
    event_map: dict[date, list[CalendarEvent]] = {}
    log_map: dict[date, DailyLog] = {}

    for task in tasks:
        task_map.setdefault(task.task_date, []).append(task)
    for event in events:
        event_map.setdefault(event.event_date, []).append(event)
    for log in logs:
        log_map[log.log_date] = log

    weeks = []
    for week in cal.monthdatescalendar(year, month):
        week_days = []
        for day in week:
            day_events = sorted(event_map.get(day, []), key=_event_sort_key)
            day_tasks = sorted(task_map.get(day, []), key=lambda item: (item.sort_order, item.created_at))
            goal_percent = _goal_completion_percent(user, log_map.get(day))
            week_days.append(
                {
                    'date': day,
                    'in_month': day.month == month,
                    'tasks': day_tasks,
                    'events': day_events,
                    'event_previews': [_calendar_preview_title(event.title) for event in day_events[:2]],
                    'goal_percent': goal_percent,
                    'heat_color': _heat_color(goal_percent),
                    'is_today': day == local_today(),
                    'is_selected': bool(selected_date and day == selected_date),
                }
            )
        weeks.append(week_days)
    return weeks



def _ensure_baseline_scores(user: User):
    if user.wellness_updated_at:
        return
    user.hydration_score = 50
    user.energy_score = 50
    user.fitness_score = 50
    user.focus_score = 50
    user.mood_score = 50
    user.overall_wellness_score = 50
    user.avatar_emoji = user.avatar_emoji or '🙂'
    user.goal_progress_intensity = (user.goal_progress_intensity or 'medium').strip().lower() or 'medium'
    user.wellness_summary = 'Scores start at a neutral 50 and move with your recent habits.'
    user.wellness_updated_at = local_now().replace(tzinfo=None)
    db.session.flush()



def _profile_locked(user: User) -> bool:
    return (
        user.age is not None
        and user.weight_kg is not None
        and user.height_cm is not None
        and user.gender_identity is not None
    )



def _get_or_create_log_for_date(user_id: int, log_date: date) -> DailyLog:
    existing = DailyLog.query.filter_by(user_id=user_id, log_date=log_date).order_by(DailyLog.id.asc()).first()
    if existing:
        return existing

    log = DailyLog(user_id=user_id, log_date=log_date)
    db.session.add(log)
    try:
        db.session.flush()
        return log
    except IntegrityError:
        db.session.rollback()
        existing = DailyLog.query.filter_by(user_id=user_id, log_date=log_date).order_by(DailyLog.id.asc()).first()
        if existing:
            return existing
        raise



def _get_or_create_log_for_today(user_id: int) -> DailyLog:
    return _get_or_create_log_for_date(user_id, local_today())


def _water_limit_error(current_total_ml: int | float | None, added_ml: int | float | None) -> str | None:
    amount_ml = max(int(added_ml or 0), 0)
    current_ml = max(int(current_total_ml or 0), 0)
    if amount_ml > MAX_SINGLE_WATER_ML:
        return f'One water entry cannot exceed {MAX_SINGLE_WATER_ML} ml.'
    if current_ml + amount_ml > MAX_DAILY_WATER_ML:
        return f"Today's water total cannot exceed {MAX_DAILY_WATER_ML} ml."
    return None


def _get_active_care_chat_session(user_id: int) -> CareChatSession | None:
    return CareChatSession.query.filter_by(user_id=user_id, ended_at=None).order_by(CareChatSession.last_activity_at.desc(), CareChatSession.started_at.desc()).first()


def _get_or_create_active_care_chat_session(user_id: int) -> CareChatSession:
    active = _get_active_care_chat_session(user_id)
    if active:
        return active
    active = CareChatSession(user_id=user_id, started_at=local_now().replace(tzinfo=None), last_activity_at=local_now().replace(tzinfo=None), message_count=0)
    db.session.add(active)
    db.session.flush()
    return active


def _get_care_chat_session_for_user(user_id: int, session_id: str, require_active: bool = True) -> CareChatSession | None:
    query = CareChatSession.query.filter_by(id=session_id, user_id=user_id)
    if require_active:
        query = query.filter(CareChatSession.ended_at.is_(None))
    return query.first()


def _care_chat_messages_for_session(session_id: str) -> list[CareChatMessage]:
    return CareChatMessage.query.filter_by(session_id=session_id).order_by(CareChatMessage.created_at.asc(), CareChatMessage.id.asc()).all()


def _care_chat_history_payload(session_id: str) -> list[dict[str, str]]:
    payload = []
    for row in _care_chat_messages_for_session(session_id):
        payload.append({
            'role': row.role,
            'content': row.content,
            'time': (row.created_at.strftime('%I:%M %p').lstrip('0') or row.created_at.strftime('%I:%M %p')) if row.created_at else '',
        })
    return payload


def _append_care_chat_message(care_session: CareChatSession, role: str, content: str) -> CareChatMessage | None:
    clean_role = (role or '').strip().lower()
    clean_content = _clean_text(content, AI_MAX_MESSAGE_CHARS)
    if clean_role not in {'user', 'assistant'} or not clean_content:
        return None
    timestamp = local_now().replace(tzinfo=None)
    message = CareChatMessage(session_id=care_session.id, role=clean_role, content=clean_content, created_at=timestamp)
    db.session.add(message)
    care_session.last_activity_at = timestamp
    care_session.message_count = int(care_session.message_count or 0) + 1
    db.session.flush()
    return message


def _care_chat_messages_for_ai(session_id: str) -> list[dict[str, str]]:
    return [
        {'role': row.role, 'content': row.content}
        for row in _care_chat_messages_for_session(session_id)
        if row.role in {'user', 'assistant'} and row.content
    ]



def _normalize_beverage(beverage: str, custom_beverage: str = '') -> str:
    beverage_value = _clean_text(beverage or 'water', 60).lower()
    custom_value = _clean_text(custom_beverage, 120)
    if beverage_value == 'other':
        return custom_value or 'water'
    return beverage_value or 'water'



def _parse_clock_text(value: str | None, fallback: str) -> datetime.time:
    text_value = (value or fallback or '').strip() or fallback
    try:
        return datetime.strptime(text_value, '%H:%M').time()
    except ValueError:
        return datetime.strptime(fallback, '%H:%M').time()



def _aware_local_datetime(target_date: date, clock_time: datetime.time, fold: int = 0) -> datetime:
    candidate = datetime.combine(target_date, clock_time).replace(tzinfo=LOCAL_TZ, fold=fold)
    normalized = candidate.astimezone(UTC_TZ).astimezone(LOCAL_TZ)
    if normalized.replace(tzinfo=None) != candidate.replace(tzinfo=None):
        candidate = normalized
    return candidate



def _local_duration_hours(start_dt: datetime, end_dt: datetime) -> float:
    return (end_dt.astimezone(UTC_TZ) - start_dt.astimezone(UTC_TZ)).total_seconds() / 3600.0



def _sleep_schedule_for_date(user: User, target_date: date) -> tuple[datetime, datetime]:
    wake_time = _parse_clock_text(user.optimal_wake_time, '07:00')
    bed_time = _parse_clock_text(user.optimal_bedtime, '22:00')
    wake_dt = _aware_local_datetime(target_date, wake_time, fold=1)
    bedtime_date = target_date + timedelta(days=1) if bed_time <= wake_time else target_date
    bedtime_dt = _aware_local_datetime(bedtime_date, bed_time, fold=0)
    return wake_dt.replace(tzinfo=None), bedtime_dt.replace(tzinfo=None)


HYDRATION_SLOT_META = [
    ('scheduled_wake', 'Wake-up glass', 'hydration_wake_time'),
    ('scheduled_breakfast', 'Breakfast glass', 'hydration_breakfast_time'),
    ('scheduled_lunch', 'Lunch glass', 'hydration_lunch_time'),
    ('scheduled_dinner', 'Dinner glass', 'hydration_dinner_time'),
]
HYDRATION_SLOT_LABELS = {slot_key: label for slot_key, label, _ in HYDRATION_SLOT_META}


def _hydration_active_prompt_filter():
    fixed_filters = [HydrationPrompt.prompt_type == slot_key for slot_key, _, _ in HYDRATION_SLOT_META]
    return db.or_(*fixed_filters, HydrationPrompt.prompt_type.like('scheduled_extra_%'))


def _hydration_prompt_label(prompt_type: str | None) -> str:
    normalized = (prompt_type or '').strip().lower()
    if normalized in HYDRATION_SLOT_LABELS:
        return HYDRATION_SLOT_LABELS[normalized]
    if normalized.startswith('scheduled_extra_'):
        return 'Extra water'
    if normalized == 'care_water':
        return 'Drink water'
    return 'Water reminder'


def _round_time_to_five_minutes(value: datetime) -> datetime:
    rounded = value.replace(second=0, microsecond=0)
    remainder = rounded.minute % 5
    if remainder:
        rounded += timedelta(minutes=(5 - remainder))
    return rounded


def _shift_hydration_candidate(
    candidate: datetime,
    blocked_times: list[datetime],
    window_start: datetime,
    window_end: datetime,
    min_gap_minutes: int,
) -> datetime | None:
    if window_end < window_start:
        return None

    min_gap_seconds = max(int(min_gap_minutes), 1) * 60
    clamped = max(min(candidate, window_end), window_start)

    def clear(dt: datetime) -> bool:
        return all(abs((dt - blocked).total_seconds()) >= min_gap_seconds for blocked in blocked_times)

    rounded = _round_time_to_five_minutes(clamped)
    max_steps = max(int((window_end - window_start).total_seconds() // 300), 0) + 2
    for step in range(max_steps):
        if step == 0:
            candidates = [rounded]
        else:
            delta = timedelta(minutes=5 * step)
            candidates = [rounded + delta, rounded - delta]
        for shifted in candidates:
            shifted = _round_time_to_five_minutes(shifted)
            if shifted < window_start or shifted > window_end:
                continue
            if clear(shifted):
                return shifted

    return None


def _build_extra_hydration_rows(
    start_dt: datetime,
    end_dt: datetime,
    blocked_times: list[datetime],
    extra_count: int,
) -> list[dict[str, str]]:
    if extra_count <= 0 or end_dt <= start_dt:
        return []

    window_start = _round_time_to_five_minutes(start_dt)
    window_end = _round_time_to_five_minutes(end_dt)
    if window_end <= window_start:
        return []

    duration_seconds = max((window_end - window_start).total_seconds(), 1)
    placed: list[datetime] = []
    rows: list[dict[str, str]] = []

    for index in range(1, extra_count + 1):
        fraction = index / (extra_count + 1)
        candidate = window_start + timedelta(seconds=duration_seconds * fraction)
        adjusted = _shift_hydration_candidate(
            candidate,
            blocked_times + placed,
            window_start,
            window_end,
            HYDRATION_EXTRA_MIN_GAP_MINUTES,
        )
        if adjusted is None:
            break
        placed.append(adjusted)
        time_text = adjusted.strftime('%H:%M')
        rows.append({
            'slot_key': f"scheduled_extra_{time_text.replace(':', '')}",
            'label': 'Extra water',
            'time_text': time_text,
            'display_time': adjusted.strftime('%I:%M %p').lstrip('0'),
            'due_at': adjusted,
        })

    rows.sort(key=lambda item: item['due_at'])
    return rows


def _water_logged_for_date(user_id: int, target_date: date) -> int:
    log = DailyLog.query.filter_by(user_id=user_id, log_date=target_date).order_by(DailyLog.id.desc()).first()
    return max(int(log.water_ml or 0), 0) if log else 0


def _hydration_goal_plan(user: User, target_date: date | None = None) -> dict:
    chosen_date = target_date or local_today()
    _ensure_hydration_schedule_defaults(user)
    fixed_rows = _hydration_schedule_rows(user)
    wake_dt, bedtime_dt = _sleep_schedule_for_date(user, chosen_date)
    now = local_now().replace(tzinfo=None) if chosen_date == local_today() else wake_dt
    day_end = bedtime_dt - timedelta(minutes=15)
    grace_dt = now - timedelta(minutes=HYDRATION_DUE_GRACE_MINUTES)

    existing_fixed_prompts = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user.id,
        HydrationPrompt.prompt_type.in_([slot_key for slot_key, _, _ in HYDRATION_SLOT_META]),
        db.func.date(HydrationPrompt.due_at) == chosen_date,
    ).all()
    existing_fixed_by_type = {row.prompt_type: row for row in existing_fixed_prompts}

    fixed_slots = []
    for row in fixed_rows:
        due_at = datetime.combine(chosen_date, _parse_clock_text(row['time_text'], row['time_text']))
        existing_prompt = existing_fixed_by_type.get(row['slot_key'])
        prompt_status = (existing_prompt.response_status or '').strip().lower() if existing_prompt else 'pending'
        if prompt_status in {'finished', 'dismissed', 'missed'}:
            continue
        fixed_slots.append({**row, 'due_at': due_at})
    fixed_slots.sort(key=lambda item: item['due_at'])

    active_fixed_rows = [
        row for row in fixed_slots
        if row['due_at'] >= grace_dt and row['due_at'] <= day_end
    ]

    current_water_ml = _water_logged_for_date(user.id, chosen_date)
    goal_ml = max(int(user.daily_water_goal_ml or 0), GLASS_VOLUME_ML)
    fixed_remaining_ml = len(active_fixed_rows) * GLASS_VOLUME_ML
    remaining_after_fixed_ml = max(goal_ml - current_water_ml - fixed_remaining_ml, 0)
    extra_count = min(max(math.ceil(remaining_after_fixed_ml / GLASS_VOLUME_ML), 0), 12)
    extra_rows = _build_extra_hydration_rows(
        max(now + timedelta(minutes=10), wake_dt),
        day_end,
        [row['due_at'] for row in active_fixed_rows if row['due_at'] >= now],
        extra_count,
    )

    planned_remaining_ml = fixed_remaining_ml + (len(extra_rows) * GLASS_VOLUME_ML)
    remaining_after_plan_ml = max(goal_ml - current_water_ml - planned_remaining_ml, 0)

    active_anchor_count = len(active_fixed_rows)
    extra_count_actual = len(extra_rows)
    planned_count = active_anchor_count + extra_count_actual

    if planned_count > 0:
        preview_text = (
            f"Today you have {current_water_ml} / {goal_ml} ml logged. "
            f"From now, WellHabit currently plans {planned_count} reminder"
            f"{'s' if planned_count != 1 else ''} for the rest of today: "
            f"{active_anchor_count} anchor and {extra_count_actual} extra. "
            "The four anchor times shown above are your default daily schedule, not four additional glasses on top of this plan."
        )
    elif remaining_after_plan_ml > 0:
        preview_text = (
            f"Today you have {current_water_ml} / {goal_ml} ml logged. "
            f"There is still about {remaining_after_plan_ml} ml left today, but there is not enough time left to space more automatic reminders well."
        )
    else:
        preview_text = (
            f"Today you have {current_water_ml} / {goal_ml} ml logged. "
            "Your remaining goal is already covered by the reminders still left today."
        )

    return {
        'goal_ml': goal_ml,
        'current_water_ml': current_water_ml,
        'current_glasses_text': _format_glasses(current_water_ml),
        'remaining_ml': max(goal_ml - current_water_ml, 0),
        'remaining_glasses_text': _format_glasses(max(goal_ml - current_water_ml, 0)),
        'active_fixed_rows': active_fixed_rows,
        'extra_rows': extra_rows,
        'active_anchor_count': active_anchor_count,
        'extra_count': extra_count_actual,
        'planned_count': planned_count,
        'preview_text': preview_text,
        'planned_remaining_ml': planned_remaining_ml,
        'remaining_after_plan_ml': remaining_after_plan_ml,
    }


def _default_hydration_schedule_map(user: User) -> dict[str, str]:
    wake_text = (user.optimal_wake_time or '07:00').strip()[:5] or '07:00'
    wake_time = _parse_clock_text(wake_text, '07:00')
    wake_dt = datetime.combine(local_today(), wake_time)
    defaults = {
        'hydration_wake_time': wake_dt.strftime('%H:%M'),
        'hydration_breakfast_time': (wake_dt + timedelta(hours=1)).strftime('%H:%M'),
        'hydration_lunch_time': '12:30',
        'hydration_dinner_time': '18:30',
    }
    return defaults



def _ensure_hydration_schedule_defaults(user: User) -> None:
    defaults = _default_hydration_schedule_map(user)
    changed = False
    for field_name, fallback in defaults.items():
        current_value = (getattr(user, field_name, None) or '').strip()[:5]
        normalized_value = _parse_clock_text(current_value or fallback, fallback).strftime('%H:%M')
        if current_value != normalized_value:
            setattr(user, field_name, normalized_value)
            changed = True
    if changed:
        db.session.flush()



def _hydration_schedule_rows(user: User) -> list[dict[str, str]]:
    _ensure_hydration_schedule_defaults(user)
    rows = []
    for slot_key, label, field_name in HYDRATION_SLOT_META:
        time_text = _parse_clock_text(getattr(user, field_name, None), _default_hydration_schedule_map(user)[field_name]).strftime('%H:%M')
        rows.append({
            'slot_key': slot_key,
            'label': label,
            'field_name': field_name,
            'time_text': time_text,
            'display_time': datetime.strptime(time_text, '%H:%M').strftime('%I:%M %p').lstrip('0'),
        })
    return rows



def _retire_legacy_hydration_prompts(user_id: int, target_date: date) -> None:
    legacy = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user_id,
        HydrationPrompt.prompt_type.in_(['morning', 'meal_followup', 'meal_now', 'meal_plus_2h']),
        HydrationPrompt.response_status.in_(['pending', 'not_yet']),
        db.func.date(HydrationPrompt.due_at) == target_date,
    ).all()
    for prompt in legacy:
        prompt.response_status = 'dismissed'
        prompt.responded_at = local_now().replace(tzinfo=None)



def _hydrate_prompt_message_from_slot(label: str, due_at: datetime, user: User | None = None, extra_context: dict | None = None) -> str:
    time_label = due_at.strftime('%I:%M %p').lstrip('0')
    if (label or '').lower() == 'extra water' and user is not None:
        goal_ml = max(int(user.daily_water_goal_ml or 0), GLASS_VOLUME_ML)
        current_ml = int((extra_context or {}).get('current_water_ml') or 0)
        remaining_ml = max(goal_ml - current_ml, 0)
        return (
            f"Extra water reminder: have about one glass around {time_label}. "
            f"You still need roughly {remaining_ml} ml today to reach your {goal_ml} ml goal."
        )
    return f"{label} reminder: have one glass of water around {time_label}."



def _sync_goal_based_hydration_prompts(user: User, target_date: date | None = None) -> None:
    chosen_date = target_date or local_today()
    if chosen_date != local_today() or not _profile_locked(user):
        return

    _ensure_hydration_schedule_defaults(user)
    _retire_legacy_hydration_prompts(user.id, chosen_date)
    now = local_now().replace(tzinfo=None)
    grace_cutoff = now - timedelta(minutes=HYDRATION_DUE_GRACE_MINUTES)
    plan = _hydration_goal_plan(user, chosen_date)

    desired_rows = list(plan['active_fixed_rows']) + list(plan['extra_rows'])
    desired_by_type = {}
    for row in desired_rows:
        desired_by_type[row['slot_key']] = {
            'label': row['label'],
            'due_at': row['due_at'],
            'message': _hydrate_prompt_message_from_slot(row['label'], row['due_at'], user=user, extra_context=plan),
        }

    existing_rows = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user.id,
        _hydration_active_prompt_filter(),
        db.func.date(HydrationPrompt.due_at) == chosen_date,
    ).order_by(HydrationPrompt.due_at.asc(), HydrationPrompt.id.asc()).all()
    existing_by_type = {row.prompt_type: row for row in existing_rows}

    for prompt in existing_rows:
        if prompt.prompt_type not in desired_by_type and prompt.response_status in {'pending', 'not_yet'}:
            prompt.response_status = 'dismissed'
            prompt.responded_at = now

    for prompt_type, payload in desired_by_type.items():
        prompt = existing_by_type.get(prompt_type)
        if not prompt:
            prompt = HydrationPrompt(
                user_id=user.id,
                prompt_type=prompt_type,
                due_at=payload['due_at'],
                message=payload['message'],
                response_status='pending',
            )
            db.session.add(prompt)
            db.session.flush()
            continue

        if prompt.response_status in {'finished', 'dismissed', 'missed'}:
            prompt.message = payload['message']
            continue

        chosen_due_at = payload['due_at']
        if prompt.response_status == 'not_yet' and prompt.due_at and prompt.due_at > now:
            chosen_due_at = prompt.due_at
        prompt.due_at = chosen_due_at
        prompt.message = _hydrate_prompt_message_from_slot(payload['label'], chosen_due_at, user=user, extra_context=plan)
        if prompt.response_status not in {'pending', 'not_yet'}:
            prompt.response_status = 'pending'

    stale_prompts = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user.id,
        _hydration_active_prompt_filter(),
        HydrationPrompt.response_status.in_(['pending', 'not_yet']),
        db.func.date(HydrationPrompt.due_at) == chosen_date,
        HydrationPrompt.due_at < grace_cutoff,
    ).all()
    for prompt in stale_prompts:
        prompt.response_status = 'missed'
        prompt.responded_at = now
        _log_activity_entry(
            user.id,
            'hydration',
            'Hydration reminder missed',
            f"Missed { _hydration_prompt_label(prompt.prompt_type) } at {prompt.due_at.strftime('%I:%M %p').lstrip('0') if prompt.due_at else 'an earlier time'}.",
        )



def _missed_hydration_summary(user_id: int, target_date: date | None = None):
    chosen_date = target_date or local_today()
    missed_prompts = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user_id,
        _hydration_active_prompt_filter(),
        HydrationPrompt.response_status == 'missed',
        db.func.date(HydrationPrompt.due_at) == chosen_date,
    ).order_by(HydrationPrompt.due_at.desc(), HydrationPrompt.id.desc()).all()
    if not missed_prompts:
        return None

    latest = missed_prompts[0]
    latest_label = _hydration_prompt_label(latest.prompt_type)
    latest_time = latest.due_at.strftime('%I:%M %p').lstrip('0') if latest.due_at else 'earlier today'
    count = len(missed_prompts)
    return {
        'count': count,
        'latest_prompt_id': latest.id,
        'latest_slot_label': latest_label,
        'latest_due_at_iso': latest.due_at.isoformat() if latest.due_at else None,
        'message': (
            f"You missed {count} water reminder{'s' if count != 1 else ''} today. "
            f"Latest missed: {latest_label} at {latest_time}."
        ),
    }



def _get_due_and_upcoming_prompt(user_id: int):
    user = db.session.get(User, user_id)
    if not user or not _profile_locked(user):
        return None, None
    _sync_goal_based_hydration_prompts(user, local_today())
    now = local_now().replace(tzinfo=None)
    grace_cutoff = now - timedelta(minutes=HYDRATION_DUE_GRACE_MINUTES)
    due_prompt = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user_id,
        _hydration_active_prompt_filter(),
        HydrationPrompt.response_status.in_(['pending', 'not_yet']),
        db.func.date(HydrationPrompt.due_at) == local_today(),
        HydrationPrompt.due_at <= now,
        HydrationPrompt.due_at >= grace_cutoff,
    ).order_by(HydrationPrompt.due_at.asc(), HydrationPrompt.id.asc()).first()

    upcoming_prompt = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user_id,
        _hydration_active_prompt_filter(),
        HydrationPrompt.response_status.in_(['pending', 'not_yet']),
        db.func.date(HydrationPrompt.due_at) == local_today(),
        HydrationPrompt.due_at > now,
    ).order_by(HydrationPrompt.due_at.asc(), HydrationPrompt.id.asc()).first()

    return due_prompt, upcoming_prompt



def _serialize_prompt(prompt: HydrationPrompt | None):
    if not prompt:
        return None
    slot_label = _hydration_prompt_label(prompt.prompt_type)
    return {
        'id': prompt.id,
        'prompt_type': prompt.prompt_type,
        'slot_label': slot_label,
        'message': prompt.message,
        'due_at_iso': prompt.due_at.isoformat() if prompt.due_at else None,
        'response_status': prompt.response_status,
        'beverage': prompt.beverage,
        'was_missed': prompt.response_status == 'missed',
    }


def _get_or_create_eye_exercise_state(user_id: int) -> EyeExerciseState:
    state = EyeExerciseState.query.filter_by(user_id=user_id).first()
    if state:
        if state.updated_at is None:
            state.updated_at = local_now().replace(tzinfo=None)
        return state
    now = local_now().replace(tzinfo=None)
    state = EyeExerciseState(
        user_id=user_id,
        carry_focus_minutes=0,
        active_prompt_id=None,
        created_at=now,
        updated_at=now,
    )
    db.session.add(state)
    db.session.flush()
    return state



def _get_active_eye_exercise_prompt(user_id: int) -> EyeExercisePrompt | None:
    state = _get_or_create_eye_exercise_state(user_id)
    prompt = None
    if state.active_prompt_id:
        prompt = EyeExercisePrompt.query.filter_by(id=state.active_prompt_id, user_id=user_id).first()
        if prompt and prompt.response_status in {'pending', 'watching', 'not_yet'}:
            return prompt
        state.active_prompt_id = None
        state.updated_at = local_now().replace(tzinfo=None)
    return None



def _serialize_eye_exercise_prompt(prompt: EyeExercisePrompt | None):
    if not prompt:
        return None
    focus_trigger = int(prompt.focus_minutes_trigger or EYE_EXERCISE_THRESHOLD_MINUTES)
    if int(prompt.focus_minutes_trigger or 0) <= 0:
        message = 'You asked to do an eye exercise now. Do you want to start it?'
    else:
        message = f"You've focused for {focus_trigger} minutes. Do you want to do an eye exercise now?"
    return {
        'id': prompt.id,
        'message': message,
        'focus_minutes_trigger': focus_trigger,
        'threshold_minutes': int(prompt.threshold_minutes or EYE_EXERCISE_THRESHOLD_MINUTES),
        'response_status': prompt.response_status,
        'video_url': prompt.video_url or EYE_EXERCISE_VIDEO_URL,
        'embed_url': 'https://www.youtube.com/embed/iVb4vUp70zY',
        'due_at_iso': prompt.due_at.isoformat() if prompt.due_at else None,
        'source_text': 'Source: YouTube · lenstark.com',
    }



def _ensure_eye_exercise_task(user_id: int, task_date: date, focus_minutes: int | None = None) -> Task:
    existing_task = Task.query.filter_by(user_id=user_id, task_date=task_date, task_type='eye_exercise', completed=False).order_by(Task.id.desc()).first()
    if existing_task:
        return existing_task
    focus_text = f' after {focus_minutes} min focus' if focus_minutes else ''
    task = Task(
        user_id=user_id,
        title='Do eye exercise',
        description=f'Recommended{focus_text}.',
        task_type='eye_exercise',
        task_date=task_date,
        completed=False,
        sort_order=_get_next_sort_order(user_id, task_date),
    )
    db.session.add(task)
    db.session.flush()
    return task



def _dismiss_eye_exercise_task(user_id: int, task_date: date | None = None) -> None:
    chosen_date = task_date or local_today()
    pending_tasks = Task.query.filter_by(user_id=user_id, task_date=chosen_date, task_type='eye_exercise', completed=False).all()
    for task in pending_tasks:
        db.session.delete(task)



def _queue_eye_exercise_prompt(user_id: int, focus_minutes: int, completed_at: datetime | None = None) -> EyeExercisePrompt | None:
    if int(focus_minutes or 0) <= 0:
        return _get_active_eye_exercise_prompt(user_id)
    active_prompt = _get_active_eye_exercise_prompt(user_id)
    state = _get_or_create_eye_exercise_state(user_id)
    now = (completed_at or local_now()).replace(tzinfo=None)
    if active_prompt:
        state.updated_at = now
        return active_prompt
    state.carry_focus_minutes = int(state.carry_focus_minutes or 0) + int(focus_minutes or 0)
    state.updated_at = now
    if state.carry_focus_minutes < EYE_EXERCISE_THRESHOLD_MINUTES:
        return None
    prompt = EyeExercisePrompt(
        user_id=user_id,
        focus_minutes_trigger=int(state.carry_focus_minutes or EYE_EXERCISE_THRESHOLD_MINUTES),
        threshold_minutes=EYE_EXERCISE_THRESHOLD_MINUTES,
        video_url=EYE_EXERCISE_VIDEO_URL,
        response_status='pending',
        due_at=now,
        created_at=now,
    )
    db.session.add(prompt)
    db.session.flush()
    state.carry_focus_minutes = 0
    state.active_prompt_id = prompt.id
    state.updated_at = now
    return prompt



def _complete_eye_exercise(user: User, target_date: date | None = None, event_at: datetime | None = None, source_label: str = 'video') -> dict:
    chosen_date = target_date or local_today()
    completed_at = (event_at or local_now()).replace(tzinfo=None)
    state = _get_or_create_eye_exercise_state(user.id)
    prompt = _get_active_eye_exercise_prompt(user.id)
    if not prompt:
        prompt = EyeExercisePrompt.query.filter(
            EyeExercisePrompt.user_id == user.id,
            EyeExercisePrompt.response_status.in_(['pending', 'watching', 'not_yet']),
        ).order_by(EyeExercisePrompt.created_at.desc(), EyeExercisePrompt.id.desc()).first()
    focus_trigger = 0
    if prompt:
        focus_trigger = int(prompt.focus_minutes_trigger or 0)
        prompt.response_status = 'finished'
        prompt.responded_at = completed_at
    state.active_prompt_id = None
    state.updated_at = completed_at
    _dismiss_eye_exercise_task(user.id, chosen_date)
    _add_calendar_event(
        user.id,
        'Eye exercise finished',
        chosen_date,
        completed_at.time().replace(second=0, microsecond=0),
        f'Completed after {focus_trigger or EYE_EXERCISE_THRESHOLD_MINUTES} minutes of focus via {source_label}.',
    )
    latest_event = f'Completed eye exercise after {focus_trigger or EYE_EXERCISE_THRESHOLD_MINUTES} focus minutes via {source_label}'
    payload = _apply_wellness_update(user, chosen_date, latest_event)
    return {
        'payload': payload,
        'focus_trigger': focus_trigger or EYE_EXERCISE_THRESHOLD_MINUTES,
        'event_label': f'Eye exercise finished · {focus_trigger or EYE_EXERCISE_THRESHOLD_MINUTES} min focus · {source_label}',
    }


def _sleep_reminder_payload(user: User) -> dict | None:
    today = local_today()
    wake_dt, bedtime_dt = _sleep_schedule_for_date(user, today)
    now = local_now().replace(tzinfo=None)
    bedtime_due = bedtime_dt.replace(tzinfo=None)
    log = DailyLog.query.filter_by(user_id=user.id, log_date=today).first()
    if now < bedtime_due:
        return None
    if log and float(log.sleep_hours or 0) >= float(user.daily_sleep_goal_hours or 8.0):
        return None
    return {
        'date_key': today.isoformat(),
        'bedtime_label': bedtime_due.strftime('%I:%M %p').lstrip('0'),
        'wake_time_label': wake_dt.strftime('%I:%M %p').lstrip('0'),
        'message': f"Best bedtime is {bedtime_due.strftime('%I:%M %p').lstrip('0')}. Your suggested wake-up time is {wake_dt.strftime('%I:%M %p').lstrip('0')}."
    }

def _build_profile_payload(user: User) -> dict:
    return {
        'age': user.age,
        'gender_identity': user.gender_identity,
        'weight_kg': user.weight_kg,
        'height_cm': user.height_cm,
        'daily_water_goal_ml': user.daily_water_goal_ml or 2000,
        'daily_sleep_goal_hours': user.daily_sleep_goal_hours or 8.0,
        'daily_step_goal': user.daily_step_goal or 8000,
        'daily_exercise_goal_minutes': user.daily_exercise_goal_minutes or 30,
        'goal_progress_intensity': (user.goal_progress_intensity or 'medium').strip().lower() or 'medium',
        'avatar_emoji': user.avatar_emoji or '🙂',
        'optimal_bedtime': user.optimal_bedtime or '22:00',
        'optimal_wake_time': user.optimal_wake_time or '07:00',
    }



def _build_log_payload(log: DailyLog | None) -> dict:
    return {
        'water_ml': int(log.water_ml or 0) if log else 0,
        'sleep_hours': float(log.sleep_hours or 0) if log else 0,
        'steps': int(log.steps or 0) if log else 0,
        'exercise_minutes': int(log.exercise_minutes or 0) if log else 0,
        'journal_text': log.journal_text if log else '',
        'mood_label': (log.mood_label if log else '') or '',
        'mood_custom_text': (log.mood_custom_text if log else '') or '',
        'activity_text': log.activity_text if log else '',
        'notes': '',
    }



def _build_focus_payload(user_id: int, target_date: date) -> dict:
    sessions = PomodoroSession.query.filter(
        PomodoroSession.user_id == user_id,
        db.func.date(PomodoroSession.completed_at) == target_date,
    ).order_by(PomodoroSession.completed_at.desc()).all()
    return {
        'focus_count': len(sessions),
        'focus_minutes': sum(session.focus_minutes for session in sessions),
        'sessions': sessions,
    }



def _build_todo_payload(user_id: int, target_date: date) -> dict:
    tasks = Task.query.filter_by(user_id=user_id, task_date=target_date).all()
    completed_count = sum(1 for task in tasks if task.completed)
    focus_completed_count = sum(1 for task in tasks if task.completed and _task_is_focus_eligible(task))
    return {
        'total_count': len(tasks),
        'completed_count': completed_count,
        'focus_completed_count': focus_completed_count,
    }



def _score_snapshot(user: User) -> dict[str, int]:
    return {
        'hydration': int(user.hydration_score or 50),
        'energy': int(user.energy_score or 50),
        'fitness': int(user.fitness_score or 50),
        'focus': int(user.focus_score or 50),
        'mood': int(user.mood_score or 50),
        'overall': int(user.overall_wellness_score or 50),
    }



def _serialize_wellness(user: User):
    score_map = _score_snapshot(user)
    return [
        {'key': key, 'label': label, 'subtitle': subtitle, 'value': score_map[key]}
        for key, label, subtitle in WELLNESS_META
    ]


def _wellness_label_map() -> dict[str, str]:
    return {key: label for key, label, _ in WELLNESS_META}


def _build_wellness_feedback(payload: dict, previous_scores: dict[str, int]) -> dict:
    labels = _wellness_label_map()
    after_scores = {
        'hydration': int(payload.get('hydration_score') or previous_scores['hydration']),
        'energy': int(payload.get('energy_score') or previous_scores['energy']),
        'fitness': int(payload.get('fitness_score') or previous_scores['fitness']),
        'focus': int(payload.get('focus_score') or previous_scores['focus']),
        'mood': int(payload.get('mood_score') or previous_scores['mood']),
        'overall': int(payload.get('overall_wellness_score') or previous_scores['overall']),
    }

    changed_metrics = []
    positive_total = 0
    negative_total = 0
    for key in ['hydration', 'energy', 'fitness', 'focus', 'mood', 'overall']:
        delta = after_scores[key] - int(previous_scores.get(key) or 0)
        if delta > 0:
            positive_total += delta
        elif delta < 0:
            negative_total += delta
        if delta != 0:
            changed_metrics.append(
                {
                    'key': key,
                    'label': labels[key],
                    'delta': delta,
                    'signed': f"{delta:+d}",
                    'tone_class': 'plus' if delta > 0 else 'minus',
                }
            )

    changed_metrics.sort(key=lambda item: (item['key'] != 'overall', -abs(item['delta'])))

    if positive_total > abs(negative_total):
        tone = 'positive'
        title = 'Nice progress today'
    elif negative_total < 0:
        tone = 'negative'
        title = 'A few scores slipped'
    else:
        tone = 'steady'
        title = 'Scores updated'

    if not changed_metrics:
        changed_metrics = [
            {
                'key': 'overall',
                'label': labels['overall'],
                'delta': 0,
                'signed': '+0',
                'tone_class': 'zero',
            }
        ]

    message = str(payload.get('summary') or 'Your wellness scores were refreshed.')
    return {
        'tone': tone,
        'title': title,
        'message': message,
        'metrics': changed_metrics[:6],
        'avatar_emoji': payload.get('avatar_emoji') or '🙂',
    }


def _store_wellness_feedback(feedback: dict | None) -> None:
    if has_request_context() and current_user.is_authenticated:
        _store_client_state(current_user.id, 'pending_wellness_feedback', feedback)



def _consume_wellness_feedback() -> dict | None:
    return _consume_client_state('pending_wellness_feedback')



def _history_entry_impacts(entry: ActivityEntry):
    visible_description, stored_impacts = _split_activity_description(entry.description)
    if stored_impacts:
        normalized = []
        for item in stored_impacts:
            value = int(item.get('value', 0) or 0)
            key = item.get('key')
            normalized.append({
                'key': key,
                'label': item.get('label') or EVENT_IMPACT_LABELS.get(key, 'Overall Wellness'),
                'value': value,
                'signed': item.get('signed') or f"{value:+d}",
            })
        if normalized:
            if all(int(item.get('value', 0) or 0) == 0 for item in normalized):
                return history_payload_from_impacts({'overall': 0}, collapse_zero=True)
            return normalized

    return history_payload_from_impacts(infer_event_impacts(entry.title, visible_description), collapse_zero=True)



def _add_calendar_event(user_id: int, title: str, event_date: date, event_time=None, description: str | None = None):
    event = CalendarEvent(
        user_id=user_id,
        title=title[:200],
        description=(description or '').strip() or None,
        event_date=event_date,
        event_time=event_time,
        created_at=local_now().replace(tzinfo=None),
    )
    db.session.add(event)
    db.session.flush()
    return event



def _impact_marker_text(impacts) -> str:
    payload = []
    for item in impacts or []:
        payload.append({
            'key': str(item.get('key') or ''),
            'label': str(item.get('label') or ''),
            'value': int(item.get('delta', item.get('value', 0)) or 0),
            'signed': str(item.get('signed') or f"{int(item.get('delta', item.get('value', 0)) or 0):+d}"),
        })
    return f"[[IMPACTS:{json.dumps(payload, separators=(',', ':'))}]]"


def _split_activity_description(raw_description: str | None) -> tuple[str | None, list | None]:
    cleaned = (raw_description or '').strip()
    if not cleaned:
        return None, None
    match = re.search(r'\[\[IMPACTS:(.*?)\]\]$', cleaned, re.DOTALL)
    if not match:
        return cleaned, None
    visible = cleaned[:match.start()].rstrip() or None
    try:
        impacts = json.loads(match.group(1))
        if not isinstance(impacts, list):
            impacts = None
    except Exception:
        logger.warning('Could not decode activity impact marker', exc_info=True)
        impacts = None
    return visible, impacts


def _log_activity_entry(user_id: int, entry_type: str, title: str, description: str | None = None, event_at: datetime | None = None, impacts=None):
    clean_description = (description or '').strip()
    logged_at = (event_at or local_now()).replace(tzinfo=None)
    if impacts:
        marker = _impact_marker_text(impacts)
        clean_description = f"{clean_description}\n{marker}" if clean_description else marker
    row = ActivityEntry(
        user_id=user_id,
        entry_type=entry_type,
        title=title[:200],
        description=clean_description or None,
        event_at=logged_at,
    )
    db.session.add(row)
    db.session.flush()
    _prune_activity_entries_if_needed(user_id, now=logged_at)
    return row


def _apply_wellness_update(user: User, target_date: date | None = None, latest_event: str = 'Manual update'):
    _ensure_baseline_scores(user)
    previous_scores = _score_snapshot(user)
    chosen_date = target_date or local_today()
    log = DailyLog.query.filter_by(user_id=user.id, log_date=chosen_date).first()
    focus = _build_focus_payload(user.id, chosen_date)
    todo = _build_todo_payload(user.id, chosen_date)
    payload = update_wellness_scores(
        profile=_build_profile_payload(user),
        daily_log=_build_log_payload(log),
        focus={'focus_count': focus['focus_count'], 'focus_minutes': focus['focus_minutes']},
        todo=todo,
        latest_event=latest_event,
        current_scores={
            'hydration_score': user.hydration_score,
            'energy_score': user.energy_score,
            'fitness_score': user.fitness_score,
            'focus_score': user.focus_score,
            'mood_score': user.mood_score,
            'overall_wellness_score': user.overall_wellness_score,
        },
    )
    user.hydration_score = int(payload.get('hydration_score') or 50)
    user.energy_score = int(payload.get('energy_score') or 50)
    user.fitness_score = int(payload.get('fitness_score') or 50)
    user.focus_score = int(payload.get('focus_score') or 50)
    user.mood_score = int(payload.get('mood_score') or 50)
    user.overall_wellness_score = int(payload.get('overall_wellness_score') or 50)
    after_scores = _score_snapshot(user)
    avatar_emoji = _choose_avatar_emoji_from_scores(user, after_scores)
    user.avatar_emoji = avatar_emoji
    payload['avatar_emoji'] = avatar_emoji
    user.wellness_summary = str(payload.get('summary') or '')
    user.wellness_updated_at = local_now().replace(tzinfo=None)
    payload['feedback'] = _build_wellness_feedback(payload, previous_scores)
    _store_wellness_feedback(payload['feedback'])
    return payload



def _update_log_meal_insight(user_id: int, log: DailyLog, candidate_text: str) -> bool:
    analysis = analyze_meal_text(candidate_text)
    log.ai_meal_detected = False
    log.ai_meal_confidence = None
    log.ai_feedback = None

    if analysis.get('ate_meal'):
        meal_time = local_now().replace(tzinfo=None)
        log.ai_meal_detected = True
        log.ai_meal_confidence = analysis.get('confidence')
        log.ai_feedback = analysis.get('reason')
        log.last_meal_detected_at = meal_time
        db.session.flush()
        return True

    if candidate_text:
        log.ai_feedback = analysis.get('reason')
        log.ai_meal_confidence = analysis.get('confidence')
    return False



def _format_glasses(ml_value: int | float | None) -> str:
    glasses = max(float(ml_value or 0) / GLASS_VOLUME_ML, 0)
    if abs(glasses - round(glasses)) < 1e-9:
        glasses_text = str(int(round(glasses)))
    else:
        glasses_text = f"{glasses:.1f}".rstrip('0').rstrip('.')
    return f"{glasses_text} glass{'es' if glasses_text != '1' else ''}"



def _build_glass_progress(current_ml: int | float | None, goal_ml: int | float | None) -> dict:
    current_ml = max(int(current_ml or 0), 0)
    goal_ml = max(int(goal_ml or 0), GLASS_VOLUME_ML)
    total_glasses = max(1, math.ceil(goal_ml / GLASS_VOLUME_ML))
    current_glasses = max(current_ml / GLASS_VOLUME_ML, 0)
    capped_glasses = min(current_glasses, float(total_glasses))
    whole_glasses = int(capped_glasses)
    partial_percent = int(round((capped_glasses - whole_glasses) * 100))

    segments = []
    for index in range(total_glasses):
        if index < whole_glasses:
            fill_percent = 100
            state = 'full'
        elif index == whole_glasses and partial_percent > 0:
            fill_percent = partial_percent
            state = 'partial'
        else:
            fill_percent = 0
            state = 'empty'
        segments.append({
            'index': index + 1,
            'fill_percent': fill_percent,
            'state': state,
        })

    remaining_ml = max(goal_ml - current_ml, 0)
    return {
        'current_glasses': capped_glasses,
        'current_display': _format_glasses(current_ml),
        'total_display': _format_glasses(goal_ml),
        'goal_ml': goal_ml,
        'current_ml': current_ml,
        'percent': max(0, min(100, int(round((current_ml / goal_ml) * 100)))) if goal_ml else 0,
        'remaining_text': 'Goal reached' if remaining_ml <= 0 else f"{remaining_ml} ml left today",
        'segments': segments,
    }



def _activity_entry_view_model(entry: ActivityEntry, compact: bool = False) -> dict:
    description, _ = _split_activity_description(entry.description)
    impacts = _history_entry_impacts(entry)
    if compact:
        non_zero = [item for item in impacts if int(item.get('value', 0) or 0) != 0]
        impacts = (non_zero[:3] if non_zero else impacts[:1])
    return {
        'row': entry,
        'title': entry.title,
        'entry_type': (entry.entry_type or '').replace('_', ' '),
        'description': description,
        'impacts': impacts,
        'event_at': entry.event_at,
    }



def _build_goal_cards(user: User):
    water_goal_ml = int(user.daily_water_goal_ml or 0)
    return [
        {
            'label': 'Daily Water Goal',
            'value': f"{water_goal_ml} ml",
            'subtitle': f"≈ {_format_glasses(water_goal_ml)}",
        },
        {
            'label': 'Sleep Goal',
            'value': f"{float(user.daily_sleep_goal_hours or 0):g} h",
            'subtitle': 'Recommended nightly sleep target',
        },
        {
            'label': 'Step Goal',
            'value': f"{int(user.daily_step_goal or 0)} steps",
            'subtitle': 'Daily movement target',
        },
        {
            'label': 'Exercise Goal',
            'value': f"{int(user.daily_exercise_goal_minutes or 30)} min",
            'subtitle': 'Intentional exercise goal',
        },
    ]



def _progress_snapshot(user: User, log: DailyLog | None):
    return {
        'water_ml': int(log.water_ml or 0) if log else 0,
        'sleep_hours': float(log.sleep_hours or 0) if log else 0,
        'steps': int(log.steps or 0) if log else 0,
        'exercise_minutes': int(log.exercise_minutes or 0) if log else 0,
        'journal_text': (log.journal_text or '').strip() if log else '',
    }



def _build_progress_cards(user: User, log: DailyLog | None):
    snapshot = _progress_snapshot(user, log)

    def percent(value, goal):
        safe_goal = max(float(goal or 0), 1.0)
        return max(0, min(100, int(round((float(value or 0) / safe_goal) * 100))))

    water_goal_ml = int(user.daily_water_goal_ml or 0)
    return [
        {
            'key': 'water',
            'label': 'Water',
            'value': f"{snapshot['water_ml']} / {water_goal_ml} ml",
            'percent': percent(snapshot['water_ml'], water_goal_ml),
            'glass_progress': _build_glass_progress(snapshot['water_ml'], water_goal_ml),
            'toggle_hint': 'Tap to show glass progress',
        },
        {
            'key': 'sleep',
            'label': 'Sleep',
            'value': f"{snapshot['sleep_hours']:g} / {float(user.daily_sleep_goal_hours or 0):g} h",
            'percent': percent(snapshot['sleep_hours'], user.daily_sleep_goal_hours),
        },
        {
            'key': 'steps',
            'label': 'Steps',
            'value': f"{snapshot['steps']} / {int(user.daily_step_goal or 0)}",
            'percent': percent(snapshot['steps'], user.daily_step_goal),
        },
        {
            'key': 'exercise',
            'label': 'Exercise',
            'value': f"{snapshot['exercise_minutes']} / {int(user.daily_exercise_goal_minutes or 30)} min",
            'percent': percent(snapshot['exercise_minutes'], user.daily_exercise_goal_minutes or 30),
        },
    ]



def _build_quick_stats(user_id: int, target_date: date):
    focus = _build_focus_payload(user_id, target_date)
    tasks = Task.query.filter_by(user_id=user_id, task_date=target_date).all()
    recent_activity_count = ActivityEntry.query.filter_by(user_id=user_id).count()
    return [
        {'label': "Today's Focus Sessions", 'value': focus['focus_count']},
        {'label': "Today's Focus Minutes", 'value': focus['focus_minutes']},
        {'label': "Today's Todo Count", 'value': len(tasks)},
        {'label': 'Recent Activity', 'value': recent_activity_count},
    ]



def _current_goal_streak(user: User, goal_key: str) -> int:
    today = local_today()
    logs = DailyLog.query.filter(
        DailyLog.user_id == user.id,
        DailyLog.log_date <= today,
    ).order_by(DailyLog.log_date.desc()).all()
    log_map = {row.log_date: row for row in logs}

    def meets_goal(log: DailyLog | None) -> bool:
        if not log:
            return False
        if goal_key == 'water':
            return int(log.water_ml or 0) >= int(user.daily_water_goal_ml or 0)
        if goal_key == 'sleep':
            return float(log.sleep_hours or 0) >= float(user.daily_sleep_goal_hours or 0)
        if goal_key == 'exercise':
            return int(log.exercise_minutes or 0) >= int(user.daily_exercise_goal_minutes or 30)
        return False

    streak = 0
    current_day = today
    while True:
        if not meets_goal(log_map.get(current_day)):
            break
        streak += 1
        current_day -= timedelta(days=1)
    return streak



def _build_streak_cards(user: User):
    return [
        {
            'label': 'Water Goal Streak',
            'value': _current_goal_streak(user, 'water'),
            'subtitle': f"Reached {int(user.daily_water_goal_ml or 0)} ml",
        },
        {
            'label': 'Sleep Goal Streak',
            'value': _current_goal_streak(user, 'sleep'),
            'subtitle': f"Reached {float(user.daily_sleep_goal_hours or 0):g} hours",
        },
        {
            'label': 'Exercise Goal Streak',
            'value': _current_goal_streak(user, 'exercise'),
            'subtitle': f"Reached {int(user.daily_exercise_goal_minutes or 30)} minutes",
        },
    ]



def _recent_activity_preview(user_id: int, limit: int = 8):
    rows = ActivityEntry.query.filter_by(user_id=user_id).order_by(ActivityEntry.event_at.desc()).limit(limit).all()
    return [_activity_entry_view_model(row, compact=True) for row in rows]
