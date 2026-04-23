from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from .. import db
from ..ai_services import convert_drink_amount_to_ml
from ..constants import (
    DEFAULT_POMODORO_ACTIVITY_LABEL,
    DEFAULT_POMODORO_BREAK_MINUTES,
    DEFAULT_POMODORO_FOCUS_MINUTES,
    GLASS_VOLUME_ML,
    POMODORO_STATE_CLIENT_KEY,
)
from ..models import CalendarEvent, EyeExercisePrompt, PomodoroSession, Task
from ..services.activity import _add_calendar_event, _log_activity_entry
from ..services.ai_suggestions import _queue_ai_suggestion_followup
from ..services.client_state import _peek_client_state_for_user, _store_client_state
from ..services.eye_exercise import (
    _complete_eye_exercise,
    _get_active_eye_exercise_prompt,
    _get_or_create_eye_exercise_state,
    _queue_eye_exercise_prompt,
    _serialize_eye_exercise_prompt,
)
from ..services.hydration import (
    _get_or_create_log_for_date,
    _infer_beverage_from_text,
    _serialize_prompt,
    _sync_goal_based_hydration_prompts,
    _water_limit_error,
)
from ..services.tasks import (
    _get_next_sort_order,
    _task_ai_suggestion_key,
    _task_is_ai_suggestion,
    _task_is_eye_exercise,
    _task_is_hydration,
    _task_is_meal,
    _task_type,
)
from ..services.wellness import _apply_wellness_update, _consume_wellness_feedback, _serialize_wellness
from ..utils.text import _clean_text
from ..utils.timez import _parse_date, _parse_time, local_now, local_today
from ..services._legacy_support import _parse_int

bp = Blueprint('tasks', __name__)



def _sanitize_pomodoro_state_payload(data):
    payload = data or {}
    if payload.get('clear'):
        return None
    mode = 'break' if str(payload.get('mode') or '').strip().lower() == 'break' else 'focus'
    focus_minutes = _parse_int(payload.get('focusMinutes'), default=DEFAULT_POMODORO_FOCUS_MINUTES, minimum=1, maximum=180)
    break_minutes = _parse_int(payload.get('breakMinutes'), default=DEFAULT_POMODORO_BREAK_MINUTES, minimum=1, maximum=60)
    cycle_number = _parse_int(payload.get('cycleNumber'), default=1, minimum=1, maximum=1000)
    remaining_seconds = _parse_int(payload.get('remainingSeconds'), default=(focus_minutes if mode == 'focus' else break_minutes) * 60, minimum=0, maximum=60 * 60 * 6)
    updated_at_ms = _parse_int(payload.get('updatedAtMs'), default=int(local_now().timestamp() * 1000), minimum=0, maximum=9_999_999_999_999)
    end_at_ms = _parse_int(payload.get('endAtMs'), default=None, minimum=0, maximum=9_999_999_999_999) if payload.get('endAtMs') not in (None, '') else None
    is_running = bool(payload.get('isRunning') and end_at_ms)
    activity_label = _clean_text(payload.get('activityLabel') or DEFAULT_POMODORO_ACTIVITY_LABEL, 200) or DEFAULT_POMODORO_ACTIVITY_LABEL
    session_key = _clean_text(payload.get('sessionKey') or '', 80) or None
    last_message = _clean_text(payload.get('lastMessage') or 'Timer state synced.', 240) or 'Timer state synced.'
    return {
        'focusMinutes': focus_minutes,
        'breakMinutes': break_minutes,
        'activityLabel': activity_label,
        'mode': mode,
        'cycleNumber': cycle_number,
        'remainingSeconds': remaining_seconds,
        'isRunning': is_running,
        'endAtMs': end_at_ms if is_running else None,
        'sessionKey': session_key,
        'lastMessage': last_message,
        'updatedAtMs': updated_at_ms,
    }


