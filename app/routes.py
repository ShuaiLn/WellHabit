from __future__ import annotations

from calendar import Calendar
import json
import math
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import flash, has_request_context, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from . import db
from .ai_services import (
    analyze_meal_text,
    convert_drink_amount_to_ml,
    suggest_personal_goals,
    update_wellness_scores,
)
from .models import ActivityEntry, CalendarEvent, DailyLog, HydrationPrompt, PomodoroSession, Task, User

LOCAL_TZ = ZoneInfo('America/Los_Angeles')
GLASS_VOLUME_ML = 250

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



def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)



def local_today() -> date:
    return local_now().date()



def _parse_date(value: str, fallback: date | None = None) -> date:
    if not value:
        return fallback or local_today()
    return datetime.strptime(value, '%Y-%m-%d').date()



def _parse_time(value: str | None):
    clean = (value or '').strip()
    if not clean:
        return None
    return datetime.strptime(clean, '%H:%M').time()



def _parse_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default



def _parse_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default



def _normalize_task_text(value: str | None) -> str:
    return re.sub(r'\s+', ' ', (value or '').strip().lower())



def _task_type(task: Task) -> str:
    stored = (getattr(task, 'task_type', None) or '').strip().lower()
    if stored:
        return stored
    normalized = _normalize_task_text(task.title)
    if normalized in {item[1] for item in MEAL_TASKS}:
        return 'meal'
    return 'regular'



def _task_is_meal(task: Task) -> bool:
    return _task_type(task) == 'meal'



def _task_is_hydration(task: Task) -> bool:
    normalized = _normalize_task_text(task.title)
    return any(word in normalized for word in ['drink', 'water', 'hydration', 'glass of water'])



def _task_is_focus_eligible(task: Task) -> bool:
    return not _task_is_meal(task) and not _task_is_hydration(task)



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



def _roll_over_pending_tasks(user_id: int) -> None:
    today = local_today()
    overdue_tasks = Task.query.filter(
        Task.user_id == user_id,
        Task.task_date < today,
        Task.completed.is_(False),
        db.or_(Task.task_type.is_(None), Task.task_type != 'meal'),
    ).order_by(Task.task_date.asc(), Task.sort_order.asc(), Task.created_at.asc()).all()

    if not overdue_tasks:
        return

    next_order = _get_next_sort_order(user_id, today)
    for task in overdue_tasks:
        task.task_date = today
        task.sort_order = next_order
        next_order += 1

    db.session.commit()



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
    log = DailyLog.query.filter_by(user_id=user_id, log_date=log_date).first()
    if not log:
        log = DailyLog(user_id=user_id, log_date=log_date)
        db.session.add(log)
        db.session.flush()
    return log



def _get_or_create_log_for_today(user_id: int) -> DailyLog:
    return _get_or_create_log_for_date(user_id, local_today())



def _normalize_beverage(beverage: str, custom_beverage: str = '') -> str:
    beverage_value = (beverage or 'water').strip().lower()
    custom_value = (custom_beverage or '').strip()
    if beverage_value == 'other':
        return custom_value or 'water'
    return beverage_value or 'water'



def _ensure_daily_morning_prompt(user_id: int) -> HydrationPrompt | None:
    user = db.session.get(User, user_id)
    if not user:
        return None
    now = local_now().replace(tzinfo=None)
    today = now.date()
    wake_dt, _ = _sleep_schedule_for_date(user, today)
    wake_due = wake_dt.replace(tzinfo=None)
    if now < wake_due or now.hour >= 12:
        return None

    existing = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user_id,
        HydrationPrompt.prompt_type == 'morning',
        db.func.date(HydrationPrompt.due_at) == today,
    ).order_by(HydrationPrompt.id.desc()).first()
    if existing:
        return existing if existing.response_status in {'pending', 'not_yet'} else None

    prompt = HydrationPrompt(
        user_id=user_id,
        prompt_type='morning',
        due_at=now,
        message='Good morning. Start your day with one glass of water.',
        response_status='pending',
    )
    db.session.add(prompt)
    db.session.flush()
    return prompt



def _suppress_today_hydration_prompts(user_id: int, keep_prompt_id: int | None = None) -> None:
    today = local_today()
    active_prompts = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user_id,
        HydrationPrompt.prompt_type.in_(['morning', 'meal_followup', 'planned_hydration']),
        HydrationPrompt.response_status.in_(['pending', 'not_yet']),
        db.func.date(HydrationPrompt.due_at) == today,
    ).all()
    dismissed_at = local_now().replace(tzinfo=None)
    for prompt in active_prompts:
        if keep_prompt_id and prompt.id == keep_prompt_id:
            continue
        prompt.response_status = 'dismissed'
        prompt.responded_at = dismissed_at


def _create_immediate_hydration_prompt(user_id: int, message: str) -> HydrationPrompt:
    _suppress_today_hydration_prompts(user_id)
    prompt = HydrationPrompt(
        user_id=user_id,
        prompt_type='meal_followup',
        due_at=local_now().replace(tzinfo=None),
        message=message,
        response_status='pending',
    )
    db.session.add(prompt)
    db.session.flush()
    return prompt



def _defer_active_hydration_prompts(user_id: int, until_dt: datetime, keep_prompt_id: int | None = None) -> None:
    active_prompts = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user_id,
        HydrationPrompt.prompt_type.in_(['morning', 'meal_followup', 'planned_hydration']),
        HydrationPrompt.response_status.in_(['pending', 'not_yet']),
        db.func.date(HydrationPrompt.due_at) == until_dt.date(),
    ).all()

    deferred_at = local_now().replace(tzinfo=None)
    for prompt in active_prompts:
        if keep_prompt_id and prompt.id == keep_prompt_id:
            continue
        prompt.response_status = 'not_yet'
        prompt.due_at = max(prompt.due_at or until_dt, until_dt)
        prompt.responded_at = deferred_at


def _parse_clock_text(value: str | None, fallback: str) -> datetime.time:
    text_value = (value or fallback or '').strip() or fallback
    try:
        return datetime.strptime(text_value, '%H:%M').time()
    except ValueError:
        return datetime.strptime(fallback, '%H:%M').time()


