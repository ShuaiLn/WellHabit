from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from .. import db
from ..ai_services import care_chat_reply, summarize_care_chat_session
from ..constants import AI_MAX_MESSAGE_CHARS
from ..services.activity import _log_activity_entry
from ..services.ai_suggestions import _maybe_create_ai_suggestion_task, _rank_personalized_interventions
from ..services.care_chat import (
    _append_care_chat_message,
    _care_chat_history_payload,
    _care_chat_messages_for_ai,
    _care_chat_messages_for_session,
    _get_care_chat_session_for_user,
    _get_or_create_active_care_chat_session,
    _normalize_care_messages,
)
from ..services.care_intents import CARE_BOUNDARY_LINES, _care_crisis_support_payload, _care_quick_action_payload
from ..services.wellness import (
    _apply_wellness_update,
    _decorate_feedback_with_ai_task,
    _ensure_baseline_scores,
    _mood_badge_payload,
    _record_mood_entry,
    _score_snapshot,
    _serialize_wellness,
    _store_wellness_feedback,
)
from ..utils.text import _clean_text, _normalize_mood_choice
from ..utils.timez import local_now, local_today

bp = Blueprint('care', __name__)




@bp.route('/api/care/chat', methods=['POST'], endpoint='care_chat_message_api')
@login_required
def care_chat_message():
    data = request.get_json(silent=True) or {}
    session_id = str(data.get('session_id') or '').strip()
    care_session = _get_care_chat_session_for_user(current_user.id, session_id, require_active=True) if session_id else None
    if not care_session:
        return jsonify({'message': 'This care chat has already ended. Please open a new one.'}), 409

    posted_messages = _normalize_care_messages(data.get('messages'))
    latest_user_text = ''
    if posted_messages and posted_messages[-1]['role'] == 'user':
        latest_user_text = _clean_text(posted_messages[-1].get('content') or '', AI_MAX_MESSAGE_CHARS)
    if not latest_user_text:
        return jsonify({'message': 'Please send a user message first.'}), 400

    stored_messages = _care_chat_messages_for_session(care_session.id)
    last_stored = stored_messages[-1] if stored_messages else None
    if not last_stored or last_stored.role != 'user' or (last_stored.content or '').strip() != latest_user_text:
        _append_care_chat_message(care_session, 'user', latest_user_text)
        db.session.flush()

    ai_messages = _care_chat_messages_for_ai(care_session.id)
    care_context_text = ' '.join(item.get('content') or '' for item in ai_messages if item.get('role') == 'user').strip()
    intervention_context = _rank_personalized_interventions(
        current_user,
        care_context_text or latest_user_text,
        detected_mood=None,
        target_date=local_today(),
    )
    reply_payload = care_chat_reply(ai_messages, _score_snapshot(current_user), intervention_context=intervention_context)
    assistant_message = _clean_text(reply_payload.get('reply') or 'I’m here with you.', AI_MAX_MESSAGE_CHARS)
    _append_care_chat_message(care_session, 'assistant', assistant_message)
    quick_action = _care_quick_action_payload(current_user, latest_user_text)
    db.session.commit()
    return jsonify(
        {
            'assistant_message': assistant_message,
            'risk_level': reply_payload.get('risk_level') or 'low',
            'quick_action': quick_action,
        }
    )


@bp.route('/api/care/end', methods=['POST'], endpoint='care_chat_end_api')
@login_required
def care_chat_end():
    data = request.get_json(silent=True) or {}
    session_id = str(data.get('session_id') or '').strip()
    care_session = _get_care_chat_session_for_user(current_user.id, session_id, require_active=True) if session_id else None

    if not care_session:
        return jsonify({'ended': True, 'updated': False})

    messages = _care_chat_messages_for_ai(care_session.id)
    user_messages = [item for item in messages if item['role'] == 'user']
    ended_at = local_now().replace(tzinfo=None)
    care_session.ended_at = ended_at
    care_session.last_activity_at = ended_at
    db.session.flush()

    if not user_messages:
        db.session.commit()
        return jsonify({'ended': True, 'updated': False})

    summary_payload = summarize_care_chat_session(messages, _score_snapshot(current_user))
    detected_mood = _normalize_mood_choice(summary_payload.get('detected_mood'), summary_payload.get('detected_mood_display'))
    mood_badge = _mood_badge_payload(*detected_mood)
    _record_mood_entry(
        current_user.id,
        'care_chat',
        detected_mood[0],
        detected_mood[1],
        summary=summary_payload.get('summary') or 'Care chat mood recorded.',
        event_at=ended_at,
        detected_by='ai',
    )
    db.session.flush()
    payload = _apply_wellness_update(current_user, local_today(), summary_payload.get('latest_event') or 'Care chat ended')
    care_user_text = ' '.join(item.get('content') or '' for item in user_messages).strip()
    db.session.flush()
    ai_result = _maybe_create_ai_suggestion_task(
        current_user,
        care_user_text or summary_payload.get('summary') or summary_payload.get('latest_event') or 'Care chat ended',
        detected_mood=detected_mood[0],
        target_date=local_today(),
        source_label='care_chat',
    )
    ai_task = ai_result.get('task')
    feedback = dict(payload.get('feedback') or {})
    feedback['care_summary'] = summary_payload.get('summary') or 'A caring AI chat was completed.'
    feedback['detected_mood'] = mood_badge['display']
    feedback['detected_mood_label'] = mood_badge['label']
    feedback['title'] = 'Care chat ended'
    feedback['boundary_lines'] = CARE_BOUNDARY_LINES
    crisis_support = _care_crisis_support_payload(data, care_user_text, detected_mood[0])
    if crisis_support:
        feedback['crisis_support'] = crisis_support
        feedback['message'] = f"{str(feedback.get('message') or '').strip()} Real-person support is available too.".strip()
    if ai_task:
        feedback = _decorate_feedback_with_ai_task(feedback, ai_task, status=ai_result.get('status') or 'added') or feedback
    _store_wellness_feedback(feedback)
    _log_activity_entry(
        current_user.id,
        'care',
        'Care chat summary',
        summary_payload.get('summary') or 'A caring AI chat was completed.',
        impacts=feedback.get('metrics'),
    )
    db.session.commit()
    return jsonify(
        {
            'ended': True,
            'updated': True,
            'summary': summary_payload.get('summary'),
            'detected_mood': mood_badge['display'],
            'wellness_feedback': feedback,
            'wellness_scores': _serialize_wellness(current_user),
        }
    )