def _pomodoro_state_needs_storage(state):
    if not state:
        return False
    default_seconds = int(state.get('focusMinutes') or DEFAULT_POMODORO_FOCUS_MINUTES) * 60
    return bool(
        state.get('isRunning')
        or str(state.get('mode') or 'focus') != 'focus'
        or int(state.get('cycleNumber') or 1) != 1
        or int(state.get('remainingSeconds') or default_seconds) != default_seconds
        or (state.get('activityLabel') or DEFAULT_POMODORO_ACTIVITY_LABEL) != DEFAULT_POMODORO_ACTIVITY_LABEL
    )


@bp.route('/tasks/add', methods=['POST'])
@login_required
def add_task():
    title = _clean_text(request.form.get('title'), 200)
    description = _clean_text(request.form.get('description'), 1000)
    task_date = _parse_date(request.form.get('task_date'))

    if not title:
        flash('Task title is required.', 'danger')
        return redirect(request.referrer or url_for('main.calendar_view'))

    existing_task = Task.query.filter_by(user_id=current_user.id, task_date=task_date, title=title, completed=False).first()
    if existing_task:
        flash('That task is already in the list for this day.', 'warning')
        return redirect(request.referrer or url_for('main.calendar_view', year=task_date.year, month=task_date.month, selected_date=task_date.isoformat()))

    task = Task(
        user_id=current_user.id,
        title=title,
        description=description or None,
        task_type='regular',
        task_date=task_date,
        sort_order=_get_next_sort_order(current_user.id, task_date),
    )
    db.session.add(task)
    _log_activity_entry(current_user.id, 'task', 'Task added', f'{title} ({task_date.isoformat()})')
    db.session.commit()
    flash('Task added successfully.', 'success')
    return redirect(request.referrer or url_for('main.calendar_view', year=task_date.year, month=task_date.month, selected_date=task_date.isoformat()))


