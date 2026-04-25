from pathlib import Path
import logging
import os
import sqlite3
import secrets
from logging.handlers import RotatingFileHandler

from flask import Flask, Response, g, jsonify, redirect, request, url_for
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import SQLAlchemyError
from flask_wtf.csrf import CSRFError, CSRFProtect

from .constants import DEFAULT_LOG_LEVEL, EYE_EXERCISE_THRESHOLD_MINUTES, LOG_BACKUP_COUNT, LOG_MAX_BYTES


db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'warning'


def _resolve_secret_key() -> tuple[str, bool]:
    configured = (os.getenv('SECRET_KEY') or os.getenv('FLASK_SECRET_KEY') or '').strip()
    if configured:
        return configured, False

    return 'dev-only-insecure-key', True


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


def _ensure_index(cursor: sqlite3.Cursor, sql: str) -> None:
    """Idempotently create an index. The provided SQL must use
    ``CREATE INDEX IF NOT EXISTS`` so repeated calls are safe."""
    cursor.execute(sql)


def _first_non_empty(rows: list[sqlite3.Row], key: str):
    for row in rows:
        value = row[key]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _dedupe_daily_logs(cursor: sqlite3.Cursor) -> None:
    duplicate_groups = list(cursor.execute(
        '''
        SELECT user_id, log_date
        FROM daily_log
        GROUP BY user_id, log_date
        HAVING COUNT(*) > 1
        '''
    ))
    for user_id, log_date in duplicate_groups:
        rows = [
            {
                'id': row[0],
                'water_ml': row[1],
                'sleep_hours': row[2],
                'steps': row[3],
                'exercise_minutes': row[4],
                'notes': row[5],
                'journal_text': row[6],
                'mood_label': row[7],
                'mood_custom_text': row[8],
                'activity_text': row[9],
                'ai_meal_detected': row[10],
                'ai_meal_confidence': row[11],
                'ai_feedback': row[12],
                'last_meal_detected_at': row[13],
            }
            for row in cursor.execute(
                '''
                SELECT id, water_ml, sleep_hours, steps, exercise_minutes,
                       notes, journal_text, mood_label, mood_custom_text, activity_text,
                       ai_meal_detected, ai_meal_confidence, ai_feedback, last_meal_detected_at
                FROM daily_log
                WHERE user_id = ? AND log_date = ?
                ORDER BY id ASC
                ''',
                (user_id, log_date),
            )
        ]
        if len(rows) < 2:
            continue
        keep_id = rows[0]['id']
        duplicate_ids = [row['id'] for row in rows[1:]]
        total_water_ml = sum(int(row['water_ml'] or 0) for row in rows)
        total_sleep_hours = sum(float(row['sleep_hours'] or 0) for row in rows)
        total_steps = sum(int(row['steps'] or 0) for row in rows)
        total_exercise_minutes = sum(int(row['exercise_minutes'] or 0) for row in rows)
        notes = _first_non_empty(rows, 'notes')
        journal_text = _first_non_empty(rows, 'journal_text')
        mood_label = _first_non_empty(rows, 'mood_label')
        mood_custom_text = _first_non_empty(rows, 'mood_custom_text')
        activity_text = _first_non_empty(rows, 'activity_text')
        ai_meal_detected = 1 if any(int(row['ai_meal_detected'] or 0) for row in rows) else 0
        ai_meal_confidence = _first_non_empty(rows, 'ai_meal_confidence')
        ai_feedback = _first_non_empty(rows, 'ai_feedback')
        last_meal_detected_at = _first_non_empty(rows, 'last_meal_detected_at')

        cursor.execute(
            '''
            UPDATE daily_log
            SET water_ml = ?,
                sleep_hours = ?,
                steps = ?,
                exercise_minutes = ?,
                notes = ?,
                journal_text = ?,
                mood_label = ?,
                mood_custom_text = ?,
                activity_text = ?,
                ai_meal_detected = ?,
                ai_meal_confidence = ?,
                ai_feedback = ?,
                last_meal_detected_at = ?
            WHERE id = ?
            ''',
            (
                total_water_ml,
                total_sleep_hours,
                total_steps,
                total_exercise_minutes,
                notes,
                journal_text,
                mood_label,
                mood_custom_text,
                activity_text,
                ai_meal_detected,
                ai_meal_confidence,
                ai_feedback,
                last_meal_detected_at,
                keep_id,
            ),
        )
        placeholders = ','.join('?' for _ in duplicate_ids)
        if cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hydration_prompt'").fetchone():
            cursor.execute(f'UPDATE hydration_prompt SET log_id = ? WHERE log_id IN ({placeholders})', (keep_id, *duplicate_ids))
        if cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mood_entry'").fetchone():
            cursor.execute(f'UPDATE mood_entry SET log_id = ? WHERE log_id IN ({placeholders})', (keep_id, *duplicate_ids))
        cursor.execute(f'DELETE FROM daily_log WHERE id IN ({placeholders})', duplicate_ids)


