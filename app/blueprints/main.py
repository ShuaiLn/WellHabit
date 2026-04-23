from datetime import date, datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import db
from ..ai_services import convert_drink_amount_to_ml, suggest_personal_goals
from ..constants import HISTORY_PAGE_SIZE
from ..models import ActivityEntry, CalendarEvent, DailyLog, Task
from ..services._legacy_support import _parse_clock_text, _parse_float
from ..services.activity import _activity_entry_view_model, _add_calendar_event, _event_sort_key, _log_activity_entry, _recent_activity_preview
from ..services.ai_suggestions import _maybe_create_ai_suggestion_task
from ..services.hydration import (
    HYDRATION_SLOT_META,
    _default_hydration_schedule_map,
    _ensure_hydration_schedule_defaults,
    _get_due_and_upcoming_prompt,
    _get_or_create_log_for_date,
    _get_or_create_log_for_today,
    _hydration_goal_plan,
    _hydration_schedule_rows,
    _missed_hydration_summary,
    _serialize_prompt,
    _sync_goal_based_hydration_prompts,
    _sync_meal_task_completion,
    _update_log_meal_insight,
    _water_limit_error,
)
from ..services.tasks import _ensure_daily_default_tasks, _get_next_sort_order
from ..services.wellness import (
    MOOD_CHIP_CHOICES,
    GOAL_INTENSITY_CHOICES,
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
    db.session.commit()

    today_tasks = Task.query.filter_by(user_id=current_user.id, task_date=today).order_by(Task.sort_order.asc(), Task.created_at.asc()).all()
    recent_logs = DailyLog.query.filter_by(user_id=current_user.id).order_by(DailyLog.log_date.desc()).limit(5).all()
    today_focus = _build_focus_payload(current_user.id, today)
    today_log = DailyLog.query.filter_by(user_id=current_user.id, log_date=today).first()

    due_prompt, upcoming_prompt = _get_due_and_upcoming_prompt(current_user.id)
    missed_summary = _missed_hydration_summary(current_user.id)
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
    )


@bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    _ensure_baseline_scores(current_user)
    locked = _profile_locked(current_user)

    if request.method == 'POST':
        action = (request.form.get('action') or 'save_profile').strip().lower()

        if action == 'update_goal_intensity':
            intensity = (request.form.get('goal_progress_intensity') or 'medium').strip().lower()
            if intensity not in {'easy', 'medium', 'hard'}:
                flash('Please choose easy, medium, or hard.', 'danger')
                return redirect(url_for('main.profile'))
            current_user.goal_progress_intensity = intensity
            db.session.commit()
            flash('Goal progress intensity was updated.', 'success')
            return redirect(url_for('main.profile'))

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
        goal_intensity_choices=GOAL_INTENSITY_CHOICES,
        hydration_schedule_rows=_hydration_schedule_rows(current_user) if locked else [],
        hydration_goal_plan=_hydration_goal_plan(current_user) if locked else None,
    )


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
            water_error = _water_limit_error(log.water_ml, added_water)
            if water_error:
                flash(water_error, 'warning')
                return redirect(url_for('main.logs', date=selected_date.isoformat()))
            log.water_ml = int(log.water_ml or 0) + added_water
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


