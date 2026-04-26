import json
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import db
from ..ai_services import convert_drink_amount_to_ml, suggest_personal_goals
from ..constants import BREAK_EXERCISES, EYE_EXERCISE_THRESHOLD_MINUTES, HISTORY_PAGE_SIZE
from ..services.care_chat import _care_chat_history_payload, _get_or_create_active_care_chat_session
from ..services.care_intents import CARE_BOUNDARY_LINES
from ..services.eye_exercise import (
    _complete_eye_exercise,
    _get_active_eye_exercise_prompt,
    _serialize_eye_exercise_prompt,
)
from ..models import ActivityEntry, BreakSession, CalendarEvent, DailyLog, Task
from ..services._legacy_support import _parse_clock_text, _parse_float, _parse_int
from ..services.activity import _activity_entry_view_model, _add_calendar_event, _event_sort_key, _log_activity_entry, _recent_activity_preview
from ..services.ai_suggestions import _maybe_create_ai_suggestion_task
from ..services.patterns import (
    get_active_pattern_cards,
    get_past_pattern_cards,
    get_pattern_learning_state,
    handle_pattern_response,
    record_camera_fatigue_signal,
    refresh_user_patterns_once_per_day,
)
from ..services.hydration import (
    HYDRATION_SLOT_META,
    _default_hydration_schedule_map,
    _ensure_hydration_schedule_defaults,
    _get_due_and_upcoming_prompt,
    _get_or_create_log_for_date,
    _get_or_create_log_for_today,
    _hydration_goal_plan,
    _increment_water_if_within_limit,
    _hydration_schedule_rows,
    _missed_hydration_summary,
    _serialize_prompt,
    _sync_goal_based_hydration_prompts,
    _sync_meal_task_completion,
    _update_log_meal_insight,
)
from ..services.tasks import _ensure_daily_default_tasks, _get_next_sort_order
from ..services.wellness import (
    MOOD_CHIP_CHOICES,
    MOOD_OPTIONS,
    _apply_wellness_update,
    _build_focus_payload,
    _build_goal_cards,
    _build_mood_trend_payload,
    _build_progress_cards,
    _build_quick_stats,
    _build_streak_cards,
    _decorate_feedback_with_ai_task,
    _ensure_baseline_scores,
    _month_grid,
    _mood_badge_payload,
    _profile_locked,
    _progress_snapshot,
    _record_mood_entry,
    _selected_day_finished_items,
    _selected_mood_emoji,
    _serialize_wellness,
    _store_wellness_feedback,
)
from ..utils.text import _clean_text, _normalize_mood_choice
from ..utils.timez import _aware_local_datetime, _local_duration_hours, _parse_date, _parse_time, local_now, local_today

bp = Blueprint('main', __name__)


def _break_exercise_map():
    return {item['key']: item for item in BREAK_EXERCISES}


