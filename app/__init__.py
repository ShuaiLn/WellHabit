from pathlib import Path
import sqlite3

from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.login_message_category = 'warning'


def _get_sqlite_db_path(app: Flask) -> Path:
    uri = app.config['SQLALCHEMY_DATABASE_URI']
    prefix = 'sqlite:///'
    if not uri.startswith(prefix):
        raise ValueError('This lightweight migration helper expects SQLite.')
    return Path(uri[len(prefix):])


def _ensure_column(cursor: sqlite3.Cursor, table_name: str, column_name: str, column_sql: str) -> None:
    columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table_name})")}
    if column_name not in columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def run_lightweight_migrations(app: Flask) -> None:
    db_path = _get_sqlite_db_path(app)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        tables = {
            row[0] for row in cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        if 'user' in tables:
            _ensure_column(cursor, 'user', 'age', 'age INTEGER')
            _ensure_column(cursor, 'user', 'gender_identity', "gender_identity VARCHAR(30)")
            _ensure_column(cursor, 'user', 'weight_kg', 'weight_kg FLOAT')
            _ensure_column(cursor, 'user', 'height_cm', 'height_cm FLOAT')
            _ensure_column(cursor, 'user', 'daily_water_goal_ml', 'daily_water_goal_ml INTEGER NOT NULL DEFAULT 2000')
            _ensure_column(cursor, 'user', 'daily_sleep_goal_hours', 'daily_sleep_goal_hours FLOAT NOT NULL DEFAULT 8.0')
            _ensure_column(cursor, 'user', 'daily_step_goal', 'daily_step_goal INTEGER NOT NULL DEFAULT 8000')
            _ensure_column(cursor, 'user', 'daily_exercise_goal_minutes', 'daily_exercise_goal_minutes INTEGER NOT NULL DEFAULT 30')
            _ensure_column(cursor, 'user', 'optimal_bedtime', "optimal_bedtime VARCHAR(5)")
            _ensure_column(cursor, 'user', 'optimal_wake_time', "optimal_wake_time VARCHAR(5)")
            _ensure_column(cursor, 'user', 'hydration_score', 'hydration_score INTEGER NOT NULL DEFAULT 50')
            _ensure_column(cursor, 'user', 'energy_score', 'energy_score INTEGER NOT NULL DEFAULT 50')
            _ensure_column(cursor, 'user', 'fitness_score', 'fitness_score INTEGER NOT NULL DEFAULT 50')
            _ensure_column(cursor, 'user', 'focus_score', 'focus_score INTEGER NOT NULL DEFAULT 50')
            _ensure_column(cursor, 'user', 'mood_score', 'mood_score INTEGER NOT NULL DEFAULT 50')
            _ensure_column(cursor, 'user', 'overall_wellness_score', 'overall_wellness_score INTEGER NOT NULL DEFAULT 50')
            _ensure_column(cursor, 'user', 'wellness_summary', 'wellness_summary TEXT')
            _ensure_column(cursor, 'user', 'wellness_updated_at', 'wellness_updated_at DATETIME')

        if 'daily_log' in tables:
            _ensure_column(cursor, 'daily_log', 'journal_text', 'journal_text TEXT')
            _ensure_column(cursor, 'daily_log', 'activity_text', 'activity_text TEXT')
            _ensure_column(cursor, 'daily_log', 'ai_meal_detected', 'ai_meal_detected BOOLEAN NOT NULL DEFAULT 0')
            _ensure_column(cursor, 'daily_log', 'ai_meal_confidence', "ai_meal_confidence VARCHAR(20)")
            _ensure_column(cursor, 'daily_log', 'ai_feedback', 'ai_feedback TEXT')
            _ensure_column(cursor, 'daily_log', 'last_meal_detected_at', 'last_meal_detected_at DATETIME')

        if 'task' in tables:
            _ensure_column(cursor, 'task', 'sort_order', 'sort_order INTEGER NOT NULL DEFAULT 0')
            _ensure_column(cursor, 'task', 'completed_at', 'completed_at DATETIME')
            _ensure_column(cursor, 'task', 'task_type', "task_type VARCHAR(30) NOT NULL DEFAULT 'regular'")
            cursor.execute('UPDATE task SET sort_order = id WHERE sort_order IS NULL OR sort_order = 0')
            cursor.execute("UPDATE task SET task_type = 'regular' WHERE task_type IS NULL OR task_type = ''")

        if 'calendar_event' not in tables:
            cursor.execute(
                '''
                CREATE TABLE calendar_event (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    title VARCHAR(200) NOT NULL,
                    description TEXT,
                    event_date DATE NOT NULL,
                    event_time TIME,
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES user (id)
                )
                '''
            )
            cursor.execute('CREATE INDEX IF NOT EXISTS ix_calendar_event_user_date ON calendar_event (user_id, event_date)')

        if 'hydration_prompt' not in tables:
            cursor.execute(
                '''
                CREATE TABLE hydration_prompt (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    log_id INTEGER,
                    prompt_type VARCHAR(30) NOT NULL,
                    message TEXT,
                    beverage VARCHAR(60),
                    custom_beverage VARCHAR(120),
                    response_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    due_at DATETIME NOT NULL,
                    created_at DATETIME NOT NULL,
                    responded_at DATETIME,
                    FOREIGN KEY(user_id) REFERENCES user (id),
                    FOREIGN KEY(log_id) REFERENCES daily_log (id)
                )
                '''
            )
            cursor.execute('CREATE INDEX IF NOT EXISTS ix_hydration_prompt_user_due ON hydration_prompt (user_id, due_at)')


        if 'pomodoro_session' in tables:
            _ensure_column(cursor, 'pomodoro_session', 'activity_label', 'activity_label VARCHAR(200)')

        if 'activity_entry' not in tables:
            cursor.execute(
                '''
                CREATE TABLE activity_entry (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    entry_type VARCHAR(50) NOT NULL,
                    title VARCHAR(200) NOT NULL,
                    description TEXT,
                    event_at DATETIME NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES user (id)
                )
                '''
            )
            cursor.execute('CREATE INDEX IF NOT EXISTS ix_activity_entry_user_event_at ON activity_entry (user_id, event_at)')

        conn.commit()


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    instance_path = Path(app.instance_path)
    instance_path.mkdir(parents=True, exist_ok=True)

    app.config.update(
        SECRET_KEY='dev-change-this-secret-key',
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{instance_path / 'wellhabit.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )

    run_lightweight_migrations(app)

    db.init_app(app)
    login_manager.init_app(app)

    from . import routes  # noqa: F401
    from .models import User  # noqa: F401

    with app.app_context():
        db.create_all()

    routes.register_routes(app)
    return app