def _sleep_schedule_for_date(user: User, target_date: date) -> tuple[datetime, datetime]:
    wake_time = _parse_clock_text(user.optimal_wake_time, '07:00')
    bed_time = _parse_clock_text(user.optimal_bedtime, '22:00')
    wake_dt = datetime.combine(target_date, wake_time)
    bedtime_dt = datetime.combine(target_date, bed_time)
    if bedtime_dt <= wake_dt:
        bedtime_dt += timedelta(days=1)
    return wake_dt, bedtime_dt


def _hydration_schedule_times(user: User, target_date: date) -> list[datetime]:
    wake_dt, bedtime_dt = _sleep_schedule_for_date(user, target_date)
    total_glasses = max(1, math.ceil(max(int(user.daily_water_goal_ml or 2000), GLASS_VOLUME_ML) / GLASS_VOLUME_ML))
    total_window = max((bedtime_dt - wake_dt).total_seconds(), 4 * 3600)
    edge_buffer = min(total_window * 0.08, 45 * 60)
    start_dt = wake_dt + timedelta(seconds=edge_buffer)
    end_dt = bedtime_dt - timedelta(seconds=edge_buffer)
    if end_dt <= start_dt:
        start_dt = wake_dt
        end_dt = bedtime_dt
    if total_glasses == 1:
        return [start_dt + (end_dt - start_dt) / 2]
    span_seconds = max((end_dt - start_dt).total_seconds(), 1)
    step_seconds = span_seconds / (total_glasses - 1)
    return [start_dt + timedelta(seconds=step_seconds * index) for index in range(total_glasses)]


def _retire_legacy_hydration_prompts(user_id: int, target_date: date) -> None:
    legacy = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user_id,
        HydrationPrompt.prompt_type.in_(['meal_now', 'meal_plus_2h']),
        HydrationPrompt.response_status.in_(['pending', 'not_yet']),
        db.func.date(HydrationPrompt.due_at) == target_date,
    ).all()
    for prompt in legacy:
        prompt.response_status = 'dismissed'
        prompt.responded_at = local_now().replace(tzinfo=None)


def _hydration_prompt_message(user: User, due_at: datetime, consumed_ml: int, goal_ml: int) -> str:
    remaining_ml = max(goal_ml - consumed_ml, 0)
    remaining_glasses = max(1, math.ceil(remaining_ml / GLASS_VOLUME_ML)) if remaining_ml else 0
    time_label = due_at.strftime('%I:%M %p').lstrip('0')
    if remaining_glasses <= 1:
        return f"One more glass should help you finish today's water target. Try around {time_label}."
    return f"Your water goal is paced across the day. Try one glass around {time_label}. About {remaining_glasses} glasses are still left today."


def _sync_goal_based_hydration_prompts(user: User, target_date: date | None = None) -> None:
    chosen_date = target_date or local_today()
    if chosen_date != local_today():
        return

    _retire_legacy_hydration_prompts(user.id, chosen_date)
    log = DailyLog.query.filter_by(user_id=user.id, log_date=chosen_date).first()
    consumed_ml = int(log.water_ml or 0) if log else 0
    goal_ml = max(int(user.daily_water_goal_ml or 2000), GLASS_VOLUME_ML)
    remaining_slots = max(0, math.ceil(max(goal_ml - consumed_ml, 0) / GLASS_VOLUME_ML))

    planned_times = _hydration_schedule_times(user, chosen_date)
    completed_slots = max(0, len(planned_times) - remaining_slots)
    desired_times = planned_times[completed_slots:]

    existing = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user.id,
        HydrationPrompt.prompt_type == 'planned_hydration',
        HydrationPrompt.response_status.in_(['pending', 'not_yet']),
        db.func.date(HydrationPrompt.due_at) == chosen_date,
    ).order_by(HydrationPrompt.due_at.asc(), HydrationPrompt.id.asc()).all()

    while len(existing) > len(desired_times):
        prompt = existing.pop()
        prompt.response_status = 'dismissed'
        prompt.responded_at = local_now().replace(tzinfo=None)

    while len(existing) < len(desired_times):
        prompt = HydrationPrompt(
            user_id=user.id,
            prompt_type='planned_hydration',
            due_at=desired_times[len(existing)],
            message='Goal-based hydration reminder.',
            response_status='pending',
        )
        db.session.add(prompt)
        db.session.flush()
        existing.append(prompt)

    for prompt, due_at in zip(existing, desired_times):
        chosen_due_at = prompt.due_at if prompt.response_status == 'not_yet' and prompt.due_at and prompt.due_at > due_at else due_at
        prompt.prompt_type = 'planned_hydration'
        prompt.due_at = chosen_due_at
        prompt.message = _hydration_prompt_message(user, chosen_due_at, consumed_ml, goal_ml)
        if prompt.response_status not in {'pending', 'not_yet'}:
            prompt.response_status = 'pending'


def _get_due_and_upcoming_prompt(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        return None, None
    _sync_goal_based_hydration_prompts(user, local_today())
    _ensure_daily_morning_prompt(user_id)
    now = local_now().replace(tzinfo=None)
    active_types = ['morning', 'meal_followup', 'planned_hydration']
    due_prompt = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user_id,
        HydrationPrompt.prompt_type.in_(active_types),
        HydrationPrompt.response_status.in_(['pending', 'not_yet']),
        db.func.date(HydrationPrompt.due_at) == local_today(),
        HydrationPrompt.due_at <= now,
    ).order_by(HydrationPrompt.due_at.asc(), HydrationPrompt.id.asc()).first()

    upcoming_prompt = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user_id,
        HydrationPrompt.prompt_type.in_(active_types),
        HydrationPrompt.response_status.in_(['pending', 'not_yet']),
        db.func.date(HydrationPrompt.due_at) == local_today(),
        HydrationPrompt.due_at > now,
    ).order_by(HydrationPrompt.due_at.asc(), HydrationPrompt.id.asc()).first()

    return due_prompt, upcoming_prompt