def _safe_json_dumps(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    except TypeError:
        return '{}'


def _safe_json_loads(value, fallback=None):
    if fallback is None:
        fallback = []
    try:
        parsed = json.loads(value or '')
    except Exception:
        return fallback
    return parsed if parsed is not None else fallback


def _time_of_day_copy(now=None) -> str:
    current = now or local_now()
    hour = current.hour
    if 5 <= hour < 12:
        return 'A gentle morning reset can help you start again without forcing productivity.'
    if 12 <= hour < 18:
        return 'A short afternoon break can reduce screen fatigue before your next focus block.'
    return 'A low-stimulation evening break can help your body slow down.'


def _default_break_key(reason: str) -> str:
    reason = (reason or 'manual').strip().lower()
    for item in BREAK_EXERCISES:
        if reason in set(item.get('default_for') or []):
            return item['key']
    return BREAK_EXERCISES[0]['key']


def _break_duration_minutes(row: BreakSession) -> int:
    if not row.started_at:
        return 0
    end = row.ended_at or local_now().replace(tzinfo=None)
    seconds = max(0, int((end - row.started_at).total_seconds()))
    return max(1, round(seconds / 60)) if seconds else 0


def _break_habits_payload(user_id: int) -> dict:
    start = local_now().replace(tzinfo=None) - timedelta(days=7)
    rows = BreakSession.query.filter(
        BreakSession.user_id == user_id,
        BreakSession.started_at >= start,
    ).order_by(BreakSession.started_at.desc()).all()
    exercise_titles = {item['key']: item['title'] for item in BREAK_EXERCISES}
    report_counts = {'better': 0, 'same': 0, 'still_tired': 0, 'skipped': 0}
    exercise_counts = {}
    for row in rows:
        report_counts[row.self_report or 'skipped'] = report_counts.get(row.self_report or 'skipped', 0) + 1
        for key in _safe_json_loads(row.exercises_done, []):
            exercise_counts[key] = exercise_counts.get(key, 0) + 1
    top_key = max(exercise_counts, key=exercise_counts.get) if exercise_counts else None
    return {
        'weekly_count': len(rows),
        'report_counts': report_counts,
        'top_exercise': exercise_titles.get(top_key, '—') if top_key else '—',
        'recent': rows[:4],
    }



def _apply_sleep_submission(log, form_data, selected_date):
    sleep_mode = (form_data.get('sleep_input_mode') or 'hours').strip().lower()
    if sleep_mode not in {'hours', 'range'}:
        sleep_mode = 'hours'

    sleep_raw = (form_data.get('sleep_hours') or '').strip()
    sleep_start_raw = (form_data.get('sleep_start_time') or '').strip()
    sleep_end_raw = (form_data.get('sleep_end_time') or '').strip()

    has_hours = bool(sleep_raw)
    has_range = bool(sleep_start_raw or sleep_end_raw)

    if sleep_mode == 'range':
        if has_hours and not has_range:
            return {'error': 'You selected time range mode. Fill both times or switch back to hours mode.'}
        if not has_range:
            return {'changed': False}
        if not sleep_start_raw or not sleep_end_raw:
            return {'error': 'Please fill both sleep start and sleep end time.'}
        sleep_start = _parse_time(sleep_start_raw)
        sleep_end = _parse_time(sleep_end_raw)
        if not sleep_start or not sleep_end:
            return {'error': 'Please enter a valid sleep time range.'}
        sleep_start_dt = _aware_local_datetime(selected_date, sleep_start, fold=0)
        sleep_end_date = selected_date + timedelta(days=1) if sleep_end <= sleep_start else selected_date
        sleep_end_dt = _aware_local_datetime(sleep_end_date, sleep_end, fold=1)
        sleep_hours_value = round(_local_duration_hours(sleep_start_dt, sleep_end_dt), 2)
        if sleep_hours_value <= 0 or sleep_hours_value > 24:
            return {'error': 'Sleep hours must stay between 0 and 24.'}
        log.sleep_hours = sleep_hours_value
        return {'changed': True, 'change_text': f'sleep {sleep_hours_value:g} h ({sleep_start_raw}-{sleep_end_raw})'}

    if has_range and not has_hours:
        return {'error': 'You selected sleep hours mode. Fill hours or switch to time range mode.'}
    if not has_hours:
        return {'changed': False}
    parsed_sleep_hours = _parse_float(sleep_raw, default=None)
    if parsed_sleep_hours is None or parsed_sleep_hours < 0 or parsed_sleep_hours > 24:
        return {'error': 'Sleep hours must stay between 0 and 24.'}
    log.sleep_hours = float(parsed_sleep_hours)
    return {'changed': True, 'change_text': f'sleep {log.sleep_hours:g} h'}


@bp.route('/dashboard')
@login_required
def dashboard():
    _ensure_baseline_scores(current_user)

    today = local_today()
    _ensure_daily_default_tasks(current_user.id, today)
    db.session.flush()
    refresh_user_patterns_once_per_day(current_user)
    db.session.commit()

    today_tasks = Task.query.filter_by(user_id=current_user.id, task_date=today).order_by(Task.sort_order.asc(), Task.created_at.asc()).all()
    recent_logs = DailyLog.query.filter_by(user_id=current_user.id).order_by(DailyLog.log_date.desc()).limit(5).all()
    today_focus = _build_focus_payload(current_user.id, today)
    today_log = DailyLog.query.filter_by(user_id=current_user.id, log_date=today).first()

    due_prompt, upcoming_prompt = _get_due_and_upcoming_prompt(current_user.id)
    missed_summary = _missed_hydration_summary(current_user.id)
    care_session = _get_or_create_active_care_chat_session(current_user.id)
    db.session.commit()

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
        morning_hydration_prompt=None,
        morning_prompt_exists=False,
        hydration_missed_summary=missed_summary,
        recent_activity_entries=_recent_activity_preview(current_user.id, 6),
        active_pattern_cards=get_active_pattern_cards(current_user.id),
        pattern_learning=get_pattern_learning_state(current_user.id),
        wellness_metrics=_serialize_wellness(current_user),
        care_session_id=care_session.id,
        care_history_messages=_care_chat_history_payload(care_session.id),
        care_intro_message=(
            "I’m here with you. This is habit support, not medical advice or therapy. Your scores are behavioral estimates, not clinical metrics. Tell me how you feel, and I’ll respond with your current hydration, energy, fitness, focus, mood, and overall wellness in mind."
        ),
        care_boundary_lines=CARE_BOUNDARY_LINES,
    )



