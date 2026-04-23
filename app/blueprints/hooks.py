from flask import Blueprint, request
from flask_login import current_user

from ..constants import (
    DEFAULT_POMODORO_ACTIVITY_LABEL,
    DEFAULT_POMODORO_BREAK_MINUTES,
    DEFAULT_POMODORO_FOCUS_MINUTES,
    EYE_EXERCISE_THRESHOLD_MINUTES,
    HYDRATION_DUE_GRACE_MINUTES,
)
from ..services.ai_suggestions import _consume_ai_suggestion_added, _consume_ai_suggestion_followup
from ..services.tasks import _sync_overdue_tasks_once_per_day
from ..services.wellness import _consume_wellness_feedback
from ..utils.timez import local_now

bp = Blueprint('hooks', __name__)



# Paths that fire every ~60 seconds from the frontend polling loop. The overdue-task
# rollover only needs to happen on real page loads; skipping it for these GET
# endpoints saves a function call per poll and guarantees no write work happens
# inside a read-only status probe.
_ROLLOVER_SKIP_PATH_PREFIXES = (
    '/hydration/status',
    '/eye-exercise/status',
    '/sleep/status',
    '/static/',
)


@bp.before_app_request
def sync_overdue_tasks():
    if not current_user.is_authenticated:
        return
    if request.method == 'GET' and any(request.path.startswith(p) for p in _ROLLOVER_SKIP_PATH_PREFIXES):
        return
    # The function itself also guards on user.last_task_rollover_on == today,
    # so repeated calls within the same day are cheap (in-memory only).
    _sync_overdue_tasks_once_per_day(current_user)


@bp.app_context_processor
def inject_nav_context():
    return {
        'nav_local_date': local_now().strftime('%A, %B %d, %Y'),
        'nav_local_time': local_now().strftime('%I:%M %p'),
        'pending_wellness_feedback': _consume_wellness_feedback(),
        'pending_ai_suggestion_followup': _consume_ai_suggestion_followup(),
        'pending_ai_suggestion_added': _consume_ai_suggestion_added(),
        'current_avatar_emoji': (current_user.avatar_emoji if current_user.is_authenticated else '🙂'),
        'app_ui_defaults': {
            'focus_minutes': DEFAULT_POMODORO_FOCUS_MINUTES,
            'break_minutes': DEFAULT_POMODORO_BREAK_MINUTES,
            'activity_label': DEFAULT_POMODORO_ACTIVITY_LABEL,
            'eye_exercise_threshold_minutes': EYE_EXERCISE_THRESHOLD_MINUTES,
            'hydration_due_grace_minutes': HYDRATION_DUE_GRACE_MINUTES,
            'ui_release': 'minimal_full_cutover',
        },
    }