def _ensure_daily_log_unique_index(cursor: sqlite3.Cursor) -> None:
    _dedupe_daily_logs(cursor)
    _ensure_index(cursor, 'CREATE UNIQUE INDEX IF NOT EXISTS ux_daily_log_user_date ON daily_log (user_id, log_date)')


def _migrate_legacy_mood_labels(cursor: sqlite3.Cursor) -> None:
    mood_map = {
        '😁': 'happy', '😄': 'happy', '🤩': 'happy', '🥳': 'happy', '😊': 'happy',
        '😌': 'calm', '🥹': 'calm', '🌤️': 'calm',
        '🙂': 'normal', '😶': 'normal',
        '😴': 'exhausted', '🥱': 'exhausted', '😮‍💨': 'exhausted',
        '😢': 'sad', '😭': 'sad', '💔': 'sad',
        '😰': 'anxious', '😟': 'anxious',
        '😣': 'stressed', '😤': 'stressed', '😡': 'stressed', '😵‍💫': 'stressed', '🤯': 'stressed',
        'overwhelmed': 'stressed', 'mixed': 'stressed', 'hopeful': 'calm',
    }
    for table_name in ('daily_log', 'mood_entry'):
        columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table_name})")}
        if 'mood_label' not in columns:
            continue
        for old_value, new_value in mood_map.items():
            cursor.execute(
                f"UPDATE {table_name} SET mood_label = ? WHERE mood_label = ?",
                (new_value, old_value),
            )


