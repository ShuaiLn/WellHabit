from datetime import date
from uuid import uuid4

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, login_manager
from .constants import EYE_EXERCISE_THRESHOLD_MINUTES
from .utils.timez import _utcnow


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)

    age = db.Column(db.Integer)
    gender_identity = db.Column(db.String(30))
    weight_kg = db.Column(db.Float)
    height_cm = db.Column(db.Float)
    daily_water_goal_ml = db.Column(db.Integer, nullable=False, default=2000)
    daily_sleep_goal_hours = db.Column(db.Float, nullable=False, default=8.0)
    daily_step_goal = db.Column(db.Integer, nullable=False, default=8000)
    daily_exercise_goal_minutes = db.Column(db.Integer, nullable=False, default=30)
    goal_progress_intensity = db.Column(db.String(12), nullable=False, default='medium')
    optimal_bedtime = db.Column(db.String(5))
    optimal_wake_time = db.Column(db.String(5))
    hydration_wake_time = db.Column(db.String(5))
    hydration_breakfast_time = db.Column(db.String(5))
    hydration_lunch_time = db.Column(db.String(5))
    hydration_dinner_time = db.Column(db.String(5))

    hydration_score = db.Column(db.Integer, nullable=False, default=50)
    energy_score = db.Column(db.Integer, nullable=False, default=50)
    fitness_score = db.Column(db.Integer, nullable=False, default=50)
    focus_score = db.Column(db.Integer, nullable=False, default=50)
    mood_score = db.Column(db.Integer, nullable=False, default=50)
    overall_wellness_score = db.Column(db.Integer, nullable=False, default=50)
    avatar_emoji = db.Column(db.String(16), nullable=False, default='🙂')
    wellness_summary = db.Column(db.Text)
    wellness_updated_at = db.Column(db.DateTime)
    last_task_rollover_on = db.Column(db.Date)
    last_activity_pruned_at = db.Column(db.DateTime)

    daily_logs = db.relationship('DailyLog', backref='user', lazy=True, cascade='all, delete-orphan')
    tasks = db.relationship('Task', backref='user', lazy=True, cascade='all, delete-orphan')
    calendar_events = db.relationship('CalendarEvent', backref='user', lazy=True, cascade='all, delete-orphan')
    pomodoro_sessions = db.relationship('PomodoroSession', backref='user', lazy=True, cascade='all, delete-orphan')
    hydration_prompts = db.relationship('HydrationPrompt', backref='user', lazy=True, cascade='all, delete-orphan')
    activity_entries = db.relationship('ActivityEntry', backref='user', lazy=True, cascade='all, delete-orphan')
    mood_entries = db.relationship('MoodEntry', backref='user', lazy=True, cascade='all, delete-orphan')
    eye_exercise_prompts = db.relationship('EyeExercisePrompt', backref='user', lazy=True, cascade='all, delete-orphan')
    eye_exercise_states = db.relationship('EyeExerciseState', backref='user', lazy=True, cascade='all, delete-orphan')
    care_chat_sessions = db.relationship('CareChatSession', backref='user', lazy=True, cascade='all, delete-orphan')
    break_sessions = db.relationship('BreakSession', backref='user', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class DailyLog(db.Model):
    __table_args__ = (db.UniqueConstraint('user_id', 'log_date', name='ux_daily_log_user_date'),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    log_date = db.Column(db.Date, default=date.today, nullable=False, index=True)
    water_ml = db.Column(db.Integer, nullable=False, default=0)
    sleep_hours = db.Column(db.Float, nullable=False, default=0)
    steps = db.Column(db.Integer, nullable=False, default=0)
    exercise_minutes = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.Text)
    journal_text = db.Column(db.Text)
    mood_label = db.Column(db.String(40))
    mood_custom_text = db.Column(db.String(120))
    activity_text = db.Column(db.Text)
    ai_meal_detected = db.Column(db.Boolean, nullable=False, default=False)
    ai_meal_confidence = db.Column(db.String(20))
    ai_feedback = db.Column(db.Text)
    last_meal_detected_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)

    hydration_prompts = db.relationship('HydrationPrompt', backref='daily_log', lazy=True)


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    task_type = db.Column(db.String(30), nullable=False, default='regular')
    task_date = db.Column(db.Date, nullable=False, index=True)
    completed = db.Column(db.Boolean, default=False, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    completed_at = db.Column(db.DateTime)
    auto_tracked_water_ml = db.Column(db.Integer, nullable=False, default=0)
    ai_generated_source = db.Column(db.String(30))
    ai_suggestion_key = db.Column(db.String(40))
    ai_followup_question = db.Column(db.String(240))
    ai_followup_rating = db.Column(db.Integer)
    ai_followup_completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)


class CalendarEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    event_date = db.Column(db.Date, nullable=False, index=True)
    event_time = db.Column(db.Time)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)


class PomodoroSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    focus_minutes = db.Column(db.Integer, nullable=False)
    break_minutes = db.Column(db.Integer, nullable=False)
    cycle_number = db.Column(db.Integer, nullable=False, default=1)
    activity_label = db.Column(db.String(200))
    completed_at = db.Column(db.DateTime, default=_utcnow, nullable=False, index=True)


class HydrationPrompt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    log_id = db.Column(db.Integer, db.ForeignKey('daily_log.id'))
    prompt_type = db.Column(db.String(30), nullable=False)
    message = db.Column(db.Text)
    beverage = db.Column(db.String(60))
    custom_beverage = db.Column(db.String(120))
    response_status = db.Column(db.String(20), nullable=False, default='pending')
    due_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    responded_at = db.Column(db.DateTime)


class ActivityEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    entry_type = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    event_at = db.Column(db.DateTime, default=_utcnow, nullable=False, index=True)