@bp.route('/tasks/<int:task_id>/toggle', methods=['POST'])
@login_required
def toggle_task(task_id):
    task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
    was_completed = bool(task.completed)
    previous_completed_at = task.completed_at
    task.completed = not task.completed
    task.completed_at = local_now().replace(tzinfo=None) if task.completed else None
    event_label = f'Completed todo: {task.title}' if task.completed else f'Reopened todo: {task.title}'
    payload = None

    if _task_is_ai_suggestion(task):
        if not task.ai_suggestion_key:
            task.ai_suggestion_key = _task_ai_suggestion_key(task)
        log = _get_or_create_log_for_date(current_user.id, task.task_date)
        if task.completed:
            task.ai_followup_rating = None
            task.ai_followup_completed_at = None
            if task.task_date == local_today() and _task_is_hydration(task):
                amount_ml = int(convert_drink_amount_to_ml(_infer_beverage_from_text(task.title), task.title).get('amount_ml', GLASS_VOLUME_ML) or GLASS_VOLUME_ML)
                if not int(task.auto_tracked_water_ml or 0):
                    water_error = _water_limit_error(log.water_ml, amount_ml)
                    if water_error:
                        db.session.rollback()
                        flash(water_error, 'warning')
                        return redirect(request.referrer or url_for('main.calendar_view'))
                    log.water_ml = int(log.water_ml or 0) + amount_ml
                    task.auto_tracked_water_ml = amount_ml
                else:
                    amount_ml = int(task.auto_tracked_water_ml or amount_ml)
                event_label = f'Completed AI suggestion: {task.title} · counted {amount_ml} ml'
                db.session.flush()
                payload = _apply_wellness_update(current_user, task.task_date, f'Completed AI suggestion todo: {task.title} and drank {amount_ml} ml')
            elif task.task_date == local_today():
                event_label = f'Completed AI suggestion: {task.title}'
                db.session.flush()
                payload = _apply_wellness_update(current_user, task.task_date, f'Completed AI suggestion todo: {task.title}')
            db.session.flush()
            _queue_ai_suggestion_followup(task)
        else:
            task.ai_followup_rating = None
            task.ai_followup_completed_at = None
            if was_completed and int(task.auto_tracked_water_ml or 0):
                removed_ml = int(task.auto_tracked_water_ml or 0)
                log.water_ml = max(0, int(log.water_ml or 0) - removed_ml)
                task.auto_tracked_water_ml = 0
                event_label = f'Reopened AI suggestion: {task.title} · removed {removed_ml} ml'
                db.session.flush()
                payload = _apply_wellness_update(current_user, task.task_date, f'Reopened AI suggestion todo: {task.title} and removed {removed_ml} ml')
            else:
                event_label = f'Reopened AI suggestion: {task.title}'
    elif _task_is_hydration(task):
        log = _get_or_create_log_for_date(current_user.id, task.task_date)
        if task.completed and task.task_date == local_today():
            amount_ml = int(convert_drink_amount_to_ml(_infer_beverage_from_text(task.title), task.title).get('amount_ml', GLASS_VOLUME_ML) or GLASS_VOLUME_ML)
            if not int(task.auto_tracked_water_ml or 0):
                water_error = _water_limit_error(log.water_ml, amount_ml)
                if water_error:
                    db.session.rollback()
                    flash(water_error, 'warning')
                    return redirect(request.referrer or url_for('main.calendar_view'))
                log.water_ml = int(log.water_ml or 0) + amount_ml
                task.auto_tracked_water_ml = amount_ml
            else:
                amount_ml = int(task.auto_tracked_water_ml or amount_ml)
            event_label = f'Completed todo: {task.title} · counted {amount_ml} ml'
            db.session.flush()
            payload = _apply_wellness_update(current_user, task.task_date, f'Drank {amount_ml} ml of {_infer_beverage_from_text(task.title)} from completed todo: {task.title}')
        elif was_completed and not task.completed and int(task.auto_tracked_water_ml or 0):
            removed_ml = int(task.auto_tracked_water_ml or 0)
            log.water_ml = max(0, int(log.water_ml or 0) - removed_ml)
            task.auto_tracked_water_ml = 0
            event_label = f'Reopened todo: {task.title} · removed {removed_ml} ml'
            db.session.flush()
            payload = _apply_wellness_update(current_user, task.task_date, f'Removed {removed_ml} ml from reopened hydration todo: {task.title}')
    elif _task_is_eye_exercise(task):
        if task.completed:
            completion = _complete_eye_exercise(current_user, task.task_date, task.completed_at, source_label='todo')
            payload = completion.get('payload')
            event_label = completion.get('event_label') or event_label
        else:
            state = _get_or_create_eye_exercise_state(current_user.id)
            if state.active_prompt_id:
                prompt = EyeExercisePrompt.query.filter_by(id=state.active_prompt_id, user_id=current_user.id).first()
                if prompt and prompt.response_status == 'finished':
                    prompt.response_status = 'not_yet'
                    prompt.responded_at = local_now().replace(tzinfo=None)
                    state.active_prompt_id = prompt.id
                    state.updated_at = local_now().replace(tzinfo=None)
    elif task.task_date == local_today() and task.completed and _task_type(task) == 'regular':
        db.session.flush()
        payload = _apply_wellness_update(current_user, task.task_date, event_label)

    _log_activity_entry(current_user.id, 'task', 'Task status changed', event_label, impacts=payload.get('feedback', {}).get('metrics') if payload else None)
    db.session.commit()
    flash('Task updated.', 'success')
    return redirect(request.referrer or url_for('main.calendar_view'))


@bp.route('/tasks/<int:task_id>/edit', methods=['POST'])
@login_required
def edit_task(task_id):
    task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
    if _task_is_meal(task):
        return jsonify({'message': 'Meal tasks are updated from the meal popup.'}), 400
    data = request.get_json(silent=True) or request.form
    new_title = _clean_text(data.get('title'), 200)
    if not new_title:
        return jsonify({'message': 'Task text cannot be empty.'}), 400

    task.title = new_title[:200]
    _log_activity_entry(current_user.id, 'task', 'Task edited', task.title)
    db.session.commit()
    return jsonify({'message': 'Task updated.', 'title': task.title, 'wellness_feedback': None})