def run_lightweight_migrations(app: Flask) -> None:
    db_path = _get_sqlite_db_path(app)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.execute('PRAGMA journal_mode=WAL')

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
            _ensure_column(cursor, 'user', 'goal_progress_intensity', "goal_progress_intensity VARCHAR(12) NOT NULL DEFAULT 'medium'")
            _ensure_column(cursor, 'user', 'optimal_bedtime', "optimal_bedtime VARCHAR(5)")
            _ensure_column(cursor, 'user', 'optimal_wake_time', "optimal_wake_time VARCHAR(5)")
            _ensure_column(cursor, 'user', 'hydration_wake_time', "hydration_wake_time VARCHAR(5)")
            _ensure_column(cursor, 'user', 'hydration_breakfast_time', "hydration_breakfast_time VARCHAR(5)")
            _ensure_column(cursor, 'user', 'hydration_lunch_time', "hydration_lunch_time VARCHAR(5)")
            _ensure_column(cursor, 'user', 'hydration_dinner_time', "hydration_dinner_time VARCHAR(5)")
            _ensure_column(cursor, 'user', 'hydration_score', 'hydration_score INTEGER NOT NULL DEFAULT 50')
            _ensure_column(cursor, 'user', 'energy_score', 'energy_score INTEGER NOT NULL DEFAULT 50')
            _ensure_column(cursor, 'user', 'fitness_score', 'fitness_score INTEGER NOT NULL DEFAULT 50')
            _ensure_column(cursor, 'user', 'focus_score', 'focus_score INTEGER NOT NULL DEFAULT 50')
            _ensure_column(cursor, 'user', 'mood_score', 'mood_score INTEGER NOT NULL DEFAULT 50')
            _ensure_column(cursor, 'user', 'overall_wellness_score', 'overall_wellness_score INTEGER NOT NULL DEFAULT 50')
            _ensure_column(cursor, 'user', 'avatar_emoji', "avatar_emoji VARCHAR(16) NOT NULL DEFAULT '🙂'")
            _ensure_column(cursor, 'user', 'wellness_summary', 'wellness_summary TEXT')
            _ensure_column(cursor, 'user', 'wellness_updated_at', 'wellness_updated_at DATETIME')
            _ensure_column(cursor, 'user', 'last_task_rollover_on', 'last_task_rollover_on DATE')
            _ensure_column(cursor, 'user', 'last_activity_pruned_at', 'last_activity_pruned_at DATETIME')

        if 'daily_log' in tables:
            _ensure_column(cursor, 'daily_log', 'journal_text', 'journal_text TEXT')
            _ensure_column(cursor, 'daily_log', 'mood_label', "mood_label VARCHAR(40)")
            _ensure_column(cursor, 'daily_log', 'mood_custom_text', "mood_custom_text VARCHAR(120)")
            _ensure_column(cursor, 'daily_log', 'activity_text', 'activity_text TEXT')
            _ensure_column(cursor, 'daily_log', 'ai_meal_detected', 'ai_meal_detected BOOLEAN NOT NULL DEFAULT 0')
            _ensure_column(cursor, 'daily_log', 'ai_meal_confidence', "ai_meal_confidence VARCHAR(20)")
            _ensure_column(cursor, 'daily_log', 'ai_feedback', 'ai_feedback TEXT')
            _ensure_column(cursor, 'daily_log', 'last_meal_detected_at', 'last_meal_detected_at DATETIME')
            _migrate_legacy_mood_labels(cursor)
            _ensure_index(cursor, 'CREATE INDEX IF NOT EXISTS ix_daily_log_user_date ON daily_log (user_id, log_date)')
            _ensure_daily_log_unique_index(cursor)
            # sleep_quality was never editable in the UI. Drop it so new inserts
            # don't have to satisfy a NOT NULL column that nothing writes to.
            # SQLite 3.35+ supports DROP COLUMN; older versions are tolerated.
            daily_log_columns = {row[1] for row in cursor.execute("PRAGMA table_info(daily_log)")}
            if 'sleep_quality' in daily_log_columns:
                try:
                    cursor.execute('ALTER TABLE daily_log DROP COLUMN sleep_quality')
                except sqlite3.OperationalError:
                    # Older SQLite: leave the legacy column sitting there; it has a
                    # DB-side default so new inserts from the ORM (which no longer
                    # mentions the column) will still satisfy NOT NULL.
                    pass

        if 'task' in tables:
            _ensure_column(cursor, 'task', 'sort_order', 'sort_order INTEGER NOT NULL DEFAULT 0')
            _ensure_column(cursor, 'task', 'completed_at', 'completed_at DATETIME')
            _ensure_column(cursor, 'task', 'task_type', "task_type VARCHAR(30) NOT NULL DEFAULT 'regular'")
            _ensure_column(cursor, 'task', 'auto_tracked_water_ml', 'auto_tracked_water_ml INTEGER NOT NULL DEFAULT 0')
            _ensure_column(cursor, 'task', 'ai_generated_source', 'ai_generated_source VARCHAR(30)')
            _ensure_column(cursor, 'task', 'ai_suggestion_key', 'ai_suggestion_key VARCHAR(40)')
            _ensure_column(cursor, 'task', 'ai_followup_question', 'ai_followup_question VARCHAR(240)')
            _ensure_column(cursor, 'task', 'ai_followup_rating', 'ai_followup_rating INTEGER')
            _ensure_column(cursor, 'task', 'ai_followup_completed_at', 'ai_followup_completed_at DATETIME')
            cursor.execute('UPDATE task SET sort_order = id WHERE sort_order IS NULL OR sort_order = 0')
            cursor.execute("UPDATE task SET task_type = 'regular' WHERE task_type IS NULL OR task_type = ''")
            _ensure_index(cursor, 'CREATE INDEX IF NOT EXISTS ix_task_user_date_sort ON task (user_id, task_date, sort_order)')
            _ensure_index(cursor, 'CREATE INDEX IF NOT EXISTS ix_task_ai_suggestion_lookup ON task (user_id, task_type, ai_suggestion_key, completed, created_at)')

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

        if 'hydration_prompt' in tables:
            _ensure_index(cursor, 'CREATE INDEX IF NOT EXISTS ix_hydration_prompt_user_type_status_due ON hydration_prompt (user_id, prompt_type, response_status, due_at)')

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

        if 'activity_entry' in tables:
            _ensure_index(cursor, 'CREATE INDEX IF NOT EXISTS ix_activity_entry_user_type_event_at ON activity_entry (user_id, entry_type, event_at)')

        if 'client_state' not in tables:
            cursor.execute(
                '''
                CREATE TABLE client_state (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    state_key VARCHAR(40) NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES user (id)
                )
                '''
            )
            cursor.execute('CREATE INDEX IF NOT EXISTS ix_client_state_user_key_created ON client_state (user_id, state_key, created_at)')
            cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS ux_client_state_user_key ON client_state (user_id, state_key)')

        if 'client_state' in tables:
            _ensure_index(cursor, 'CREATE INDEX IF NOT EXISTS ix_client_state_user_key_created ON client_state (user_id, state_key, created_at)')
            _ensure_index(cursor, 'CREATE UNIQUE INDEX IF NOT EXISTS ux_client_state_user_key ON client_state (user_id, state_key)')

        if 'mood_entry' not in tables:
            cursor.execute(
                '''
                CREATE TABLE mood_entry (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    log_id INTEGER,
                    source VARCHAR(30) NOT NULL DEFAULT 'journal',
                    mood_label VARCHAR(40) NOT NULL,
                    mood_custom_text VARCHAR(120),
                    mood_value INTEGER NOT NULL DEFAULT 50,
                    summary TEXT,
                    detected_by VARCHAR(20) NOT NULL DEFAULT 'user',
                    event_at DATETIME NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES user (id),
                    FOREIGN KEY(log_id) REFERENCES daily_log (id)
                )
                '''
            )
            cursor.execute('CREATE INDEX IF NOT EXISTS ix_mood_entry_user_event_at ON mood_entry (user_id, event_at)')

        if 'mood_entry' in tables:
            _ensure_index(cursor, 'CREATE INDEX IF NOT EXISTS ix_mood_entry_user_source_event_at ON mood_entry (user_id, source, event_at)')
            _migrate_legacy_mood_labels(cursor)

        if 'eye_exercise_prompt' not in tables:
            cursor.execute(
                f'''
                CREATE TABLE eye_exercise_prompt (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    focus_minutes_trigger INTEGER NOT NULL DEFAULT {EYE_EXERCISE_THRESHOLD_MINUTES},
                    threshold_minutes INTEGER NOT NULL DEFAULT {EYE_EXERCISE_THRESHOLD_MINUTES},
                    video_url VARCHAR(300) NOT NULL DEFAULT 'https://www.youtube.com/watch?v=iVb4vUp70zY',
                    response_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    due_at DATETIME NOT NULL,
                    created_at DATETIME NOT NULL,
                    responded_at DATETIME,
                    FOREIGN KEY(user_id) REFERENCES user (id)
                )
                '''
            )
            cursor.execute('CREATE INDEX IF NOT EXISTS ix_eye_exercise_prompt_user_due ON eye_exercise_prompt (user_id, due_at)')

        if 'eye_exercise_prompt' in tables:
            _ensure_index(cursor, 'CREATE INDEX IF NOT EXISTS ix_eye_exercise_prompt_user_status_due ON eye_exercise_prompt (user_id, response_status, due_at)')

        if 'eye_exercise_state' not in tables:
            cursor.execute(
                '''
                CREATE TABLE eye_exercise_state (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL UNIQUE,
                    carry_focus_minutes INTEGER NOT NULL DEFAULT 0,
                    active_prompt_id INTEGER,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES user (id),
                    FOREIGN KEY(active_prompt_id) REFERENCES eye_exercise_prompt (id)
                )
                '''
            )
            cursor.execute('CREATE INDEX IF NOT EXISTS ix_eye_exercise_state_user_active ON eye_exercise_state (user_id, active_prompt_id)')

        if 'eye_exercise_state' in tables:
            _ensure_column(cursor, 'eye_exercise_state', 'carry_focus_minutes', 'carry_focus_minutes INTEGER NOT NULL DEFAULT 0')
            _ensure_column(cursor, 'eye_exercise_state', 'active_prompt_id', 'active_prompt_id INTEGER')
            _ensure_column(cursor, 'eye_exercise_state', 'updated_at', 'updated_at DATETIME')
            _ensure_index(cursor, 'CREATE INDEX IF NOT EXISTS ix_eye_exercise_state_user_active ON eye_exercise_state (user_id, active_prompt_id)')

        if 'care_chat_session' not in tables:
            cursor.execute(
                '''
                CREATE TABLE care_chat_session (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    started_at DATETIME NOT NULL,
                    ended_at DATETIME,
                    last_activity_at DATETIME NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(user_id) REFERENCES user (id)
                )
                '''
            )
        if 'care_chat_session' in tables or 'care_chat_session' not in tables:
            _ensure_index(cursor, 'CREATE INDEX IF NOT EXISTS ix_care_chat_session_user_ended_at ON care_chat_session (user_id, ended_at)')

        if 'care_chat_message' not in tables:
            cursor.execute(
                '''
                CREATE TABLE care_chat_message (
                    id INTEGER NOT NULL PRIMARY KEY,
                    session_id VARCHAR(32) NOT NULL,
                    role VARCHAR(20) NOT NULL,
                    content TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES care_chat_session (id)
                )
                '''
            )
        if 'care_chat_message' in tables or 'care_chat_message' not in tables:
            _ensure_index(cursor, 'CREATE INDEX IF NOT EXISTS ix_care_chat_message_session_created ON care_chat_message (session_id, created_at)')


        if 'break_session' not in tables:
            cursor.execute(
                '''
                CREATE TABLE break_session (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    started_at DATETIME NOT NULL,
                    ended_at DATETIME,
                    trigger VARCHAR(30) NOT NULL DEFAULT 'manual',
                    exercises_done TEXT NOT NULL DEFAULT '[]',
                    self_report VARCHAR(30),
                    fatigue_signal_snapshot TEXT,
                    FOREIGN KEY(user_id) REFERENCES user (id)
                )
                '''
            )
            cursor.execute('CREATE INDEX IF NOT EXISTS ix_break_session_user_started_at ON break_session (user_id, started_at)')

        if 'break_session' in tables:
            _ensure_index(cursor, 'CREATE INDEX IF NOT EXISTS ix_break_session_user_started_at ON break_session (user_id, started_at)')

        conn.commit()


