import re

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

CARE_NEGATIVE_MOODS = {'anxious', 'sad', 'stressed', 'overwhelmed', 'exhausted'}
CARE_POSITIVE_MOODS = {'happy', 'calm', 'hopeful'}
CARE_SCORE_LABELS = {
    'hydration': 'Hydration',
    'energy': 'Energy',
    'fitness': 'Fitness',
    'focus': 'Focus',
    'mood': 'Mood',
    'overall': 'Overall Wellness',
}


def _clamp_care_score(value: int | float) -> int:
    return max(0, min(100, int(round(float(value)))))


def _care_user_reported_improvement(user_text: str | None) -> bool:
    lowered = (user_text or '').lower()
    patterns = [
        r'\bi\s+(feel|felt|am|\'m)\s+(a\s+little\s+|slightly\s+|much\s+)?(better|calmer|relieved|grounded|okay|ok|fine|hopeful)\b',
        r'\bi\s+feel\s+less\s+(anxious|sad|stressed|overwhelmed|panicked)\b',
        r'\bi\s+(feel|felt)\s+more\s+(calm|grounded|hopeful|steady)\b',
        r'\bthat\s+helped\b',
    ]
    if any(re.search(pattern, lowered) for pattern in patterns):
        return True
    return any(phrase in (user_text or '') for phrase in ['好多了', '好一点', '没那么焦虑', '平静一点', '冷静一点', '感觉好些'])


def _care_scoring_policy(detected_mood_label: str | None, user_text: str | None) -> dict:
    mood = (detected_mood_label or 'normal').strip().lower()
    explicit_improvement = _care_user_reported_improvement(user_text)
    if mood in CARE_NEGATIVE_MOODS:
        if explicit_improvement:
            mood_delta = 2
            message = 'Care chat recorded a difficult feeling, with a small positive adjustment because the user explicitly said they felt a little better.'
            event = f'Care chat ended: user reported feeling {mood} and explicitly said they felt a little better'
        else:
            mood_delta = -6 if mood in {'anxious', 'sad', 'stressed', 'overwhelmed'} else -4
            message = 'Care chat recorded a difficult feeling. Mood was not increased because the user did not explicitly say they felt better.'
            event = f'Care chat ended: user reported feeling {mood}; no explicit improvement reported'
        return {
            'mood': mood,
            'explicit_improvement': explicit_improvement,
            'mood_delta': mood_delta,
            'energy_delta': -2 if mood == 'exhausted' and not explicit_improvement else 0,
            'message': message,
            'event': event,
        }
    if mood in CARE_POSITIVE_MOODS:
        return {
            'mood': mood,
            'explicit_improvement': explicit_improvement,
            'mood_delta': 3,
            'energy_delta': 0,
            'message': 'Care chat recorded a positive mood from the user.',
            'event': f'Care chat ended: user reported feeling {mood}',
        }
    return {
        'mood': mood,
        'explicit_improvement': explicit_improvement,
        'mood_delta': 0,
        'energy_delta': 0,
        'message': 'Care chat recorded the user\'s mood without assuming improvement from the AI support itself.',
        'event': 'Care chat ended: user talked about their feelings; no explicit improvement reported',
    }


def _care_feedback_from_scores(payload: dict, previous_scores: dict[str, int]) -> dict:
    after_scores = {
        'hydration': int(payload.get('hydration_score') or previous_scores['hydration']),
        'energy': int(payload.get('energy_score') or previous_scores['energy']),
        'fitness': int(payload.get('fitness_score') or previous_scores['fitness']),
        'focus': int(payload.get('focus_score') or previous_scores['focus']),
        'mood': int(payload.get('mood_score') or previous_scores['mood']),
        'overall': int(payload.get('overall_wellness_score') or previous_scores['overall']),
    }
    metrics = []
    positive_total = 0
    negative_total = 0
    for key in ['hydration', 'energy', 'fitness', 'focus', 'mood', 'overall']:
        delta = after_scores[key] - int(previous_scores.get(key) or 0)
        if delta > 0:
            positive_total += delta
        elif delta < 0:
            negative_total += delta
        if delta != 0:
            metrics.append({
                'key': key,
                'label': CARE_SCORE_LABELS[key],
                'delta': delta,
                'signed': f'{delta:+d}',
                'tone_class': 'plus' if delta > 0 else 'minus',
            })
    metrics.sort(key=lambda item: (item['key'] != 'overall', -abs(item['delta'])))
    if not metrics:
        metrics = [{
            'key': 'overall',
            'label': CARE_SCORE_LABELS['overall'],
            'delta': 0,
            'signed': '+0',
            'tone_class': 'zero',
        }]
    if positive_total > abs(negative_total):
        tone = 'positive'
        title = 'Care chat recorded'
    elif negative_total < 0:
        tone = 'negative'
        title = 'Care mood recorded'
    else:
        tone = 'steady'
        title = 'Care chat recorded'
    return {
        'tone': tone,
        'title': title,
        'message': str(payload.get('summary') or 'Care chat scores were updated.'),
        'metrics': metrics[:6],
        'avatar_emoji': payload.get('avatar_emoji') or '🙂',
    }


def _apply_care_score_policy_to_user(user, payload: dict, previous_scores: dict[str, int], policy: dict) -> dict:
    # Care chat scoring should reflect the user's reported mood, not the fact that
    # the assistant offered calming techniques. Keep unrelated metrics unchanged.
    hydration = int(previous_scores['hydration'])
    fitness = int(previous_scores['fitness'])
    focus = int(previous_scores['focus'])
    energy = _clamp_care_score(int(previous_scores['energy']) + int(policy.get('energy_delta') or 0))
    mood = _clamp_care_score(int(previous_scores['mood']) + int(policy.get('mood_delta') or 0))
    overall = _clamp_care_score((hydration + energy + fitness + focus + mood) / 5)

    user.hydration_score = hydration
    user.energy_score = energy
    user.fitness_score = fitness
    user.focus_score = focus
    user.mood_score = mood
    user.overall_wellness_score = overall

    payload.update({
        'hydration_score': hydration,
        'energy_score': energy,
        'fitness_score': fitness,
        'focus_score': focus,
        'mood_score': mood,
        'overall_wellness_score': overall,
        'summary': policy.get('message') or payload.get('summary') or 'Care chat mood recorded.',
        'source': f"{payload.get('source') or 'care'}+care_policy",
    })
    payload['feedback'] = _care_feedback_from_scores(payload, previous_scores)
    return payload



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

    score_before_care = _score_snapshot(current_user)
    summary_payload = summarize_care_chat_session(messages, score_before_care)
    detected_mood = _normalize_mood_choice(summary_payload.get('detected_mood'), summary_payload.get('detected_mood_display'))
    mood_badge = _mood_badge_payload(*detected_mood)
    care_user_text = ' '.join(item.get('content') or '' for item in user_messages).strip()
    care_policy = _care_scoring_policy(detected_mood[0], care_user_text)
    summary_payload['latest_event'] = care_policy['event']
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
    payload = _apply_wellness_update(current_user, local_today(), care_policy['event'])
    payload = _apply_care_score_policy_to_user(current_user, payload, score_before_care, care_policy)
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