@bp.route('/eye-exercise')
@login_required
def eye_exercise_view():
    active_prompt = _get_active_eye_exercise_prompt(current_user.id)
    db.session.commit()
    return render_template(
        'eye_exercise_page.html',
        active_eye_prompt=_serialize_eye_exercise_prompt(active_prompt),
        eye_exercise_threshold=EYE_EXERCISE_THRESHOLD_MINUTES,
        eye_exercise_embed_url='https://www.youtube.com/embed/iVb4vUp70zY',
    )


@bp.route('/eye-exercise/finish', methods=['POST'])
@login_required
def eye_exercise_finish_page():
    now = local_now().replace(tzinfo=None)
    completion = _complete_eye_exercise(current_user, local_today(), now, source_label='manual page')
    payload = completion.get('payload') or {}
    _log_activity_entry(
        current_user.id,
        'eye_exercise',
        'Eye exercise finished',
        completion.get('event_label') or 'Eye exercise finished · manual page',
        event_at=now,
        impacts=payload.get('feedback', {}).get('metrics'),
    )
    db.session.commit()
    flash('Eye exercise saved.', 'success')
    return redirect(url_for('main.eye_exercise_view'))


@bp.route('/break')
@login_required
def break_view():
    reason = (request.args.get('reason') or 'manual').strip().lower() or 'manual'
    if reason not in {'manual', 'fatigue'}:
        reason = 'manual'
    default_exercise_key = _default_break_key(reason)
    suggested_duration_sec = 300 if reason == 'fatigue' else 180
    return render_template(
        'break.html',
        reason=reason,
        exercises=BREAK_EXERCISES,
        default_exercise_key=default_exercise_key,
        suggested_duration_sec=suggested_duration_sec,
        time_of_day_copy=_time_of_day_copy(),
    )