def _serialize_prompt(prompt: HydrationPrompt | None):
    if not prompt:
        return None
    return {
        'id': prompt.id,
        'prompt_type': prompt.prompt_type,
        'message': prompt.message,
        'due_at_iso': prompt.due_at.isoformat() if prompt.due_at else None,
        'response_status': prompt.response_status,
        'beverage': prompt.beverage,
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
        'optimal_bedtime': user.optimal_bedtime or '22:00',
        'optimal_wake_time': user.optimal_wake_time or '07:00',
    }



def _build_log_payload(log: DailyLog | None) -> dict:
    return {
        'water_ml': int(log.water_ml or 0) if log else 0,
        'sleep_hours': float(log.sleep_hours or 0) if log else 0,
        'sleep_quality': 'Average',
        'steps': int(log.steps or 0) if log else 0,
        'exercise_minutes': int(log.exercise_minutes or 0) if log else 0,
        'journal_text': log.journal_text if log else '',
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
    }


def _store_wellness_feedback(feedback: dict | None) -> None:
    if has_request_context():
        if feedback:
            session['pending_wellness_feedback'] = feedback
        else:
            session.pop('pending_wellness_feedback', None)


def _consume_wellness_feedback() -> dict | None:
    if not has_request_context():
        return None
    return session.pop('pending_wellness_feedback', None)



def _history_entry_impacts(entry: ActivityEntry):
    labels = {
        'hydration': 'Hydration',
        'energy': 'Energy',
        'fitness': 'Fitness',
        'focus': 'Focus',
        'mood': 'Mood',
        'overall': 'Overall Wellness',
    }

    def _collapse_if_all_zero(items):
        if items and all(int(item.get('value', 0) or 0) == 0 for item in items):
            return [
                {
                    'key': 'overall',
                    'label': labels['overall'],
                    'value': 0,
                    'signed': '+0',
                }
            ]
        return items

    _, stored_impacts = _split_activity_description(entry.description)
    if stored_impacts:
        normalized = []
        for item in stored_impacts:
            value = int(item.get('value', 0) or 0)
            normalized.append({
                'key': item.get('key'),
                'label': item.get('label') or labels.get(item.get('key'), 'Overall Wellness'),
                'value': value,
                'signed': item.get('signed') or f"{value:+d}",
            })
        if normalized:
            return _collapse_if_all_zero(normalized)

    title_text = (entry.title or '').lower()
    desc_text = (_split_activity_description(entry.description)[0] or '').lower()
    text = ' '.join(part for part in [title_text, desc_text] if part)
    impacts = {
        'hydration': 0,
        'energy': 0,
        'fitness': 0,
        'focus': 0,
        'mood': 0,
    }

    if title_text in {'task added', 'task edited', 'task deleted'}:
        impacts['overall'] = 0
    else:
        is_meal = any(word in text for word in ['breakfast', 'lunch', 'dinner', 'meal'])
        is_hydration = any(word in text for word in ['drink', 'water', 'hydration', 'milk', 'coke', 'beverage'])
        is_task_completion = 'completed todo' in text or 'meal finished' in title_text

        if is_hydration:
            if any(word in text for word in ['skip', 'dismiss', 'not yet', 'postponed']):
                impacts['hydration'] -= 3
            else:
                amount_match = re.search(r'(\d+)\s*ml', text)
                amount = int(amount_match.group(1)) if amount_match else 250
                impacts['hydration'] += max(2, min(8, int(round(amount / 120))))

        if 'sleep' in text:
            impacts['energy'] += 4
        if any(word in text for word in ['steps', 'exercise', 'walk', 'run', 'stretch', 'yoga']):
            impacts['fitness'] += 4
        if is_meal and 'skipped' not in text:
            impacts['energy'] += 4
            impacts['fitness'] += 4
        elif is_task_completion and not is_hydration:
            impacts['focus'] += 5
        if 'journal' in text:
            impacts['mood'] += 3
        if any(word in text for word in ['tired', 'anxious', 'sad', 'stress', 'stressed', 'burned out']):
            impacts['mood'] -= 4

        impacts['overall'] = int(round((impacts['hydration'] + impacts['energy'] + impacts['fitness'] + impacts['focus'] + impacts['mood']) / 3))

    computed = [
        {
            'key': key,
            'label': labels[key],
            'value': impacts[key],
            'signed': f"{impacts[key]:+d}",
        }
        for key in ['hydration', 'energy', 'fitness', 'focus', 'mood', 'overall']
    ]
    return _collapse_if_all_zero(computed)



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
        impacts = None
    return visible, impacts


def _log_activity_entry(user_id: int, entry_type: str, title: str, description: str | None = None, event_at: datetime | None = None, impacts=None):
    clean_description = (description or '').strip()
    if impacts:
        marker = _impact_marker_text(impacts)
        clean_description = f"{clean_description}\n{marker}" if clean_description else marker
    row = ActivityEntry(
        user_id=user_id,
        entry_type=entry_type,
        title=title[:200],
        description=clean_description or None,
        event_at=(event_at or local_now()).replace(tzinfo=None),
    )
    db.session.add(row)
    db.session.flush()
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



