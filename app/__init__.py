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

        if 'daily_log' in tables:
            _ensure_column(cursor, 'daily_log', 'journal_text', 'journal_text TEXT')
            _ensure_column(cursor, 'daily_log', 'activity_text', 'activity_text TEXT')
            _ensure_column(cursor, 'daily_log', 'ai_meal_detected', 'ai_meal_detected BOOLEAN NOT NULL DEFAULT 0')
            _ensure_column(cursor, 'daily_log', 'ai_meal_confidence', "ai_meal_confidence VARCHAR(20)")
            _ensure_column(cursor, 'daily_log', 'ai_feedback', 'ai_feedback TEXT')
            _ensure_column(cursor, 'daily_log', 'last_meal_detected_at', 'last_meal_detected_at DATETIME')

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