class ClientState(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    state_key = db.Column(db.String(40), nullable=False)
    payload_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False, index=True)


class MoodEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    log_id = db.Column(db.Integer, db.ForeignKey('daily_log.id'))
    source = db.Column(db.String(30), nullable=False, default='journal')
    mood_label = db.Column(db.String(40), nullable=False)
    mood_custom_text = db.Column(db.String(120))
    mood_value = db.Column(db.Integer, nullable=False, default=50)
    summary = db.Column(db.Text)
    detected_by = db.Column(db.String(20), nullable=False, default='user')
    event_at = db.Column(db.DateTime, default=_utcnow, nullable=False, index=True)


class CareChatSession(db.Model):
    __table_args__ = (db.Index('ix_care_chat_session_user_ended_at', 'user_id', 'ended_at'),)

    id = db.Column(db.String(32), primary_key=True, default=lambda: uuid4().hex)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    started_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    ended_at = db.Column(db.DateTime, index=True)
    last_activity_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    message_count = db.Column(db.Integer, nullable=False, default=0)

    messages = db.relationship('CareChatMessage', backref='care_chat_session', lazy=True, cascade='all, delete-orphan')


class CareChatMessage(db.Model):
    __table_args__ = (db.Index('ix_care_chat_message_session_created', 'session_id', 'created_at'),)

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(32), db.ForeignKey('care_chat_session.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False, index=True)



class EyeExercisePrompt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    focus_minutes_trigger = db.Column(db.Integer, nullable=False, default=EYE_EXERCISE_THRESHOLD_MINUTES)
    threshold_minutes = db.Column(db.Integer, nullable=False, default=EYE_EXERCISE_THRESHOLD_MINUTES)
    video_url = db.Column(db.String(300), nullable=False, default='https://www.youtube.com/watch?v=iVb4vUp70zY')
    response_status = db.Column(db.String(20), nullable=False, default='pending')
    due_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    responded_at = db.Column(db.DateTime)


class EyeExerciseState(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    carry_focus_minutes = db.Column(db.Integer, nullable=False, default=0)
    active_prompt_id = db.Column(db.Integer, db.ForeignKey('eye_exercise_prompt.id'))
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, nullable=False)


class BreakSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    started_at = db.Column(db.DateTime, default=_utcnow, nullable=False, index=True)
    ended_at = db.Column(db.DateTime)
    trigger = db.Column(db.String(30), nullable=False, default='manual')
    exercises_done = db.Column(db.Text, nullable=False, default='[]')
    self_report = db.Column(db.String(30))
    fatigue_signal_snapshot = db.Column(db.Text)

class DailySignal(db.Model):
    __table_args__ = (db.UniqueConstraint('user_id', 'signal_date', name='ux_daily_signal_user_date'),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    signal_date = db.Column(db.Date, nullable=False, index=True)
    has_daily_log = db.Column(db.Boolean, nullable=False, default=False)
    active_app_day = db.Column(db.Boolean, nullable=False, default=False)
    sleep_hours = db.Column(db.Float, nullable=False, default=0)
    mood_score = db.Column(db.Integer, nullable=False, default=50)
    focus_sessions = db.Column(db.Integer, nullable=False, default=0)
    focus_minutes = db.Column(db.Integer, nullable=False, default=0)
    focus_completion_rate = db.Column(db.Float, nullable=False, default=0)
    water_ml = db.Column(db.Integer, nullable=False, default=0)
    water_goal_ml = db.Column(db.Integer, nullable=False, default=0)
    water_completion_rate = db.Column(db.Float, nullable=False, default=0)
    exercise_minutes = db.Column(db.Integer, nullable=False, default=0)
    eye_break_count = db.Column(db.Integer, nullable=False, default=0)
    rest_break_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, nullable=False)


class UserBaseline(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    window_days = db.Column(db.Integer, nullable=False, default=14)
    calculated_on = db.Column(db.Date, nullable=False, default=date.today, index=True)
    metrics_json = db.Column(db.Text, nullable=False, default='{}')
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, nullable=False)


class PatternRule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rule_key = db.Column(db.String(60), nullable=False, unique=True)
    display_name = db.Column(db.String(120), nullable=False)
    safe_headline = db.Column(db.String(240), nullable=False)
    description = db.Column(db.Text)
    window_days = db.Column(db.Integer, nullable=False, default=3)
    trigger_score = db.Column(db.Integer, nullable=False, default=4)
    clear_score_ratio = db.Column(db.Float, nullable=False, default=0.7)
    intervention_title = db.Column(db.String(160), nullable=False)
    intervention_detail = db.Column(db.Text)
    intervention_task_title = db.Column(db.String(200), nullable=False)
    intervention_task_description = db.Column(db.Text)
    feedback_question = db.Column(db.String(240))
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, nullable=False)


class UserPatternState(db.Model):
    __table_args__ = (db.UniqueConstraint('user_id', 'rule_key', name='ux_user_pattern_state_user_rule'),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rule_key = db.Column(db.String(60), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default='active', index=True)
    score = db.Column(db.Integer, nullable=False, default=0)
    score_threshold = db.Column(db.Integer, nullable=False, default=0)
    evidence_json = db.Column(db.Text, nullable=False, default='[]')
    triggered_on = db.Column(db.Date)
    last_scored_on = db.Column(db.Date, index=True)
    consecutive_low_days = db.Column(db.Integer, nullable=False, default=0)
    feedback_status = db.Column(db.String(30))
    feedback_rating = db.Column(db.Integer)
    feedback_note = db.Column(db.Text)
    no_thanks_count = db.Column(db.Integer, nullable=False, default=0)
    push_suppressed_until = db.Column(db.Date)
    intervention_task_id = db.Column(db.Integer, db.ForeignKey('task.id'))
    resolved_on = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