@bp.route('/break/start', methods=['POST'])
@login_required
def break_start():
    data = request.get_json(silent=True) or {}
    trigger = (data.get('trigger') or 'manual').strip().lower()
    if trigger not in {'manual', 'fatigue'}:
        trigger = 'manual'
    row = BreakSession(
        user_id=current_user.id,
        started_at=local_now().replace(tzinfo=None),
        trigger=trigger,
        fatigue_signal_snapshot=_safe_json_dumps(data.get('fatigue_signal_snapshot') or {}),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({'session_id': row.id, 'started_at': row.started_at.isoformat()})


@bp.route('/break/finish', methods=['POST'])
@login_required
def break_finish():
    data = request.get_json(silent=True) or {}
    session_id = _parse_int(data.get('session_id'), default=None, minimum=1, maximum=10_000_000)
    row = BreakSession.query.filter_by(id=session_id, user_id=current_user.id).first() if session_id else None
    if not row:
        return jsonify({'ok': False, 'message': 'Break session not found.'}), 404
    exercises_done = data.get('exercises_done') or []
    if not isinstance(exercises_done, list):
        exercises_done = []
    allowed_keys = set(_break_exercise_map())
    exercises_done = [str(key) for key in exercises_done if str(key) in allowed_keys]
    self_report = (data.get('self_report') or '').strip().lower()
    raw_self_report = self_report
    if self_report not in {'better', 'same', 'still_tired'}:
        current_app.logger.warning("Invalid break self_report for user %s session %s: %r", current_user.id, session_id, raw_self_report)
        self_report = 'same'
    now = local_now().replace(tzinfo=None)
    row.ended_at = now
    row.exercises_done = _safe_json_dumps(exercises_done)
    row.self_report = self_report
    cooldown_minutes = 20 if self_report == 'better' else 8
    cooldown_until = now + timedelta(minutes=cooldown_minutes)
    exercise_titles = [item['title'] for item in BREAK_EXERCISES if item['key'] in exercises_done]
    duration = _break_duration_minutes(row)
    _log_activity_entry(
        current_user.id,
        'break',
        'Break completed',
        f"{duration} min · Feeling: {self_report.replace('_', ' ')} · {', '.join(exercise_titles) if exercise_titles else 'No exercise selected'}",
        event_at=now,
    )
    db.session.commit()
    return jsonify({'ok': True, 'cooldown_until': cooldown_until.isoformat(), 'cooldown_minutes': cooldown_minutes})


@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    _ensure_baseline_scores(current_user)
    refresh_user_patterns_once_per_day(current_user)
    locked = _profile_locked(current_user)

    if request.method == 'POST':
        action = (request.form.get('action') or 'save_profile').strip().lower()

        if action == 'update_hydration_schedule':
            if not locked:
                flash('Save your basic profile first, then set daily water reminder times.', 'warning')
                return redirect(url_for('main.profile'))
            defaults = _default_hydration_schedule_map(current_user)
            for _, label, field_name in HYDRATION_SLOT_META:
                raw_value = (request.form.get(field_name) or '').strip()
                normalized = _parse_clock_text(raw_value or defaults[field_name], defaults[field_name]).strftime('%H:%M')
                setattr(current_user, field_name, normalized)
            _sync_goal_based_hydration_prompts(current_user, local_today())
            db.session.commit()
            flash('Daily water reminder times were updated.', 'success')
            return redirect(url_for('main.profile'))

        if locked:
            flash('Basic information is already locked after the first save.', 'warning')
            return redirect(url_for('main.profile'))

        age = _parse_int(request.form.get('age'), default=None)
        gender_identity = (request.form.get('gender_identity') or 'prefer_not_say').strip().lower()
        valid_gender_values = {'male', 'female', 'non_binary', 'prefer_not_say'}
        if gender_identity not in valid_gender_values:
            gender_identity = 'prefer_not_say'
        weight_kg = _parse_float(request.form.get('weight_kg'), default=None)
        height_cm = _parse_float(request.form.get('height_cm'), default=None)

        if age is None or weight_kg is None or height_cm is None:
            flash('Please fill age, weight, and height once.', 'danger')
            return redirect(url_for('main.profile'))
        if not (5 <= age <= 120):
            flash('Age should be between 5 and 120.', 'danger')
            return redirect(url_for('main.profile'))
        if not (20 <= float(weight_kg) <= 400):
            flash('Weight should be between 20 and 400 kg.', 'danger')
            return redirect(url_for('main.profile'))
        if not (80 <= float(height_cm) <= 260):
            flash('Height should be between 80 and 260 cm.', 'danger')
            return redirect(url_for('main.profile'))

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
        _ensure_hydration_schedule_defaults(current_user)
        _sync_goal_based_hydration_prompts(current_user, local_today())
        db.session.commit()
        flash('Basic information saved. Goals and Schedule are now unlocked. Use the tabs above to switch.', 'success')
        return redirect(url_for('main.profile'))

    latest_log = DailyLog.query.filter_by(user_id=current_user.id).order_by(DailyLog.log_date.desc()).first()
    today_log = DailyLog.query.filter_by(user_id=current_user.id, log_date=local_today()).first()
    if locked:
        _ensure_hydration_schedule_defaults(current_user)
        db.session.commit()
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
        mood_trend=_build_mood_trend_payload(current_user.id, 14),
        hydration_schedule_rows=_hydration_schedule_rows(current_user) if locked else [],
        hydration_goal_plan=_hydration_goal_plan(current_user) if locked else None,
        active_pattern_cards=get_active_pattern_cards(current_user.id, include_suppressed=True) if locked else [],
        past_pattern_cards=get_past_pattern_cards(current_user.id) if locked else [],
        pattern_learning=get_pattern_learning_state(current_user.id) if locked else {'ready': False, 'active_days': 0, 'logged_days': 0, 'min_active_days': 7},
        break_habits=_break_habits_payload(current_user.id) if locked else None,
    )


