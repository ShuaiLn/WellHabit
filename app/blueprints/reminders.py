from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from .. import db
from ..ai_services import convert_drink_amount_to_ml
from ..constants import EYE_EXERCISE_THRESHOLD_MINUTES
from ..models import EyeExercisePrompt, HydrationPrompt, Task
from ..services.activity import _log_activity_entry
from ..services.eye_exercise import (
    _complete_eye_exercise,
    _dismiss_eye_exercise_task,
    _ensure_eye_exercise_task,
    _get_active_eye_exercise_prompt,
    _get_or_create_eye_exercise_state,
    _serialize_eye_exercise_prompt,
)
from ..services.hydration import (
    _get_due_and_upcoming_prompt,
    _get_or_create_log_for_today,
    _hydration_prompt_label,
    _missed_hydration_summary,
    _normalize_beverage,
    _serialize_prompt,
    _sleep_reminder_payload,
    _sync_goal_based_hydration_prompts,
    _water_limit_error,
)
from ..services.tasks import _get_next_sort_order
from ..services.wellness import _apply_wellness_update, _serialize_wellness
from ..utils.text import _clean_text
from ..utils.timez import local_now, local_today

bp = Blueprint('reminders', __name__)


@bp.route('/eye-exercise/status')
@login_required
def eye_exercise_status():
    prompt = _get_active_eye_exercise_prompt(current_user.id)
    if prompt and prompt.response_status == 'not_yet':
        prompt = None
    db.session.commit()
    return jsonify({'eye_prompt': _serialize_eye_exercise_prompt(prompt), 'avatar_emoji': current_user.avatar_emoji or '🙂'})


@bp.route('/eye-exercise/respond', methods=['POST'])
@login_required
def eye_exercise_respond():
    data = request.get_json(silent=True) or request.form
    prompt_id = data.get('prompt_id')
    action = _clean_text(data.get('action') or data.get('response_status') or '', 20).lower()

    prompt = None
    if prompt_id:
        try:
            prompt = EyeExercisePrompt.query.filter_by(id=int(prompt_id), user_id=current_user.id).first()
        except (TypeError, ValueError):
            prompt = None
    if not prompt:
        prompt = _get_active_eye_exercise_prompt(current_user.id)

    if not prompt:
        return jsonify({'message': 'Eye exercise prompt not found.'}), 404

    if action not in {'yes', 'start', 'watch', 'finished', 'done', 'not_yet', 'no_thanks', 'dismissed'}:
        return jsonify({'message': 'Unknown eye exercise action.'}), 400

    state = _get_or_create_eye_exercise_state(current_user.id)
    now = local_now().replace(tzinfo=None)

    if action in {'yes', 'start', 'watch'}:
        prompt.response_status = 'watching'
        prompt.responded_at = now
        state.active_prompt_id = prompt.id
        state.updated_at = now
        db.session.commit()
        return jsonify({
            'message': 'Starting the eye exercise video.',
            'eye_prompt': _serialize_eye_exercise_prompt(prompt),
            'show_video': True,
        })

    if action in {'finished', 'done'}:
        completion = _complete_eye_exercise(current_user, local_today(), now, source_label='video')
        payload = completion.get('payload') or {}
        _log_activity_entry(
            current_user.id,
            'eye_exercise',
            'Eye exercise finished',
            completion.get('event_label') or 'Eye exercise finished',
            event_at=now,
            impacts=payload.get('feedback', {}).get('metrics'),
        )
        db.session.commit()
        return jsonify({
            'message': 'Eye exercise saved.',
            'eye_prompt': None,
            'wellness_scores': _serialize_wellness(current_user),
            'wellness_summary': payload.get('summary'),
            'wellness_feedback': payload.get('feedback'),
            'avatar_emoji': current_user.avatar_emoji or '🙂',
            'refresh_dashboard': True,
        })

    if action == 'not_yet':
        prompt.response_status = 'not_yet'
        prompt.responded_at = now
        state.active_prompt_id = prompt.id
        state.updated_at = now
        task = _ensure_eye_exercise_task(current_user.id, local_today(), int(prompt.focus_minutes_trigger or EYE_EXERCISE_THRESHOLD_MINUTES))
        payload = _apply_wellness_update(current_user, local_today(), 'Eye exercise reminder postponed')
        _log_activity_entry(
            current_user.id,
            'eye_exercise',
            'Eye exercise postponed',
            f'Added todo: {task.title} after {int(prompt.focus_minutes_trigger or EYE_EXERCISE_THRESHOLD_MINUTES)} min focus',
            event_at=now,
            impacts=payload.get('feedback', {}).get('metrics'),
        )
        db.session.commit()
        return jsonify({
            'message': "Okay. I added an eye exercise task to today's todo list.",
            'eye_prompt': None,
            'wellness_scores': _serialize_wellness(current_user),
            'wellness_summary': payload.get('summary'),
            'wellness_feedback': payload.get('feedback'),
            'avatar_emoji': current_user.avatar_emoji or '🙂',
            'refresh_dashboard': True,
        })

    prompt.response_status = 'dismissed'
    prompt.responded_at = now
    state.active_prompt_id = None
    state.updated_at = now
    _dismiss_eye_exercise_task(current_user.id, local_today())
    payload = _apply_wellness_update(current_user, local_today(), 'Eye exercise reminder dismissed')
    _log_activity_entry(
        current_user.id,
        'eye_exercise',
        'Eye exercise dismissed',
        f'Dismissed after {int(prompt.focus_minutes_trigger or EYE_EXERCISE_THRESHOLD_MINUTES)} min focus',
        event_at=now,
        impacts=payload.get('feedback', {}).get('metrics'),
    )
    db.session.commit()
    return jsonify({
        'message': 'No problem. The eye exercise reminder was skipped.',
        'eye_prompt': None,
        'wellness_scores': _serialize_wellness(current_user),
        'wellness_summary': payload.get('summary'),
        'wellness_feedback': payload.get('feedback'),
        'avatar_emoji': current_user.avatar_emoji or '🙂',
        'refresh_dashboard': True,
    })