@bp.route('/tasks/<int:task_id>/ai-followup', methods=['POST'])
@login_required
def save_ai_suggestion_followup(task_id):
    task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
    if not _task_is_ai_suggestion(task):
        return jsonify({'message': 'This is not an AI suggestion task.'}), 400

    data = request.get_json(silent=True) or request.form
    rating = _parse_int(data.get('rating'), default=0)
    if rating < 1 or rating > 10:
        return jsonify({'message': 'Choose a number from 1 to 10.'}), 400

    if not task.ai_suggestion_key:
        task.ai_suggestion_key = _task_ai_suggestion_key(task)
    task.ai_followup_rating = rating
    task.ai_followup_completed_at = local_now().replace(tzinfo=None)
    latest_event = f'AI suggestion follow-up after negativity detected: felt better {rating}/10 after {task.title}'
    payload = _apply_wellness_update(current_user, local_today(), latest_event)
    _log_activity_entry(
        current_user.id,
        'care',
        'AI suggestion follow-up saved',
        f'{task.title} · feel better {rating}/10 after negativity detected',
        impacts=payload.get('feedback', {}).get('metrics') if payload else None,
    )
    db.session.commit()
    return jsonify({
        'message': 'Follow-up saved.',
        'rating': rating,
        'wellness_feedback': payload.get('feedback') if payload else None,
    })


