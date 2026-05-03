from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from statistics import mean

from .. import db
from ..models import (
    ActivityEntry,
    DailyLog,
    DailySignal,
    EyeExercisePrompt,
    MoodEntry,
    PatternRule,
    PomodoroSession,
    Task,
    User,
    UserBaseline,
    UserPatternState,
)
from ..constants import MOOD_SCORE_MAP, PATTERN_EVIDENCE_MESSAGES, PATTERN_MIN_ACTIVE_DAYS_FOR_READY
from ..utils.timez import local_now, local_today


DEFAULT_PATTERN_RULES = [
    {
        'rule_key': 'hydration_lag',
        'display_name': 'Hydration lag pattern',
        'safe_headline': 'Your hydration has shifted below your goal.',
        'description': 'Your water intake looks lower than your own recent rhythm. This is a behavioral trend, not a medical diagnosis.',
        'window_days': 3,
        'trigger_score': 4,
        'clear_score_ratio': 0.70,
        'intervention_title': 'Drink water + 2-minute stretch',
        'intervention_detail': 'Drink one glass of water slowly, then stand up and stretch your neck, shoulders, and back for two minutes.',
        'intervention_task_title': 'Drink water and do a 2-minute stretch',
        'intervention_task_description': 'Pattern support: hydration lag. Drink one glass of water slowly, then stretch for two minutes.',
        'feedback_question': 'After water + stretch, how much better do you feel out of 10?',
    },
    {
        'rule_key': 'overfocus',
        'display_name': 'Overfocus pattern',
        'safe_headline': 'Your focus time is running high compared with recovery breaks.',
        'description': 'Your recent focus rhythm looks heavier than your recent break rhythm. This is a productivity/recovery signal, not a health diagnosis.',
        'window_days': 3,
        'trigger_score': 4,
        'clear_score_ratio': 0.70,
        'intervention_title': '20-20-20 eye break',
        'intervention_detail': 'Look at something far away for 20 seconds, blink slowly, and loosen your shoulders before the next focus block.',
        'intervention_task_title': 'Take a 20-20-20 eye break',
        'intervention_task_description': 'Pattern support: overfocus. Look far away for 20 seconds, blink slowly, and relax your shoulders.',
        'feedback_question': 'After the eye break, how much better do your eyes/body feel out of 10?',
    },
    {
        'rule_key': 'fatigue',
        'display_name': 'Fatigue pattern',
        'safe_headline': 'Your recovery signals have been lower for a few days.',
        'description': 'Sleep, mood, and focus signals may be moving in the same low-recovery direction. This is a behavioral trend, not a diagnosis.',
        'window_days': 3,
        'trigger_score': 6,
        'clear_score_ratio': 0.70,
        'intervention_title': 'Breathing reset + early sleep reminder',
        'intervention_detail': 'Take one minute of slow breathing now, then set an earlier sleep reminder for tonight if possible.',
        'intervention_task_title': 'Do a 1-minute breathing reset and plan earlier sleep',
        'intervention_task_description': 'Pattern support: fatigue trend. Take five slow breaths and set a realistic earlier sleep reminder.',
        'feedback_question': 'After the breathing reset, how much steadier do you feel out of 10?',
    },
    {
        'rule_key': 'reduced_recovery',
        'display_name': 'Reduced recovery pattern',
        'safe_headline': 'Your recovery balance looks lower than usual.',
        'description': 'Your recent sleep/rest signals look low compared with your recent workload. This suggests adjusting today’s target, not diagnosing a condition.',
        'window_days': 3,
        'trigger_score': 4,
        'clear_score_ratio': 0.70,
        'intervention_title': 'Lower today’s focus target by 25%',
        'intervention_detail': 'Reduce today’s focus target a little and add one short recovery break so the day is easier to complete.',
        'intervention_task_title': 'Lower today’s focus target by 25%',
        'intervention_task_description': 'Pattern support: reduced recovery. Make today’s focus target smaller and add one short recovery break.',
        'feedback_question': 'After lowering the target, how manageable does today feel out of 10?',
    },
]