def configure_logging(app: Flask) -> None:
    log_level_name = str(os.getenv('LOG_LEVEL') or app.config.get('LOG_LEVEL') or DEFAULT_LOG_LEVEL).upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    logs_dir = Path(app.instance_path) / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / 'wellhabit.log'

    formatter = logging.Formatter(
        '%(asctime)s %(levelname)s [%(name)s] %(message)s'
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    file_handler_present = any(
        isinstance(handler, RotatingFileHandler) and Path(getattr(handler, 'baseFilename', '')) == log_path
        for handler in root_logger.handlers
    )
    if not file_handler_present:
        file_handler = RotatingFileHandler(log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding='utf-8')
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    stream_handler_present = any(getattr(handler, '_wellhabit_stream', False) for handler in root_logger.handlers)
    if not stream_handler_present:
        stream_handler = logging.StreamHandler()
        stream_handler._wellhabit_stream = True
        stream_handler.setLevel(log_level)
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)

    app.logger.setLevel(log_level)
    app.logger.info('Logging configured', extra={'log_path': str(log_path), 'level': log_level_name})


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    instance_path = Path(app.instance_path)
    instance_path.mkdir(parents=True, exist_ok=True)
    secret_key, using_ephemeral_secret_key = _resolve_secret_key()

    app.config.update(
        SECRET_KEY=secret_key,
        SQLALCHEMY_DATABASE_URI=os.getenv('DATABASE_URL') or f"sqlite:///{instance_path / 'wellhabit.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=(os.getenv('SESSION_COOKIE_SECURE', '0') == '1'),
        SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'timeout': 15}},
        LOG_LEVEL=os.getenv('LOG_LEVEL', DEFAULT_LOG_LEVEL),
        WTF_CSRF_TIME_LIMIT=60 * 60 * 8,
        USING_EPHEMERAL_SECRET_KEY=using_ephemeral_secret_key,
    )

    configure_logging(app)
    if app.config.get('USING_EPHEMERAL_SECRET_KEY'):
        app.logger.warning('Using an ephemeral SECRET_KEY because FLASK_DEBUG=1 and no persistent secret was configured.')
    run_lightweight_migrations(app)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    @app.before_request
    def assign_csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(16)

    @app.context_processor
    def inject_csp_nonce():
        return {'csp_nonce': getattr(g, 'csp_nonce', '')}

    @app.route('/wellhabit-sw.js')
    def wellhabit_service_worker():
        sw_path = app.static_folder and os.path.join(app.static_folder, 'wellhabit_sw.js')
        if not sw_path or not os.path.exists(sw_path):
            return Response('', status=404)
        with open(sw_path, 'r', encoding='utf-8') as handle:
            response = Response(handle.read(), mimetype='application/javascript')
        response.headers['Service-Worker-Allowed'] = '/'
        response.headers['Cache-Control'] = 'public, max-age=3600'
        return response

    @app.after_request
    def apply_security_headers(response):
        nonce = getattr(g, 'csp_nonce', '')
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; script-src 'self' 'nonce-{}' 'wasm-unsafe-eval' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; img-src 'self' data: https: blob:; font-src 'self' data:; connect-src 'self' https://cdn.jsdelivr.net https://storage.googleapis.com; worker-src 'self' blob:; frame-src 'self' https://www.youtube.com https://www.youtube-nocookie.com; object-src 'none'; base-uri 'self'; form-action 'self'".format(nonce),
        )
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        return response

    from .blueprints import register_blueprints
    from .models import User  # noqa: F401

    with app.app_context():
        db.create_all()

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error: CSRFError):
        app.logger.warning('CSRF validation failed', extra={'path': request.path, 'method': request.method})
        message = getattr(error, 'description', None) or 'Security check failed. Please refresh and try again.'
        wants_json = (
            request.is_json
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or (request.accept_mimetypes.best or '').startswith('application/json')
        )
        if wants_json:
            return jsonify({'message': message}), 400
        from flask import flash
        flash(message, 'danger')
        return redirect(request.referrer or url_for('auth.login'))

    @app.errorhandler(SQLAlchemyError)
    def handle_sqlalchemy_error(error: SQLAlchemyError):
        db.session.rollback()
        app.logger.exception('Database error during request', extra={'path': request.path, 'method': request.method})
        message = 'Something went wrong while saving. Please try again.'
        wants_json = (
            request.is_json
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or (request.accept_mimetypes.best or '').startswith('application/json')
        )
        if wants_json:
            return jsonify({'message': message}), 500
        from flask import flash
        flash(message, 'danger')
        fallback_endpoint = 'main.dashboard'
        return redirect(request.referrer or url_for(fallback_endpoint))

    register_blueprints(app)
    return app