@bp.route('/sleep/status')
@login_required
def sleep_status():
    return jsonify({'due_sleep_reminder': _sleep_reminder_payload(current_user)})


@bp.route('/hydration/status')
@login_required
def hydration_status():
    due_prompt, upcoming_prompt = _get_due_and_upcoming_prompt(current_user.id)
    missed_summary = _missed_hydration_summary(current_user.id)
    db.session.commit()
    return jsonify(
        {
            'due_prompt': _serialize_prompt(due_prompt),
            'upcoming_prompt': _serialize_prompt(upcoming_prompt),
            'morning_prompt': None,
            'morning_prompt_exists': False,
            'missed_summary': missed_summary,
        }
    )


@bp.route('/hydration/respond', methods=['POST'])
@login_required
def hydration_respond():
    data = request.get_json(silent=True) or request.form
    prompt_id = data.get('prompt_id')
    prompt_type = _clean_text(data.get('prompt_type') or 'scheduled_wake', 30).lower()
    action = _clean_text(data.get('action') or data.get('response_status') or '', 20).lower()
    beverage = _clean_text(data.get('beverage') or 'water', 60)
    custom_beverage = _clean_text(data.get('custom_beverage'), 120)
    amount_text = _clean_text(data.get('amount_text'), 80)

    prompt = None
    if prompt_id:
        try:
            prompt = HydrationPrompt.query.filter_by(id=int(prompt_id), user_id=current_user.id).first()
        except (TypeError, ValueError):
            prompt = None

    if not prompt:
        due_prompt, _ = _get_due_and_upcoming_prompt(current_user.id)
        if due_prompt:
            prompt = due_prompt

    if not prompt:
        return jsonify({'message': 'Prompt not found.'}), 404

    if action not in {'finished', 'done', 'not_yet', 'skipped', 'dismissed'}:
        return jsonify({'message': 'Unknown hydration action.'}), 400
    if beverage.strip().lower() == 'other' and not custom_beverage:
        return jsonify({'message': 'Please type your drink name when you choose Other.'}), 400

    normalized_beverage = _normalize_beverage(beverage, custom_beverage)
    prompt.beverage = normalized_beverage
    prompt.custom_beverage = custom_beverage or None
    prompt.responded_at = local_now().replace(tzinfo=None)

    if action in {'finished', 'done'}:
        amount_ml = convert_drink_amount_to_ml(normalized_beverage, amount_text).get('amount_ml', 250) if amount_text else 250
        log = _get_or_create_log_for_today(current_user.id)
        water_error = _water_limit_error(log.water_ml, amount_ml)
        if water_error:
            return jsonify({'message': water_error}), 400
        prompt.response_status = 'finished'
        log.water_ml = int(log.water_ml or 0) + int(amount_ml)
        db.session.flush()
        payload = _apply_wellness_update(current_user, log.log_date, f'Drank {amount_ml} ml of {normalized_beverage}')
        _log_activity_entry(current_user.id, 'hydration', 'Hydration logged', f'{amount_ml} ml {normalized_beverage}', impacts=payload.get('feedback', {}).get('metrics'))
        _sync_goal_based_hydration_prompts(current_user, log.log_date)
        message = f"Great job. Added {amount_ml} ml to today's water total."
    elif action == 'not_yet':
        prompt.response_status = 'dismissed'
        task_date = local_today()
        reminder_text = _hydration_prompt_label(prompt.prompt_type)
        existing_task = Task.query.filter_by(user_id=current_user.id, task_date=task_date, title=reminder_text, completed=False).first()
        if not existing_task:
            db.session.add(Task(
                user_id=current_user.id,
                title=reminder_text,
                description='Added from your fixed water reminder time.',
                task_type='regular',
                task_date=task_date,
                completed=False,
                sort_order=_get_next_sort_order(current_user.id, task_date),
            ))
        db.session.flush()
        payload = _apply_wellness_update(current_user, local_today(), 'Hydration reminder postponed')
        _log_activity_entry(current_user.id, 'hydration', 'Hydration reminder postponed', prompt.message, impacts=payload.get('feedback', {}).get('metrics'))
        _sync_goal_based_hydration_prompts(current_user, local_today())
        message = "Okay. I added a water task to today's todo list. The next automatic reminder will wait for your next scheduled water time."
    else:
        prompt.response_status = 'dismissed'
        db.session.flush()
        payload = _apply_wellness_update(current_user, local_today(), 'Hydration reminder skipped')
        _log_activity_entry(current_user.id, 'hydration', 'Hydration reminder skipped', prompt.message, impacts=payload.get('feedback', {}).get('metrics'))
        _sync_goal_based_hydration_prompts(current_user, local_today())
        message = 'No problem. The reminder was skipped.'

    db.session.commit()
    due_prompt, upcoming_prompt = _get_due_and_upcoming_prompt(current_user.id)
    missed_summary = _missed_hydration_summary(current_user.id)
    return jsonify(
        {
            'message': message,
            'due_prompt': _serialize_prompt(due_prompt),
            'upcoming_prompt': _serialize_prompt(upcoming_prompt),
            'missed_summary': missed_summary,
            'wellness_scores': _serialize_wellness(current_user),
            'wellness_summary': payload.get('summary'),
            'wellness_feedback': payload.get('feedback'),
        }
    )