@bp.route('/api/pomodoro/fatigue', methods=['POST'])
@login_required
def pomodoro_fatigue_signal():
    payload = request.get_json(silent=True) or {}
    result = record_camera_fatigue_signal(current_user, payload)
    db.session.commit()
    return jsonify(result)


@bp.route('/patterns/<int:state_id>/respond', methods=['POST'])
@login_required
def respond_pattern(state_id):
    action = (request.form.get('action') or '').strip().lower()
    rating = _parse_int(request.form.get('rating'), default=None, minimum=1, maximum=10) if request.form.get('rating') else None
    message, category = handle_pattern_response(current_user, state_id, action, rating=rating)
    db.session.commit()
    wants_json = (
        request.headers.get('X-Requested-With') == 'fetch'
        or request.accept_mimetypes.best == 'application/json'
        or 'application/json' in (request.headers.get('Accept') or '')
    )
    if wants_json:
        return jsonify({
            'ok': category in {'success', 'info'},
            'message': message,
            'category': category,
            'action': action,
            'state_id': state_id,
        })
    flash(message, category)
    return redirect(request.referrer or url_for('main.dashboard'))


@bp.route('/history')
@login_required
def history_view():
    page = _parse_int(request.args.get('page'), default=1, minimum=1, maximum=100000)
    pagination = ActivityEntry.query.filter_by(user_id=current_user.id).order_by(ActivityEntry.event_at.desc()).paginate(page=page, per_page=HISTORY_PAGE_SIZE, error_out=False)
    entries = [_activity_entry_view_model(entry) for entry in pagination.items]
    return render_template('history.html', entries=entries, pagination=pagination, page=page)