def _build_goal_cards(user: User):
    return [
        {'label': 'Daily Water Goal (ml)', 'value': int(user.daily_water_goal_ml or 0)},
        {'label': 'Sleep Goal (hours)', 'value': f"{float(user.daily_sleep_goal_hours or 0):g}"},
        {'label': 'Step Goal', 'value': int(user.daily_step_goal or 0)},
        {'label': 'Exercise Goal (minutes)', 'value': int(user.daily_exercise_goal_minutes or 30)},
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

    return [
        {
            'label': 'Water',
            'value': f"{snapshot['water_ml']} / {int(user.daily_water_goal_ml or 0)} ml",
            'percent': percent(snapshot['water_ml'], user.daily_water_goal_ml),
        },
        {
            'label': 'Sleep',
            'value': f"{snapshot['sleep_hours']:g} / {float(user.daily_sleep_goal_hours or 0):g} h",
            'percent': percent(snapshot['sleep_hours'], user.daily_sleep_goal_hours),
        },
        {
            'label': 'Steps',
            'value': f"{snapshot['steps']} / {int(user.daily_step_goal or 0)}",
            'percent': percent(snapshot['steps'], user.daily_step_goal),
        },
        {
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
    return ActivityEntry.query.filter_by(user_id=user_id).order_by(ActivityEntry.event_at.desc()).limit(limit).all()



def register_routes(app):
    @app.before_request
    def sync_overdue_tasks():
        if current_user.is_authenticated:
            _roll_over_pending_tasks(current_user.id)

    @app.context_processor
    def inject_nav_context():
        return {
            'nav_local_date': local_now().strftime('%A, %B %d, %Y'),
            'nav_local_time': local_now().strftime('%I:%M:%S %p'),
            'pending_wellness_feedback': _consume_wellness_feedback(),
        }

    @app.route('/')
    def index():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        return render_template('index.html')

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            confirm_password = request.form.get('confirm_password', '')

            if not username or not email or not password:
                flash('Please fill in all required fields.', 'danger')
                return render_template('register.html')
            if password != confirm_password:
                flash('Passwords do not match.', 'danger')
                return render_template('register.html')
            if User.query.filter((User.username == username) | (User.email == email)).first():
                flash('Username or email already exists.', 'danger')
                return render_template('register.html')

            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash('Account created successfully. Please log in.', 'success')
            return redirect(url_for('login'))

        return render_template('register.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            user = User.query.filter_by(email=email).first()

            if user and user.check_password(password):
                login_user(user)
                if not user.wellness_updated_at:
                    _ensure_baseline_scores(user)
                    db.session.commit()
                flash('Logged in successfully.', 'success')
                return redirect(url_for('dashboard'))

            flash('Invalid email or password.', 'danger')

        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        flash('You have been logged out.', 'info')
        return redirect(url_for('login'))

    @app.route('/dashboard')
    @login_required
    def dashboard():
        _ensure_baseline_scores(current_user)

        today = local_today()
        _ensure_daily_default_tasks(current_user.id, today)
        db.session.commit()

        today_tasks = Task.query.filter_by(user_id=current_user.id, task_date=today).order_by(Task.sort_order.asc(), Task.created_at.asc()).all()
        recent_logs = DailyLog.query.filter_by(user_id=current_user.id).order_by(DailyLog.log_date.desc()).limit(5).all()
        today_focus = _build_focus_payload(current_user.id, today)
        today_log = DailyLog.query.filter_by(user_id=current_user.id, log_date=today).first()

        due_prompt, upcoming_prompt = _get_due_and_upcoming_prompt(current_user.id)
        morning_prompt_record = HydrationPrompt.query.filter(
            HydrationPrompt.user_id == current_user.id,
            HydrationPrompt.prompt_type == 'morning',
            db.func.date(HydrationPrompt.due_at) == today,
        ).first()

        return render_template(
            'dashboard.html',
            today_tasks=today_tasks,
            recent_logs=recent_logs,
            today_focus_count=today_focus['focus_count'],
            today_focus_minutes=today_focus['focus_minutes'],
            today=today,
            today_log=today_log,
            due_hydration_prompt=_serialize_prompt(due_prompt),
            upcoming_hydration_prompt=_serialize_prompt(upcoming_prompt),
            morning_prompt_exists=bool(morning_prompt_record),
            recent_activity_entries=_recent_activity_preview(current_user.id, 6),
        )

    @app.route('/profile', methods=['GET', 'POST'])
    @login_required
    def profile():
        _ensure_baseline_scores(current_user)
        locked = _profile_locked(current_user)

        if request.method == 'POST':
            if locked:
                flash('Basic information is already locked after the first save.', 'warning')
                return redirect(url_for('profile'))

            age = _parse_int(request.form.get('age'), default=None)
            gender_identity = (request.form.get('gender_identity') or 'prefer_not_say').strip().lower()
            valid_gender_values = {'male', 'female', 'non_binary', 'prefer_not_say'}
            if gender_identity not in valid_gender_values:
                gender_identity = 'prefer_not_say'
            weight_kg = _parse_float(request.form.get('weight_kg'), default=None)
            height_cm = _parse_float(request.form.get('height_cm'), default=None)

            if age is None or weight_kg is None or height_cm is None:
                flash('Please fill age, weight, and height once.', 'danger')
                return redirect(url_for('profile'))

            current_user.age = age
            current_user.gender_identity = gender_identity
            current_user.weight_kg = weight_kg
            current_user.height_cm = height_cm

            suggested = suggest_personal_goals(age, weight_kg, height_cm, gender_identity=gender_identity)
            current_user.daily_water_goal_ml = int(suggested['daily_water_goal_ml'])
            current_user.daily_sleep_goal_hours = float(suggested['daily_sleep_goal_hours'])
            current_user.daily_step_goal = int(suggested['daily_step_goal'])
            current_user.optimal_bedtime = str(suggested.get('optimal_bedtime') or '22:00')[:5]
            current_user.optimal_wake_time = str(suggested.get('optimal_wake_time') or '07:00')[:5]
            _sync_goal_based_hydration_prompts(current_user, local_today())
            db.session.commit()
            flash('Basic information saved. Daily goals and your recommended sleep schedule were generated automatically.', 'success')
            return redirect(url_for('profile'))

        latest_log = DailyLog.query.filter_by(user_id=current_user.id).order_by(DailyLog.log_date.desc()).first()
        today_log = DailyLog.query.filter_by(user_id=current_user.id, log_date=local_today()).first()
        return render_template(
            'profile.html',
            wellness_metrics=_serialize_wellness(current_user),
            latest_log=latest_log,
            goal_cards=_build_goal_cards(current_user),
            streak_cards=_build_streak_cards(current_user),
            profile_locked=locked,
            today_progress=_progress_snapshot(current_user, today_log),
            progress_cards=_build_progress_cards(current_user, today_log),
            quick_stats_cards=_build_quick_stats(current_user.id, local_today()),
        )

    @app.route('/history')
    @login_required
    def history_view():
        raw_entries = ActivityEntry.query.filter_by(user_id=current_user.id).order_by(ActivityEntry.event_at.desc()).limit(150).all()
        entries = [
            {
                'row': entry,
                'description': _split_activity_description(entry.description)[0],
                'impacts': _history_entry_impacts(entry),
            }
            for entry in raw_entries
        ]
        return render_template('history.html', entries=entries)

    @app.route('/activity/update', methods=['POST'])
    @login_required
    def update_activity():
        activity_text = request.form.get('activity_text', '').strip()
        if not activity_text:
            flash('Please write what you just did first.', 'danger')
            return redirect(url_for('dashboard'))

        chosen_time = _parse_time(request.form.get('activity_time')) or local_now().time().replace(second=0, microsecond=0)
        chosen_dt = datetime.combine(local_today(), chosen_time)
        log = _get_or_create_log_for_today(current_user.id)
        timestamp = chosen_time.strftime('%H:%M')
        entry = f'[{timestamp}] {activity_text}'
        existing = (log.activity_text or '').strip()
        log.activity_text = entry if not existing else f'{entry}\n{existing}'

        _add_calendar_event(current_user.id, f'What just did: {activity_text[:80]}', log.log_date, chosen_time, activity_text)
        auto_task = Task(
            user_id=current_user.id,
            title=activity_text[:200],
            description=None,
            task_date=log.log_date,
            completed=True,
            completed_at=chosen_dt,
            sort_order=_get_next_sort_order(current_user.id, log.log_date),
        )
        db.session.add(auto_task)
        _log_activity_entry(current_user.id, 'activity', 'What just did updated', activity_text, event_at=chosen_dt)

        meal_detected = _update_log_meal_insight(current_user.id, log, activity_text)
        if meal_detected:
            _sync_meal_task_completion(current_user.id, log.log_date, activity_text, chosen_dt)
        _apply_wellness_update(current_user, log.log_date, f'Recent activity updated: {activity_text}')
        db.session.commit()

        flash("Recent activity saved and marked as completed in today's todo list.", 'success')
        return redirect(url_for('dashboard'))

    @app.route('/logs', methods=['GET', 'POST'])
    @login_required
    def logs():
        selected_date = _parse_date(request.values.get('log_date'), fallback=_parse_date(request.args.get('date'), fallback=local_today()))
        if request.method == 'POST':
            log = _get_or_create_log_for_date(current_user.id, selected_date)
            changes = []

            water_amount = (request.form.get('water_ml') or '').strip()
            if water_amount:
                water_conversion = convert_drink_amount_to_ml('water', water_amount)
                added_water = int(water_conversion['amount_ml'])
                log.water_ml = int(log.water_ml or 0) + added_water
                changes.append(f'water +{added_water} ml')

            sleep_mode = (request.form.get('sleep_input_mode') or 'hours').strip().lower()
            sleep_raw = (request.form.get('sleep_hours') or '').strip()
            sleep_start_raw = (request.form.get('sleep_start_time') or '').strip()
            sleep_end_raw = (request.form.get('sleep_end_time') or '').strip()
            if sleep_mode == 'range':
                if sleep_start_raw or sleep_end_raw:
                    if not sleep_start_raw or not sleep_end_raw:
                        flash('Please fill both sleep start and sleep end time.', 'warning')
                        return redirect(url_for('logs', date=selected_date.isoformat()))
                    sleep_start = _parse_time(sleep_start_raw)
                    sleep_end = _parse_time(sleep_end_raw)
                    sleep_start_dt = datetime.combine(selected_date, sleep_start)
                    sleep_end_dt = datetime.combine(selected_date, sleep_end)
                    if sleep_end_dt <= sleep_start_dt:
                        sleep_end_dt += timedelta(days=1)
                    sleep_hours_value = round((sleep_end_dt - sleep_start_dt).total_seconds() / 3600.0, 2)
                    log.sleep_hours = sleep_hours_value
                    changes.append(f'sleep {sleep_hours_value:g} h ({sleep_start_raw}-{sleep_end_raw})')
            elif sleep_raw:
                log.sleep_hours = float(sleep_raw)
                changes.append(f'sleep {log.sleep_hours:g} h')

            steps_raw = (request.form.get('steps') or '').strip()
            if steps_raw:
                log.steps = int(float(steps_raw))
                changes.append(f'steps {log.steps}')

            exercise_name = (request.form.get('exercise_name') or '').strip()
            exercise_raw = (request.form.get('exercise_minutes') or '').strip()
            exercise_reps_raw = (request.form.get('exercise_reps') or '').strip()
            if exercise_name or exercise_raw or exercise_reps_raw:
                if not exercise_name:
                    flash('Please write what exercise you did.', 'warning')
                    return redirect(url_for('logs', date=selected_date.isoformat()))
                if not exercise_raw and not exercise_reps_raw:
                    flash('For exercise, fill reps or minutes.', 'warning')
                    return redirect(url_for('logs', date=selected_date.isoformat()))

                exercise_minutes = int(float(exercise_raw)) if exercise_raw else 0
                exercise_reps = int(float(exercise_reps_raw)) if exercise_reps_raw else 0
                if exercise_minutes:
                    log.exercise_minutes = exercise_minutes
                note_parts = [f'Exercise: {exercise_name}']
                if exercise_minutes:
                    note_parts.append(f'{exercise_minutes} min')
                if exercise_reps:
                    note_parts.append(f'{exercise_reps} reps')
                exercise_note = ' · '.join(note_parts)
                existing_notes = (log.notes or '').strip()
                log.notes = exercise_note if not existing_notes else f'{exercise_note}\n{existing_notes}'
                change_text = f'exercise {exercise_name}'
                if exercise_minutes:
                    change_text += f' {exercise_minutes} min'
                if exercise_reps:
                    change_text += f' {exercise_reps} reps'
                changes.append(change_text)
                _log_activity_entry(current_user.id, 'exercise', 'Exercise updated', exercise_note)

            journal_text = (request.form.get('journal_text') or '').strip()
            if journal_text:
                log.journal_text = journal_text
                changes.append('journal updated')

            if not changes:
                flash('Fill at least one field to update the daily log.', 'warning')
                return redirect(url_for('logs', date=selected_date.isoformat()))

            candidate_text = ' '.join(part for part in [log.activity_text or '', log.journal_text or ''] if part).strip()
            meal_detected = _update_log_meal_insight(current_user.id, log, candidate_text)
            latest_event = 'Daily log updated: ' + ', '.join(changes)
            payload = _apply_wellness_update(current_user, selected_date, latest_event)
            _log_activity_entry(current_user.id, 'daily_log', 'Daily log updated', latest_event, impacts=payload.get('feedback', {}).get('metrics'))
            db.session.commit()

            flash('Daily log updated.', 'success')
            return redirect(url_for('logs', date=selected_date.isoformat()))

        current_log = DailyLog.query.filter_by(user_id=current_user.id, log_date=selected_date).first()
        all_logs = DailyLog.query.filter_by(user_id=current_user.id).order_by(DailyLog.log_date.desc()).all()
        due_prompt, upcoming_prompt = _get_due_and_upcoming_prompt(current_user.id)
        morning_prompt_record = None
        return render_template(
            'logs.html',
            current_log=current_log,
            selected_date=selected_date,
            all_logs=all_logs,
            due_hydration_prompt=_serialize_prompt(due_prompt),
            upcoming_hydration_prompt=_serialize_prompt(upcoming_prompt),
            morning_prompt_exists=bool(morning_prompt_record),
            current_snapshot=_progress_snapshot(current_user, current_log),
        )

    @app.route('/calendar')
    @login_required
    def calendar_view():
        today = local_today()
        _ensure_daily_default_tasks(current_user.id, today)
        db.session.commit()
        year = int(request.args.get('year') or today.year)
        month = int(request.args.get('month') or today.month)

        if month < 1:
            month = 12
            year -= 1
        elif month > 12:
            month = 1
            year += 1

        month_start = date(year, month, 1)
        next_month_boundary = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        selected_date = _parse_date(
            request.args.get('selected_date'),
            fallback=today if today.month == month and today.year == year else month_start,
        )

        month_tasks = Task.query.filter(
            Task.user_id == current_user.id,
            Task.task_date >= month_start,
            Task.task_date < next_month_boundary,
        ).order_by(Task.task_date.asc(), Task.sort_order.asc(), Task.created_at.asc()).all()

        month_events = CalendarEvent.query.filter(
            CalendarEvent.user_id == current_user.id,
            CalendarEvent.event_date >= month_start,
            CalendarEvent.event_date < next_month_boundary,
        ).all()
        month_logs = DailyLog.query.filter(
            DailyLog.user_id == current_user.id,
            DailyLog.log_date >= month_start,
            DailyLog.log_date < next_month_boundary,
        ).all()

        selected_tasks = Task.query.filter_by(user_id=current_user.id, task_date=selected_date, completed=True).order_by(Task.sort_order.asc(), Task.created_at.asc()).all()
        selected_events = sorted(
            CalendarEvent.query.filter_by(user_id=current_user.id, event_date=selected_date).all(),
            key=_event_sort_key,
        )
        selected_finished_items = _selected_day_finished_items(selected_events, selected_tasks)
        selected_log = DailyLog.query.filter_by(user_id=current_user.id, log_date=selected_date).first()

        weeks = _month_grid(year, month, month_tasks, month_events, month_logs, current_user, selected_date=selected_date)
        prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
        next_year, next_month_num = (year + 1, 1) if month == 12 else (year, month + 1)

        return render_template(
            'calendar.html',
            weeks=weeks,
            display_date=month_start,
            prev_year=prev_year,
            prev_month=prev_month,
            next_year=next_year,
            next_month=next_month_num,
            selected_date=selected_date,
            selected_tasks=selected_tasks,
            selected_events=selected_events,
            selected_finished_items=selected_finished_items,
            selected_log=selected_log,
        )

    @app.route('/tasks/add', methods=['POST'])
    @login_required
    def add_task():
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        task_date = _parse_date(request.form.get('task_date'))

        if not title:
            flash('Task title is required.', 'danger')
            return redirect(request.referrer or url_for('calendar_view'))

        task = Task(
            user_id=current_user.id,
            title=title,
            description=description,
            task_type='regular',
            task_date=task_date,
            sort_order=_get_next_sort_order(current_user.id, task_date),
        )
        db.session.add(task)
        _log_activity_entry(current_user.id, 'task', 'Task added', f'{title} ({task_date.isoformat()})')
        db.session.commit()
        flash('Task added successfully.', 'success')
        return redirect(request.referrer or url_for('calendar_view', year=task_date.year, month=task_date.month, selected_date=task_date.isoformat()))

    @app.route('/tasks/<int:task_id>/toggle', methods=['POST'])
    @login_required
    def toggle_task(task_id):
        task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
        task.completed = not task.completed
        task.completed_at = local_now().replace(tzinfo=None) if task.completed else None
        event_label = f'Completed todo: {task.title}' if task.completed else f'Reopened todo: {task.title}'
        payload = None

        if task.task_date == local_today() and task.completed:
            if _task_is_hydration(task):
                beverage = _infer_beverage_from_text(task.title)
                amount_ml = int(convert_drink_amount_to_ml(beverage, task.title).get('amount_ml', GLASS_VOLUME_ML) or GLASS_VOLUME_ML)
                log = _get_or_create_log_for_date(current_user.id, task.task_date)
                log.water_ml = int(log.water_ml or 0) + amount_ml
                event_label = f'Completed todo: {task.title} · counted {amount_ml} ml'
                payload = _apply_wellness_update(current_user, task.task_date, f'Drank {amount_ml} ml of {beverage} from completed todo: {task.title}')
            elif _task_type(task) == 'regular':
                payload = _apply_wellness_update(current_user, task.task_date, event_label)

        _log_activity_entry(current_user.id, 'task', 'Task status changed', event_label, impacts=payload.get('feedback', {}).get('metrics') if payload else None)
        db.session.commit()
        flash('Task updated.', 'success')
        return redirect(request.referrer or url_for('calendar_view'))

    @app.route('/tasks/<int:task_id>/edit', methods=['POST'])
    @login_required
    def edit_task(task_id):
        task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
        if _task_is_meal(task):
            return jsonify({'message': 'Meal tasks are updated from the meal popup.'}), 400
        data = request.get_json(silent=True) or request.form
        new_title = (data.get('title') or '').strip()
        if not new_title:
            return jsonify({'message': 'Task text cannot be empty.'}), 400

        task.title = new_title[:200]
        _log_activity_entry(current_user.id, 'task', 'Task edited', task.title)
        db.session.commit()
        return jsonify({'message': 'Task updated.', 'title': task.title, 'wellness_feedback': None})

    @app.route('/tasks/<int:task_id>/delete', methods=['POST'])
    @login_required
    def delete_task(task_id):
        task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
        task_date = task.task_date
        task_title = task.title
        db.session.delete(task)
        _log_activity_entry(current_user.id, 'task', 'Task deleted', task_title)
        db.session.commit()
        flash('Task deleted.', 'info')
        return redirect(request.referrer or url_for('calendar_view'))

    @app.route('/tasks/<int:task_id>/meal', methods=['POST'])
    @login_required
    def update_meal_task(task_id):
        task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
        if not _task_is_meal(task):
            return jsonify({'message': 'This is not a meal task.'}), 400

        data = request.get_json(silent=True) or request.form
        meal_status = (data.get('meal_status') or 'finished').strip().lower()
        meal_text = (data.get('meal_text') or '').strip()
        meal_time = _parse_time(data.get('meal_time')) or local_now().time().replace(second=0, microsecond=0)
        chosen_dt = datetime.combine(task.task_date, meal_time)

        if meal_status not in {'finished', 'skipped'}:
            return jsonify({'message': 'Choose finished or skipped.'}), 400

        task.completed = True
        task.completed_at = chosen_dt
        if meal_status == 'skipped':
            task.description = f'Skipped · {meal_time.strftime("%H:%M")}'
            latest_event = f'Skipped meal: {task.title}'
            _log_activity_entry(current_user.id, 'meal', 'Meal skipped', f'{task.title} · {meal_time.strftime("%H:%M")}', event_at=chosen_dt)
            hydration_prompt = None
        else:
            detail_bits = []
            if meal_text:
                detail_bits.append(meal_text)
            detail_bits.append(meal_time.strftime('%H:%M'))
            task.description = ' · '.join(detail_bits)
            latest_event = f'Completed meal: {task.title}'
            if meal_text:
                latest_event += f' ({meal_text})'
            meal_entry_description = f'{task.title} · {(meal_text or "meal")} · {meal_time.strftime("%H:%M")}'
            hydration_prompt = _create_immediate_hydration_prompt(current_user.id, f'You just finished {task.title.lower()}. A glass of water would help keep your hydration on track.')

        payload = None
        if task.task_date == local_today() and meal_status == 'finished':
            payload = _apply_wellness_update(current_user, task.task_date, latest_event)
            _log_activity_entry(current_user.id, 'meal', 'Meal finished', meal_entry_description, event_at=chosen_dt, impacts=payload.get('feedback', {}).get('metrics'))
            _sync_goal_based_hydration_prompts(current_user, task.task_date)

        if hydration_prompt:
            _consume_wellness_feedback()

        db.session.commit()
        return jsonify({
            'message': 'Meal task saved.',
            'task_id': task.id,
            'task_title': task.title,
            'task_description': task.description,
            'meal_status': meal_status,
            'due_prompt': _serialize_prompt(hydration_prompt),
            'wellness_feedback': None if hydration_prompt else (payload.get('feedback') if payload else None),
        })

    @app.route('/tasks/reorder', methods=['POST'])
    @login_required
    def reorder_tasks():
        data = request.get_json(silent=True) or {}
        task_ids = data.get('task_ids') or []
        task_date = _parse_date(data.get('task_date'), fallback=local_today())

        tasks = Task.query.filter(Task.user_id == current_user.id, Task.task_date == task_date, Task.id.in_(task_ids)).all()
        if len(tasks) != len(task_ids):
            return jsonify({'message': 'Task list mismatch.'}), 400

        task_lookup = {task.id: task for task in tasks}
        for index, task_id in enumerate(task_ids, start=1):
            task_lookup[int(task_id)].sort_order = index

        db.session.commit()
        return jsonify({'message': 'Task order updated.'})

    @app.route('/calendar/events/add', methods=['POST'])
    @login_required
    def add_event():
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        event_date = _parse_date(request.form.get('event_date'))
        event_time = _parse_time(request.form.get('event_time'))

        if not title:
            flash('Event title is required.', 'danger')
            return redirect(url_for('calendar_view', year=event_date.year, month=event_date.month, selected_date=event_date.isoformat()))

        _add_calendar_event(current_user.id, title, event_date, event_time, description)
        _log_activity_entry(current_user.id, 'calendar', 'Calendar event added', title)
        db.session.commit()
        flash('Event added successfully.', 'success')
        return redirect(url_for('calendar_view', year=event_date.year, month=event_date.month, selected_date=event_date.isoformat()))

    @app.route('/calendar/events/<int:event_id>/delete', methods=['POST'])
    @login_required
    def delete_event(event_id):
        event = CalendarEvent.query.filter_by(id=event_id, user_id=current_user.id).first_or_404()
        event_date = event.event_date
        event_title = event.title
        db.session.delete(event)
        _log_activity_entry(current_user.id, 'calendar', 'Calendar event deleted', event_title)
        db.session.commit()
        flash('Event deleted.', 'info')
        return redirect(url_for('calendar_view', year=event_date.year, month=event_date.month, selected_date=event_date.isoformat()))

    @app.route('/pomodoro/save', methods=['POST'])
    @login_required
    def save_pomodoro():
        data = request.get_json(silent=True) or {}
        focus_minutes = int(data.get('focus_minutes') or 25)
        break_minutes = int(data.get('break_minutes') or 5)
        cycle_number = int(data.get('cycle_number') or 1)
        activity_label = (data.get('activity_label') or 'work').strip() or 'work'
        completed_at = local_now().replace(tzinfo=None)

        session_row = PomodoroSession(
            user_id=current_user.id,
            focus_minutes=focus_minutes,
            break_minutes=break_minutes,
            cycle_number=cycle_number,
            activity_label=activity_label,
            completed_at=completed_at,
        )
        db.session.add(session_row)
        _add_calendar_event(
            current_user.id,
            f'Pomodoro done: {activity_label[:80]}',
            completed_at.date(),
            completed_at.time().replace(second=0, microsecond=0),
            f'Cycle {cycle_number}: {focus_minutes} min focus + {break_minutes} min break.',
        )
        payload = _apply_wellness_update(current_user, local_today(), f'Completed Pomodoro cycle {cycle_number} for {activity_label}')
        _log_activity_entry(
            current_user.id,
            'pomodoro',
            'Pomodoro completed',
            f'{activity_label} · cycle {cycle_number} · {focus_minutes} min focus / {break_minutes} min break',
            event_at=completed_at,
            impacts=payload.get('feedback', {}).get('metrics'),
        )
        db.session.commit()
        _consume_wellness_feedback()
        return jsonify(
            {
                'message': 'Pomodoro session saved.',
                'wellness_scores': _serialize_wellness(current_user),
                'wellness_summary': payload.get('summary'),
                'wellness_feedback': payload.get('feedback'),
            }
        )

    @app.route('/sleep/status')
    @login_required
    def sleep_status():
        return jsonify({'due_sleep_reminder': _sleep_reminder_payload(current_user)})

    @app.route('/hydration/status')
    @login_required
    def hydration_status():
        due_prompt, upcoming_prompt = _get_due_and_upcoming_prompt(current_user.id)
        morning_prompt_record = HydrationPrompt.query.filter(
            HydrationPrompt.user_id == current_user.id,
            HydrationPrompt.prompt_type == 'morning',
            db.func.date(HydrationPrompt.due_at) == local_today(),
        ).first()
        return jsonify(
            {
                'due_prompt': _serialize_prompt(due_prompt),
                'upcoming_prompt': _serialize_prompt(upcoming_prompt),
                'morning_prompt_exists': bool(morning_prompt_record),
            }
        )

    @app.route('/hydration/respond', methods=['POST'])
    @login_required
    def hydration_respond():
        data = request.get_json(silent=True) or request.form
        prompt_id = data.get('prompt_id')
        prompt_type = (data.get('prompt_type') or 'meal_now').strip().lower()
        action = (data.get('action') or data.get('response_status') or '').strip().lower()
        beverage = (data.get('beverage') or 'water').strip()
        custom_beverage = (data.get('custom_beverage') or '').strip()
        amount_text = (data.get('amount_text') or '').strip()

        prompt = None
        if prompt_id:
            prompt = HydrationPrompt.query.filter_by(id=int(prompt_id), user_id=current_user.id).first()
        else:
            prompt = HydrationPrompt(
                user_id=current_user.id,
                prompt_type='planned_hydration',
                due_at=local_now().replace(tzinfo=None),
                message='Goal-based hydration reminder.',
                response_status='pending',
            )
            db.session.add(prompt)
            db.session.flush()

        if not prompt:
            return jsonify({'message': 'Prompt not found.'}), 404

        normalized_beverage = _normalize_beverage(beverage, custom_beverage)
        prompt.beverage = normalized_beverage
        prompt.custom_beverage = custom_beverage or None
        prompt.responded_at = local_now().replace(tzinfo=None)

        if action in {'finished', 'done'}:
            prompt.response_status = 'finished'
            amount_ml = convert_drink_amount_to_ml(normalized_beverage, amount_text).get('amount_ml', 250) if amount_text else 250
            log = _get_or_create_log_for_today(current_user.id)
            log.water_ml = int(log.water_ml or 0) + int(amount_ml)
            payload = _apply_wellness_update(current_user, log.log_date, f'Drank {amount_ml} ml of {normalized_beverage}')
            _log_activity_entry(current_user.id, 'hydration', 'Hydration logged', f'{amount_ml} ml {normalized_beverage}', impacts=payload.get('feedback', {}).get('metrics'))
            _sync_goal_based_hydration_prompts(current_user, log.log_date)
            message = f"Great job. Added {amount_ml} ml to today's water total."
        elif action == 'not_yet':
            prompt.response_status = 'not_yet'
            deferred_until = (local_now() + timedelta(minutes=20)).replace(tzinfo=None)
            prompt.due_at = deferred_until
            _defer_active_hydration_prompts(current_user.id, deferred_until, keep_prompt_id=prompt.id)
            task_date = local_today()
            reminder_text = 'Drink a glass of water'
            existing_task = Task.query.filter_by(user_id=current_user.id, task_date=task_date, title=reminder_text, completed=False).first()
            if not existing_task:
                db.session.add(Task(
                    user_id=current_user.id,
                    title=reminder_text,
                    description=None,
                    task_type='regular',
                    task_date=task_date,
                    completed=False,
                    sort_order=_get_next_sort_order(current_user.id, task_date),
                ))
            payload = _apply_wellness_update(current_user, local_today(), 'Hydration reminder postponed')
            _log_activity_entry(current_user.id, 'hydration', 'Hydration reminder postponed', prompt.message, impacts=payload.get('feedback', {}).get('metrics'))
            _sync_goal_based_hydration_prompts(current_user, local_today())
            message = "Okay. I added a water task to today's todo list and will remind you again later."
        else:
            prompt.response_status = 'dismissed'
            payload = _apply_wellness_update(current_user, local_today(), 'Hydration reminder skipped')
            _log_activity_entry(current_user.id, 'hydration', 'Hydration reminder skipped', prompt.message, impacts=payload.get('feedback', {}).get('metrics'))
            _sync_goal_based_hydration_prompts(current_user, local_today())
            message = 'No problem. The reminder was skipped.'

        db.session.commit()
        _consume_wellness_feedback()
        due_prompt, upcoming_prompt = _get_due_and_upcoming_prompt(current_user.id)
        return jsonify(
            {
                'message': message,
                'due_prompt': _serialize_prompt(due_prompt),
                'upcoming_prompt': _serialize_prompt(upcoming_prompt),
                'wellness_scores': _serialize_wellness(current_user),
                'wellness_summary': payload.get('summary'),
                'wellness_feedback': payload.get('feedback'),
            }
        )