@bp.route('/tasks/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
    task_date = task.task_date
    task_title = task.title
    if _task_is_eye_exercise(task):
        state = _get_or_create_eye_exercise_state(current_user.id)
        prompt = _get_active_eye_exercise_prompt(current_user.id)
        if prompt:
            prompt.response_status = 'dismissed'
            prompt.responded_at = local_now().replace(tzinfo=None)
        state.active_prompt_id = None
        state.updated_at = local_now().replace(tzinfo=None)
    db.session.delete(task)
    _log_activity_entry(current_user.id, 'task', 'Task deleted', task_title)
    db.session.commit()
    flash('Task deleted.', 'info')
    return redirect(request.referrer or url_for('main.calendar_view'))


@bp.route('/tasks/<int:task_id>/meal', methods=['POST'])
@login_required
def update_meal_task(task_id):
    task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
    if not _task_is_meal(task):
        return jsonify({'message': 'This is not a meal task.'}), 400

    data = request.get_json(silent=True) or request.form
    meal_status = (data.get('meal_status') or 'finished').strip().lower()
    meal_text = _clean_text(data.get('meal_text'), 300)
    raw_meal_time = (data.get('meal_time') or '').strip()
    meal_time = _parse_time(raw_meal_time)
    if raw_meal_time and not meal_time:
        return jsonify({'message': 'Please enter a valid meal time.'}), 400
    meal_time = meal_time or local_now().time().replace(second=0, microsecond=0)
    chosen_dt = datetime.combine(task.task_date, meal_time)

    if meal_status not in {'finished', 'skipped'}:
        return jsonify({'message': 'Choose finished or skipped.'}), 400

    task.completed = True
    task.completed_at = chosen_dt
    if meal_status == 'skipped':
        task.description = f'Skipped · {meal_time.strftime("%H:%M")}'
        task.auto_tracked_water_ml = 0
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
        hydration_prompt = None

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


@bp.route('/tasks/reorder', methods=['POST'])
@login_required
def reorder_tasks():
    data = request.get_json(silent=True) or {}
    raw_task_ids = data.get('task_ids') or []
    task_ids = []
    for raw_id in raw_task_ids:
        try:
            task_ids.append(int(raw_id))
        except (TypeError, ValueError):
            return jsonify({'message': 'Task list contains an invalid task id.'}), 400
    if len(set(task_ids)) != len(task_ids):
        return jsonify({'message': 'Task list contains duplicate ids.'}), 400
    task_date = _parse_date(data.get('task_date'), fallback=local_today())

    tasks = Task.query.filter(Task.user_id == current_user.id, Task.task_date == task_date, Task.id.in_(task_ids)).all()
    if len(tasks) != len(task_ids):
        return jsonify({'message': 'Task list mismatch.'}), 400

    task_lookup = {task.id: task for task in tasks}
    for index, task_id in enumerate(task_ids, start=1):
        task_lookup[int(task_id)].sort_order = index

    db.session.commit()
    return jsonify({'message': 'Task order updated.'})


@bp.route('/calendar/events/add', methods=['POST'])
@login_required
def add_event():
    title = _clean_text(request.form.get('title'), 200)
    description = _clean_text(request.form.get('description'), 2000)
    raw_event_date = (request.form.get('event_date') or '').strip()
    event_date = _parse_date(raw_event_date, fallback=local_today())
    raw_event_time = (request.form.get('event_time') or '').strip()
    event_time = _parse_time(raw_event_time)

    if raw_event_date and event_date.isoformat() != raw_event_date:
        flash('Please enter a valid event date.', 'danger')
        return redirect(url_for('main.calendar_view', year=local_today().year, month=local_today().month, selected_date=local_today().isoformat()))
    if raw_event_time and not event_time:
        flash('Please enter a valid event time.', 'danger')
        return redirect(url_for('main.calendar_view', year=event_date.year, month=event_date.month, selected_date=event_date.isoformat()))
    if not title:
        flash('Event title is required.', 'danger')
        return redirect(url_for('main.calendar_view', year=event_date.year, month=event_date.month, selected_date=event_date.isoformat()))

    _add_calendar_event(current_user.id, title, event_date, event_time, description)
    _log_activity_entry(current_user.id, 'calendar', 'Calendar event added', title)
    db.session.commit()
    flash('Event added successfully.', 'success')
    return redirect(url_for('main.calendar_view', year=event_date.year, month=event_date.month, selected_date=event_date.isoformat()))


@bp.route('/calendar/events/<int:event_id>/delete', methods=['POST'])
@login_required
def delete_event(event_id):
    event = CalendarEvent.query.filter_by(id=event_id, user_id=current_user.id).first_or_404()
    event_date = event.event_date
    event_title = event.title
    db.session.delete(event)
    _log_activity_entry(current_user.id, 'calendar', 'Calendar event deleted', event_title)
    db.session.commit()
    flash('Event deleted.', 'info')
    return redirect(url_for('main.calendar_view', year=event_date.year, month=event_date.month, selected_date=event_date.isoformat()))


@bp.route('/pomodoro/save', methods=['POST'])
@login_required
def save_pomodoro():
    data = request.get_json(silent=True) or {}
    focus_minutes = _parse_int(data.get('focus_minutes'), default=DEFAULT_POMODORO_FOCUS_MINUTES, minimum=1, maximum=180)
    break_minutes = _parse_int(data.get('break_minutes'), default=5, minimum=1, maximum=60)
    cycle_number = _parse_int(data.get('cycle_number'), default=1, minimum=1, maximum=50)
    activity_label = _clean_text(data.get('activity_label') or 'work', 200) or 'work'
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
    eye_prompt = _queue_eye_exercise_prompt(current_user.id, focus_minutes, completed_at=completed_at)
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
            'avatar_emoji': current_user.avatar_emoji or '🙂',
            'eye_prompt': _serialize_eye_exercise_prompt(eye_prompt),
        }
    )




@bp.route('/pomodoro/state', methods=['GET', 'POST'])
@login_required
def pomodoro_state():
    if request.method == 'GET':
        payload = _peek_client_state_for_user(current_user.id, POMODORO_STATE_CLIENT_KEY)
        return jsonify({'state': payload})

    data = request.get_json(silent=True) or {}
    state = _sanitize_pomodoro_state_payload(data)
    if state and _pomodoro_state_needs_storage(state):
        _store_client_state(current_user.id, POMODORO_STATE_CLIENT_KEY, state)
    else:
        _store_client_state(current_user.id, POMODORO_STATE_CLIENT_KEY, None)
        state = None
    db.session.commit()
    return jsonify({'message': 'Pomodoro state synced.', 'state': state})