def _safe_float(value, default=0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value, default=0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return int(default)


def _average(values: list[float], default=0.0) -> float:
    clean = [float(v) for v in values if v is not None]
    return float(mean(clean)) if clean else float(default)


def _clamped_rate(value: float, goal: float) -> float:
    if goal <= 0:
        return 0.0
    return max(0.0, min(1.5, float(value) / float(goal)))


def _json_loads(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _evidence(key: str, **params) -> dict:
    """Store evidence as a stable key + params instead of hard-coded prose."""
    return {'key': key, 'params': params}


def _render_evidence_item(item) -> str:
    """Render stored evidence for the current English UI.

    Old databases may still contain plain strings, so keep them readable.
    Future Chinese/other-language UI can swap PATTERN_EVIDENCE_MESSAGES.
    """
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return ''
    key = str(item.get('key') or '').strip()
    params = item.get('params') or {}
    if not isinstance(params, dict):
        params = {}
    template = PATTERN_EVIDENCE_MESSAGES.get(key, key.replace('_', ' ').strip() or 'Pattern evidence')
    try:
        return template.format(**params)
    except Exception:
        return template


def _render_evidence_list(items) -> list[str]:
    return [text for text in (_render_evidence_item(item) for item in (items or [])) if text]


def _ensure_default_pattern_rules() -> None:
    for item in DEFAULT_PATTERN_RULES:
        changed = False
        rule = PatternRule.query.filter_by(rule_key=item['rule_key']).first()
        if not rule:
            rule = PatternRule(rule_key=item['rule_key'])
            db.session.add(rule)
            changed = True
        for key, value in item.items():
            if getattr(rule, key, None) != value:
                setattr(rule, key, value)
                changed = True
        if not rule.is_enabled:
            rule.is_enabled = True
            changed = True
        if changed:
            rule.updated_at = local_now().replace(tzinfo=None)
    db.session.flush()


def _mood_score_for_day(user_id: int, day: date) -> int:
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    entries = MoodEntry.query.filter(
        MoodEntry.user_id == user_id,
        MoodEntry.event_at >= start,
        MoodEntry.event_at < end,
    ).all()
    if entries:
        return int(round(_average([entry.mood_value for entry in entries], 50)))
    log = DailyLog.query.filter_by(user_id=user_id, log_date=day).first()
    if log and log.mood_label:
        normalized = (log.mood_label or '').strip().lower()
        direct = (log.mood_label or '').strip()
        return MOOD_SCORE_MAP.get(normalized, MOOD_SCORE_MAP.get(direct, 50))
    return 50


def _activity_rest_count(user_id: int, day: date) -> int:
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    rows = ActivityEntry.query.filter(
        ActivityEntry.user_id == user_id,
        ActivityEntry.event_at >= start,
        ActivityEntry.event_at < end,
    ).all()
    keywords = ('break', 'rest', 'stretch', 'walk', 'breath', 'eye', 'relax', 'quiet reset')
    count = 0
    for row in rows:
        text = f'{row.entry_type or ""} {row.title or ""} {row.description or ""}'.lower()
        if any(word in text for word in keywords):
            count += 1
    return count


def _upsert_daily_signal(user: User, day: date) -> DailySignal:
    log = DailyLog.query.filter_by(user_id=user.id, log_date=day).first()
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)

    sessions = PomodoroSession.query.filter(
        PomodoroSession.user_id == user.id,
        PomodoroSession.completed_at >= start,
        PomodoroSession.completed_at < end,
    ).all()
    tasks = Task.query.filter_by(user_id=user.id, task_date=day).all()
    eye_break_count = EyeExercisePrompt.query.filter(
        EyeExercisePrompt.user_id == user.id,
        EyeExercisePrompt.due_at >= start,
        EyeExercisePrompt.due_at < end,
        EyeExercisePrompt.response_status == 'finished',
    ).count()

    water_goal_ml = _safe_int(user.daily_water_goal_ml, 2000)
    water_ml = _safe_int(getattr(log, 'water_ml', 0), 0)
    sleep_hours = _safe_float(getattr(log, 'sleep_hours', 0), 0)
    exercise_minutes = _safe_int(getattr(log, 'exercise_minutes', 0), 0)
    focus_sessions = len(sessions)
    focus_minutes = sum(_safe_int(session.focus_minutes, 0) for session in sessions)
    completed_tasks = sum(1 for task in tasks if task.completed)
    todo_completion_rate = _clamped_rate(completed_tasks, len(tasks)) if tasks else 0.0

    signal = DailySignal.query.filter_by(user_id=user.id, signal_date=day).first()
    if not signal:
        signal = DailySignal(user_id=user.id, signal_date=day)
        db.session.add(signal)
    signal.has_daily_log = bool(log)
    signal.active_app_day = bool(log or sessions or tasks)
    signal.sleep_hours = sleep_hours
    signal.mood_score = _mood_score_for_day(user.id, day)
    signal.focus_sessions = focus_sessions
    signal.focus_minutes = focus_minutes
    signal.focus_completion_rate = todo_completion_rate
    signal.water_ml = water_ml
    signal.water_goal_ml = water_goal_ml
    signal.water_completion_rate = _clamped_rate(water_ml, water_goal_ml)
    signal.exercise_minutes = exercise_minutes
    signal.eye_break_count = eye_break_count
    signal.rest_break_count = _activity_rest_count(user.id, day)
    signal.updated_at = local_now().replace(tzinfo=None)
    db.session.flush()
    return signal


def _stats_from_signals(signals: list[DailySignal]) -> dict[str, float]:
    sleep_values = [float(row.sleep_hours) for row in signals if float(row.sleep_hours or 0) > 0]
    return {
        'days': len(signals),
        'active_days': sum(1 for row in signals if bool(getattr(row, 'active_app_day', False))),
        'logged_days': sum(1 for row in signals if bool(getattr(row, 'has_daily_log', False))),
        'sleep_logged_days': len(sleep_values),
        'avg_sleep_hours': _average(sleep_values, 0.0),
        'avg_mood_score': _average([row.mood_score for row in signals], 50.0),
        'avg_focus_sessions': _average([row.focus_sessions for row in signals], 0.0),
        'avg_focus_minutes': _average([row.focus_minutes for row in signals], 0.0),
        'avg_focus_completion_rate': _average([row.focus_completion_rate for row in signals], 0.0),
        'avg_water_completion_rate': _average([row.water_completion_rate for row in signals], 0.0),
        'avg_water_ml': _average([row.water_ml for row in signals], 0.0),
        'avg_exercise_minutes': _average([row.exercise_minutes for row in signals], 0.0),
        'avg_eye_break_count': _average([row.eye_break_count for row in signals], 0.0),
        'avg_rest_break_count': _average([row.rest_break_count for row in signals], 0.0),
    }


def _build_baseline(user: User, today: date, window_days: int = 14) -> UserBaseline:
    baseline_start = today - timedelta(days=window_days + 3)
    baseline_end = today - timedelta(days=3)
    signals = DailySignal.query.filter(
        DailySignal.user_id == user.id,
        DailySignal.signal_date >= baseline_start,
        DailySignal.signal_date < baseline_end,
    ).order_by(DailySignal.signal_date.asc()).all()
    if len(signals) < 3:
        # Avoid letting today's data become its own baseline. With too little history,
        # patterns stay quiet and the UI explains that WellHabit is still learning.
        fallback_start = today - timedelta(days=window_days)
        fallback_end = today - timedelta(days=1)
        signals = DailySignal.query.filter(
            DailySignal.user_id == user.id,
            DailySignal.signal_date >= fallback_start,
            DailySignal.signal_date <= fallback_end,
        ).order_by(DailySignal.signal_date.asc()).all()

    metrics = _stats_from_signals(signals)
    baseline = UserBaseline.query.filter_by(user_id=user.id).first()
    if not baseline:
        baseline = UserBaseline(user_id=user.id)
        db.session.add(baseline)
    baseline.window_days = window_days
    baseline.calculated_on = today
    baseline.metrics_json = json.dumps(metrics, ensure_ascii=False)
    baseline.updated_at = local_now().replace(tzinfo=None)
    db.session.flush()
    return baseline


def _recent_signals(user_id: int, today: date, window_days: int) -> list[DailySignal]:
    start = today - timedelta(days=max(1, int(window_days)) - 1)
    return DailySignal.query.filter(
        DailySignal.user_id == user_id,
        DailySignal.signal_date >= start,
        DailySignal.signal_date <= today,
    ).order_by(DailySignal.signal_date.asc()).all()


def _score_hydration_lag(recent: dict, baseline: dict) -> tuple[int, list[str]]:
    if recent.get('active_days', 0) < 2:
        return 0, []
    score = 0
    evidence = []
    if recent['avg_water_completion_rate'] < 0.60:
        score += 3
        evidence.append(_evidence('water_below_goal_pct', pct=60))
    if baseline.get('avg_water_completion_rate', 0) >= 0.40 and recent['avg_water_completion_rate'] < baseline['avg_water_completion_rate'] * 0.75:
        score += 2
        evidence.append(_evidence('water_below_baseline_pct', pct=25))
    if recent['avg_water_ml'] < 1000:
        score += 1
        evidence.append(_evidence('water_under_ml', ml=1000))
    return score, evidence


def _score_overfocus(recent: dict, baseline: dict) -> tuple[int, list[str]]:
    if recent.get('active_days', 0) < 2:
        return 0, []
    score = 0
    evidence = []
    baseline_focus = baseline.get('avg_focus_minutes', 0)
    if recent['avg_focus_minutes'] >= 90:
        score += 2
        evidence.append(_evidence('focus_time_high'))
    if baseline_focus >= 30 and recent['avg_focus_minutes'] > baseline_focus * 1.50:
        score += 2
        evidence.append(_evidence('focus_above_baseline_pct', pct=50))
    if recent['avg_focus_sessions'] >= 3 and recent['avg_eye_break_count'] < 1:
        score += 2
        evidence.append(_evidence('eye_breaks_low'))
    if recent['avg_rest_break_count'] < 1 and recent['avg_focus_minutes'] >= 60:
        score += 1
        evidence.append(_evidence('rest_breaks_low_for_focus'))
    return score, evidence


def _score_fatigue(recent: dict, baseline: dict) -> tuple[int, list[str]]:
    if recent.get('active_days', 0) < 2:
        return 0, []
    score = 0
    evidence = []
    if 0 < recent['avg_sleep_hours'] < 6:
        score += 3
        evidence.append(_evidence('sleep_below_hours', hours=6))
    if baseline.get('avg_mood_score', 50) > 0 and recent['avg_mood_score'] < baseline.get('avg_mood_score', 50) * 0.80:
        score += 2
        evidence.append(_evidence('mood_below_baseline_pct', pct=20))
    if baseline.get('avg_focus_minutes', 0) >= 20 and recent['avg_focus_minutes'] < baseline['avg_focus_minutes'] * 0.70:
        score += 2
        evidence.append(_evidence('focus_below_baseline_pct', pct=30))
    if baseline.get('avg_rest_break_count', 0) >= 1 and recent['avg_rest_break_count'] > baseline['avg_rest_break_count'] * 1.50:
        score += 1
        evidence.append(_evidence('rest_activity_higher_pct', pct=50))
    return score, evidence


def _score_reduced_recovery(recent: dict, baseline: dict, user: User) -> tuple[int, list[str]]:
    if recent.get('active_days', 0) < 2:
        return 0, []
    score = 0
    evidence = []
    sleep_goal = _safe_float(user.daily_sleep_goal_hours, 8.0)
    if 0 < recent['avg_sleep_hours'] < sleep_goal * 0.80:
        score += 2
        evidence.append(_evidence('sleep_below_target_pct', pct=80))
    if recent['avg_focus_minutes'] >= max(60, baseline.get('avg_focus_minutes', 0) * 1.20) and recent['avg_rest_break_count'] < 1:
        score += 2
        evidence.append(_evidence('workload_high_recovery_low'))
    if baseline.get('avg_exercise_minutes', 0) >= 10 and recent['avg_exercise_minutes'] < baseline['avg_exercise_minutes'] * 0.60:
        score += 1
        evidence.append(_evidence('exercise_below_baseline_pct', pct=40))
    if baseline.get('avg_mood_score', 50) > 0 and recent['avg_mood_score'] < baseline.get('avg_mood_score', 50) * 0.85:
        score += 1
        evidence.append(_evidence('mood_lower_than_baseline'))
    return score, evidence


def _score_rule(rule: PatternRule, recent_stats: dict, baseline_metrics: dict, user: User) -> tuple[int, list[str]]:
    key = rule.rule_key
    if key == 'hydration_lag':
        return _score_hydration_lag(recent_stats, baseline_metrics)
    if key == 'overfocus':
        return _score_overfocus(recent_stats, baseline_metrics)
    if key == 'fatigue':
        return _score_fatigue(recent_stats, baseline_metrics)
    if key == 'reduced_recovery':
        return _score_reduced_recovery(recent_stats, baseline_metrics, user)
    return 0, []


def refresh_user_patterns_once_per_day(user: User) -> None:
    today = local_today()
    _ensure_default_pattern_rules()
    baseline = UserBaseline.query.filter_by(user_id=user.id).first()
    if baseline and baseline.calculated_on == today:
        return
    for offset in range(29, -1, -1):
        _upsert_daily_signal(user, today - timedelta(days=offset))
    baseline = _build_baseline(user, today, window_days=14)
    baseline_metrics = _json_loads(baseline.metrics_json, {})
    now_naive = local_now().replace(tzinfo=None)

    rules = PatternRule.query.filter_by(is_enabled=True).order_by(PatternRule.rule_key.asc()).all()
    for rule in rules:
        recent = _stats_from_signals(_recent_signals(user.id, today, rule.window_days or 3))
        score, evidence = _score_rule(rule, recent, baseline_metrics, user)
        state = UserPatternState.query.filter_by(user_id=user.id, rule_key=rule.rule_key).first()
        if score >= int(rule.trigger_score or 1):
            if not state:
                state = UserPatternState(user_id=user.id, rule_key=rule.rule_key)
                db.session.add(state)
            if state.status == 'archived':
                state.triggered_on = today
                state.resolved_on = None
                state.no_thanks_count = 0
                state.push_suppressed_until = None
            state.status = 'active'
            state.score = score
            state.score_threshold = int(rule.trigger_score or 1)
            state.evidence_json = json.dumps(evidence, ensure_ascii=False)
            state.last_scored_on = today
            state.consecutive_low_days = 0
            if not state.triggered_on:
                state.triggered_on = today
            state.updated_at = now_naive
        elif state and state.status == 'active':
            state.score = score
            state.score_threshold = int(rule.trigger_score or 1)
            state.evidence_json = json.dumps(evidence, ensure_ascii=False)
            state.last_scored_on = today
            clear_score = float(rule.trigger_score or 1) * float(rule.clear_score_ratio or 0.70)
            if score < clear_score:
                state.consecutive_low_days = int(state.consecutive_low_days or 0) + 1
            else:
                state.consecutive_low_days = 0
            if int(state.consecutive_low_days or 0) >= 3:
                state.status = 'archived'
                state.resolved_on = today
                state.feedback_status = 'auto_archived'
            state.updated_at = now_naive
    db.session.flush()


def _state_to_card(state: UserPatternState, rule: PatternRule, suppressed: bool = False) -> dict:
    evidence_items = _json_loads(state.evidence_json, [])
    evidence = _render_evidence_list(evidence_items)
    return {
        'id': state.id,
        'rule_key': state.rule_key,
        'display_name': rule.display_name,
        'headline': rule.safe_headline,
        'description': rule.description,
        'score': int(state.score or 0),
        'threshold': int(state.score_threshold or rule.trigger_score or 1),
        'triggered_on': state.triggered_on,
        'resolved_on': state.resolved_on,
        'last_scored_on': state.last_scored_on,
        'status': state.status,
        'feedback_status': state.feedback_status,
        'no_thanks_count': int(state.no_thanks_count or 0),
        'suppressed': suppressed,
        'suppressed_until': state.push_suppressed_until,
        'evidence': evidence[:4],
        'evidence_items': evidence_items[:4],
        'intervention_title': rule.intervention_title,
        'intervention_detail': rule.intervention_detail,
        'feedback_question': rule.feedback_question,
    }




def record_camera_fatigue_signal(user: User, payload: dict) -> dict:
    """Store Pomodoro camera fatigue signals as behavioral pattern evidence.

    This deliberately stores weak, camera-derived signals as pattern evidence, not as a
    medical diagnosis. Mild signals become activity history. Heavy / microsleep /
    confirmed-break signals also activate the existing fatigue pattern card.
    """
    if not isinstance(payload, dict):
        payload = {}
    metrics = payload.get('metrics') if isinstance(payload.get('metrics'), dict) else {}
    timer = payload.get('timer') if isinstance(payload.get('timer'), dict) else {}
    event_type = str(payload.get('event_type') or 'camera_signal').strip().lower()[:40]
    now = local_now().replace(tzinfo=None)
    today = local_today()
    fatigue_score = max(0.0, min(1.0, _safe_float(metrics.get('fatigue_score'), 0.0)))
    perclos = max(0.0, min(1.0, _safe_float(metrics.get('perclos'), 0.0)))
    yawn_count = max(0, _safe_int(metrics.get('yawn_count_10m'), 0))
    positive_affect_signal = bool(metrics.get('possible_positive_affect_signal')) or event_type == 'possible_relaxed_affect'
    microsleep = bool(metrics.get('microsleep')) or event_type == 'microsleep'
    heavy = event_type in {'heavy_signal', 'microsleep', 'break_confirmed'} or fatigue_score >= 0.70 or microsleep
    mild = event_type == 'mild_signal' or fatigue_score >= 0.50

    detail_parts = [
        f'source=pomodoro_camera',
        f'event={event_type}',
        f'fatigue_score={fatigue_score:.2f}',
        f'perclos={perclos:.2f}',
        f'yawns_10m={yawn_count}',
    ]
    if positive_affect_signal:
        detail_parts.append('possible_positive_affect_signal=true')
    activity_label = str(timer.get('activity_label') or '').strip()
    if activity_label:
        detail_parts.append(f'activity={activity_label[:80]}')
    db.session.add(ActivityEntry(
        user_id=user.id,
        entry_type='camera_fatigue',
        title='Possible relaxed affect signal during focus' if positive_affect_signal else ('Possible fatigue signal during focus' if mild or heavy else 'Camera focus signal sampled'),
        description=' · '.join(detail_parts),
        event_at=now,
    ))

    state_id = None
    activated = False
    if heavy:
        _ensure_default_pattern_rules()
        rule = PatternRule.query.filter_by(rule_key='fatigue', is_enabled=True).first()
        threshold = int(getattr(rule, 'trigger_score', 6) or 6)
        state = UserPatternState.query.filter_by(user_id=user.id, rule_key='fatigue').first()
        if not state:
            state = UserPatternState(user_id=user.id, rule_key='fatigue')
            db.session.add(state)
        evidence = [
            _evidence('camera_fatigue_score', pct=round(fatigue_score * 100)),
            _evidence('camera_perclos_pct', pct=round(perclos * 100)),
        ]
        if microsleep:
            evidence.append(_evidence('camera_microsleep'))
        if yawn_count:
            evidence.append(_evidence('camera_yawns', count=yawn_count))
        if bool(metrics.get('nodding')) or abs(_safe_float(metrics.get('pitch_delta'), 0.0)) >= 15:
            evidence.append(_evidence('camera_head_signal'))
        if bool(metrics.get('sustained_gaze_down')):
            evidence.append(_evidence('camera_gaze_down'))
        state.status = 'active'
        state.score = max(threshold, min(10, int(round(fatigue_score * 10)) or threshold))
        state.score_threshold = threshold
        state.evidence_json = json.dumps(evidence, ensure_ascii=False)
        state.triggered_on = state.triggered_on or today
        state.last_scored_on = today
        state.consecutive_low_days = 0
        state.updated_at = now
        db.session.flush()
        state_id = state.id
        activated = True

    return {
        'ok': True,
        'event_type': event_type,
        'stored': True,
        'active_pattern': activated,
        'pattern_state_id': state_id,
        'message': 'Camera signal stored as behavioral pattern evidence, not a diagnosis.',
    }


def get_pattern_learning_state(user_id: int, lookback_days: int = 14) -> dict:
    today = local_today()
    start = today - timedelta(days=max(1, lookback_days) - 1)
    signals = DailySignal.query.filter(
        DailySignal.user_id == user_id,
        DailySignal.signal_date >= start,
        DailySignal.signal_date <= today,
    ).order_by(DailySignal.signal_date.asc()).all()
    active_days = sum(1 for row in signals if bool(getattr(row, 'active_app_day', False)))
    logged_days = sum(1 for row in signals if bool(getattr(row, 'has_daily_log', False)))
    return {
        'active_days': active_days,
        'logged_days': logged_days,
        'lookback_days': lookback_days,
        'ready': active_days >= PATTERN_MIN_ACTIVE_DAYS_FOR_READY,
        'min_active_days': PATTERN_MIN_ACTIVE_DAYS_FOR_READY,
    }


def get_active_pattern_cards(user_id: int, include_suppressed: bool = False) -> list[dict]:
    today = local_today()
    rules = {rule.rule_key: rule for rule in PatternRule.query.filter_by(is_enabled=True).all()}
    states = UserPatternState.query.filter_by(user_id=user_id, status='active').order_by(UserPatternState.score.desc(), UserPatternState.updated_at.desc()).all()
    cards = []
    for state in states:
        rule = rules.get(state.rule_key)
        if not rule:
            continue
        suppressed = bool(state.push_suppressed_until and state.push_suppressed_until > today)
        if suppressed and not include_suppressed:
            continue
        cards.append(_state_to_card(state, rule, suppressed=suppressed))
    return cards


def get_past_pattern_cards(user_id: int, limit: int = 6) -> list[dict]:
    rules = {rule.rule_key: rule for rule in PatternRule.query.filter_by(is_enabled=True).all()}
    states = UserPatternState.query.filter_by(user_id=user_id, status='archived').order_by(UserPatternState.resolved_on.desc(), UserPatternState.updated_at.desc()).limit(limit).all()
    cards = []
    for state in states:
        rule = rules.get(state.rule_key)
        if rule:
            cards.append(_state_to_card(state, rule, suppressed=False))
    return cards


def handle_pattern_response(user: User, state_id: int, action: str, rating: int | None = None) -> tuple[str, str]:
    from .wellness import _apply_wellness_update
    from .tasks import _get_next_sort_order

    action = (action or '').strip().lower()
    state = UserPatternState.query.filter_by(id=state_id, user_id=user.id, status='active').first()
    if not state:
        return 'Pattern is no longer active.', 'warning'
    rule = PatternRule.query.filter_by(rule_key=state.rule_key, is_enabled=True).first()
    if not rule:
        return 'Pattern rule is not available.', 'warning'

    now = local_now()
    today = local_today()
    state.updated_at = now.replace(tzinfo=None)

    if action in {'finished', 'accept'}:
        clean_rating = None
        if rating is not None:
            parsed_rating = _safe_int(rating, 0)
            clean_rating = max(1, min(10, parsed_rating)) if parsed_rating else None
        state.feedback_status = 'finished'
        state.feedback_rating = clean_rating
        state.feedback_note = 'User completed the pattern intervention.'
        _apply_wellness_update(user, today, f'Completed pattern intervention: {rule.intervention_title} for {rule.display_name}')
        db.session.add(ActivityEntry(
            user_id=user.id,
            entry_type='pattern',
            title='Pattern intervention finished',
            description=f'{rule.display_name} · {rule.intervention_title}' + (f' · rating {clean_rating}/10' if clean_rating else ''),
            event_at=now.replace(tzinfo=None),
        ))
        return 'Pattern support marked as finished.', 'success'

    if action == 'not_yet':
        existing_task = None
        if state.intervention_task_id:
            existing_task = Task.query.filter_by(id=state.intervention_task_id, user_id=user.id, completed=False).first()
        if not existing_task:
            existing_task = Task.query.filter_by(
                user_id=user.id,
                task_date=today,
                task_type='ai_suggestion',
                ai_generated_source='pattern_recognition',
                ai_suggestion_key=f'pattern:{rule.rule_key}',
                completed=False,
            ).first()
        if not existing_task:
            existing_task = Task(
                user_id=user.id,
                title=rule.intervention_task_title,
                description=rule.intervention_task_description,
                task_type='ai_suggestion',
                task_date=today,
                sort_order=_get_next_sort_order(user.id, today),
                ai_generated_source='pattern_recognition',
                ai_suggestion_key=f'pattern:{rule.rule_key}',
                ai_followup_question=rule.feedback_question,
            )
            db.session.add(existing_task)
            db.session.flush()
        state.intervention_task_id = existing_task.id
        state.feedback_status = 'not_yet'
        db.session.add(ActivityEntry(
            user_id=user.id,
            entry_type='pattern',
            title='Pattern support added to todo',
            description=f'{rule.display_name} · {rule.intervention_task_title}',
            event_at=now.replace(tzinfo=None),
        ))
        return 'Added the pattern support action to today’s todo list.', 'success'

    if action == 'no_thanks':
        state.no_thanks_count = int(state.no_thanks_count or 0) + 1
        state.feedback_status = 'no_thanks'
        days = 7 if int(state.no_thanks_count or 0) >= 3 else 2
        state.push_suppressed_until = today + timedelta(days=days)
        db.session.add(ActivityEntry(
            user_id=user.id,
            entry_type='pattern',
            title='Pattern support dismissed',
            description=f'{rule.display_name} · snoozed for {days} days',
            event_at=now.replace(tzinfo=None),
        ))
        return 'No problem. This pattern will be shown less often.', 'info'

    return 'Choose Finished, Not yet, or No thanks.', 'warning'
