from datetime import datetime, date

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, login_manager


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

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

    daily_logs = db.relationship('DailyLog', backref='user', lazy=True, cascade='all, delete-orphan')
    tasks = db.relationship('Task', backref='user', lazy=True, cascade='all, delete-orphan')
    calendar_events = db.relationship('CalendarEvent', backref='user', lazy=True, cascade='all, delete-orphan')
    pomodoro_sessions = db.relationship('PomodoroSession', backref='user', lazy=True, cascade='all, delete-orphan')
    hydration_prompts = db.relationship('HydrationPrompt', backref='user', lazy=True, cascade='all, delete-orphan')
    activity_entries = db.relationship('ActivityEntry', backref='user', lazy=True, cascade='all, delete-orphan')
    mood_entries = db.relationship('MoodEntry', backref='user', lazy=True, cascade='all, delete-orphan')
    eye_exercise_prompts = db.relationship('EyeExercisePrompt', backref='user', lazy=True, cascade='all, delete-orphan')
    eye_exercise_states = db.relationship('EyeExerciseState', backref='user', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class DailyLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    log_date = db.Column(db.Date, default=date.today, nullable=False, index=True)
    water_ml = db.Column(db.Integer, nullable=False, default=0)
    sleep_hours = db.Column(db.Float, nullable=False, default=0)
    sleep_quality = db.Column(db.String(20), nullable=False, default='Average')
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class CalendarEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    event_date = db.Column(db.Date, nullable=False, index=True)
    event_time = db.Column(db.Time)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PomodoroSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    focus_minutes = db.Column(db.Integer, nullable=False)
    break_minutes = db.Column(db.Integer, nullable=False)
    cycle_number = db.Column(db.Integer, nullable=False, default=1)
    activity_label = db.Column(db.String(200))
    completed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


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
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    responded_at = db.Column(db.DateTime)


class ActivityEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    entry_type = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    event_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


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
    event_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)



class EyeExercisePrompt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    focus_minutes_trigger = db.Column(db.Integer, nullable=False, default=25)
    threshold_minutes = db.Column(db.Integer, nullable=False, default=25)
    video_url = db.Column(db.String(300), nullable=False, default='https://www.youtube.com/watch?v=iVb4vUp70zY')
    response_status = db.Column(db.String(20), nullable=False, default='pending')
    due_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    responded_at = db.Column(db.DateTime)


class EyeExerciseState(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    carry_focus_minutes = db.Column(db.Integer, nullable=False, default=0)
    active_prompt_id = db.Column(db.Integer, db.ForeignKey('eye_exercise_prompt.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