@bp.route('/activity/update', methods=['POST'])
@login_required
def update_activity():
    activity_text = _clean_text(request.form.get('activity_text'), 500)
    if not activity_text:
        flash('Please write what you just did first.', 'danger')
        return redirect(url_for('main.dashboard'))

    raw_activity_time = (request.form.get('activity_time') or '').strip()
    chosen_time = _parse_time(raw_activity_time)
    if raw_activity_time and not chosen_time:
        flash('Please enter a valid activity time.', 'warning')
        return redirect(url_for('main.dashboard'))
    chosen_time = chosen_time or local_now().time().replace(second=0, microsecond=0)
    chosen_dt = datetime.combine(local_today(), chosen_time)
    log = _get_or_create_log_for_today(current_user.id)
    timestamp = chosen_time.strftime('%H:%M')
    entry = f'[{timestamp}] {activity_text}'
    existing = (log.activity_text or '').strip()
    log.activity_text = entry if not existing else f'{entry}\n{existing}'

    _add_calendar_event(current_user.id, f'What just did: {activity_text[:80]}', log.log_date, chosen_time, activity_text)
    existing_auto_task = Task.query.filter_by(
        user_id=current_user.id,
        title=activity_text[:200],
        task_date=log.log_date,
        completed=True,
    ).filter(Task.description.like('Auto-added from What just did%')).order_by(Task.id.desc()).first()
    if not existing_auto_task or not existing_auto_task.completed_at or existing_auto_task.completed_at.strftime('%H:%M') != chosen_time.strftime('%H:%M'):
        auto_task = Task(
            user_id=current_user.id,
            title=activity_text[:200],
            description=f'Auto-added from What just did · {timestamp}',
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
    return redirect(url_for('main.dashboard'))


@bp.route('/logs', methods=['GET', 'POST'])
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
            water_error = _increment_water_if_within_limit(log, added_water)
            if water_error:
                flash(water_error, 'warning')
                return redirect(url_for('main.logs', date=selected_date.isoformat()))
            changes.append(f'water +{added_water} ml')

        sleep_update = _apply_sleep_submission(log, request.form, selected_date)
        if sleep_update.get('error'):
            flash(sleep_update['error'], 'warning')
            return redirect(url_for('main.logs', date=selected_date.isoformat()))
        if sleep_update.get('changed'):
            changes.append(sleep_update['change_text'])

        steps_raw = (request.form.get('steps') or '').strip()
        if steps_raw:
            parsed_steps = _parse_int(steps_raw, default=None)
            if parsed_steps is None or parsed_steps < 0 or parsed_steps > 200000:
                flash('Steps must be a number between 0 and 200000.', 'warning')
                return redirect(url_for('main.logs', date=selected_date.isoformat()))
            log.steps = parsed_steps
            changes.append(f'steps {log.steps}')

        exercise_name = (request.form.get('exercise_name') or '').strip()
        exercise_raw = (request.form.get('exercise_minutes') or '').strip()
        exercise_reps_raw = (request.form.get('exercise_reps') or '').strip()
        if exercise_name or exercise_raw or exercise_reps_raw:
            if not exercise_name:
                flash('Please write what exercise you did.', 'warning')
                return redirect(url_for('main.logs', date=selected_date.isoformat()))
            if not exercise_raw and not exercise_reps_raw:
                flash('For exercise, fill reps or minutes.', 'warning')
                return redirect(url_for('main.logs', date=selected_date.isoformat()))

            exercise_minutes = _parse_int(exercise_raw, default=None) if exercise_raw else 0
            exercise_reps = _parse_int(exercise_reps_raw, default=None) if exercise_reps_raw else 0
            if exercise_minutes is None or exercise_reps is None:
                flash('Exercise minutes and reps must be valid numbers.', 'warning')
                return redirect(url_for('main.logs', date=selected_date.isoformat()))
            if exercise_minutes < 0 or exercise_minutes > 1440 or exercise_reps < 0 or exercise_reps > 100000:
                flash('Exercise minutes or reps are outside a reasonable range.', 'warning')
                return redirect(url_for('main.logs', date=selected_date.isoformat()))
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

        journal_text = _clean_text(request.form.get('journal_text'), 5000)
        if journal_text:
            log.journal_text = journal_text
            changes.append('journal updated')

        mood_emoji_raw = _clean_text(request.form.get('journal_mood_emoji'), 16)
        mood_custom_text = _clean_text(request.form.get('mood_custom_text'), 120)
        mood_selected = bool(mood_emoji_raw)
        mood_entry_summary = None
        mood_detected_by = 'user'
        if mood_selected:
            allowed_moods = {item['label'] for item in MOOD_CHIP_CHOICES}
            if mood_emoji_raw not in allowed_moods:
                flash('Please choose one of the available mood chips.', 'warning')
                return redirect(url_for('main.logs', date=selected_date.isoformat()))
            if mood_emoji_raw == 'custom':
                if not mood_custom_text:
                    flash('Please write your custom mood before saving.', 'warning')
                    return redirect(url_for('main.logs', date=selected_date.isoformat()))
                mood_choice, custom_choice = _normalize_mood_choice('custom', mood_custom_text)
            else:
                mood_choice, custom_choice = _normalize_mood_choice(mood_emoji_raw, '')
            mood_badge = _mood_badge_payload(mood_choice, custom_choice)
            mood_entry_summary = f"Mood set to {mood_badge['display']}."
            log.mood_label = mood_choice
            log.mood_custom_text = custom_choice
            changes.append(f"mood {mood_badge['display']}")

        if not changes:
            flash('Fill at least one field to update the daily log.', 'warning')
            return redirect(url_for('main.logs', date=selected_date.isoformat()))

        if mood_selected:
            _record_mood_entry(
                current_user.id,
                'journal',
                log.mood_label,
                (log.mood_custom_text if log.mood_label == 'custom' else mood_emoji_raw),
                summary=mood_entry_summary or 'Journal mood updated.',
                log=log,
                event_at=datetime.combine(selected_date, datetime.min.time()),
                detected_by=mood_detected_by,
            )

        candidate_text = ' '.join(part for part in [log.activity_text or '', log.journal_text or '', log.mood_custom_text or '', log.mood_label or ''] if part).strip()
        db.session.flush()
        meal_detected = _update_log_meal_insight(current_user.id, log, candidate_text)
        latest_event = 'Daily log updated: ' + ', '.join(changes)
        db.session.flush()
        payload = _apply_wellness_update(current_user, selected_date, latest_event)
        db.session.flush()
        ai_result = _maybe_create_ai_suggestion_task(
            current_user,
            candidate_text or latest_event,
            detected_mood=log.mood_label,
            target_date=local_today(),
            source_label='journal',
        )
        ai_task = ai_result.get('task')
        if ai_task:
            payload['feedback'] = _decorate_feedback_with_ai_task(payload.get('feedback'), ai_task, status=ai_result.get('status') or 'added')
            _store_wellness_feedback(payload.get('feedback'))
        _log_activity_entry(current_user.id, 'daily_log', 'Daily log updated', latest_event, impacts=payload.get('feedback', {}).get('metrics'))
        db.session.commit()

        flash('Daily log updated.', 'success')
        return redirect(url_for('main.logs', date=selected_date.isoformat()))

    current_log = DailyLog.query.filter_by(user_id=current_user.id, log_date=selected_date).first()
    all_logs = DailyLog.query.filter_by(user_id=current_user.id).order_by(DailyLog.log_date.desc()).all()
    due_prompt, upcoming_prompt = _get_due_and_upcoming_prompt(current_user.id)
    missed_summary = _missed_hydration_summary(current_user.id)
    db.session.commit()
    return render_template(
        'logs.html',
        current_log=current_log,
        selected_date=selected_date,
        all_logs=all_logs,
        due_hydration_prompt=_serialize_prompt(due_prompt),
        upcoming_hydration_prompt=_serialize_prompt(upcoming_prompt),
        morning_hydration_prompt=None,
        morning_prompt_exists=False,
        hydration_missed_summary=missed_summary,
        current_snapshot=_progress_snapshot(current_user, current_log),
        mood_options=MOOD_OPTIONS,
        mood_chip_choices=MOOD_CHIP_CHOICES,
        selected_mood_emoji=_selected_mood_emoji(getattr(current_log, 'mood_label', None)),
    )


@bp.route('/calendar')
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
    month_breaks = BreakSession.query.filter(
        BreakSession.user_id == current_user.id,
        BreakSession.started_at >= datetime.combine(month_start, datetime.min.time()),
        BreakSession.started_at < datetime.combine(next_month_boundary, datetime.min.time()),
    ).all()
    break_dates = {row.started_at.date() for row in month_breaks if row.started_at}

    selected_tasks = Task.query.filter_by(user_id=current_user.id, task_date=selected_date, completed=True).order_by(Task.sort_order.asc(), Task.created_at.asc()).all()
    selected_events = sorted(
        CalendarEvent.query.filter_by(user_id=current_user.id, event_date=selected_date).all(),
        key=_event_sort_key,
    )
    selected_finished_items = _selected_day_finished_items(selected_events, selected_tasks)
    selected_log = DailyLog.query.filter_by(user_id=current_user.id, log_date=selected_date).first()

    weeks = _month_grid(year, month, month_tasks, month_events, month_logs, current_user, selected_date=selected_date)
    for week in weeks:
        for day in week:
            day['has_break'] = day['date'] in break_dates
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


