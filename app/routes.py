from __future__ import annotations

from calendar import Calendar
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

from flask import flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from . import db
from .ai_services import analyze_meal_text, convert_drink_amount_to_ml
from .models import DailyLog, HydrationPrompt, PomodoroSession, Task, User

LOCAL_TZ = ZoneInfo('America/Los_Angeles')


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def local_today() -> date:
    return local_now().date()


def _parse_date(value: str, fallback: date | None = None) -> date:
    if not value:
        return fallback or local_today()
    return datetime.strptime(value, '%Y-%m-%d').date()


def _month_grid(year: int, month: int, tasks):
    cal = Calendar(firstweekday=0)
    task_map: dict[date, list[Task]] = {}
    for task in tasks:
        task_map.setdefault(task.task_date, []).append(task)

    weeks = []
    for week in cal.monthdatescalendar(year, month):
        week_days = []
        for day in week:
            week_days.append({
                'date': day,
                'in_month': day.month == month,
                'tasks': task_map.get(day, []),
                'is_today': day == local_today(),
            })
        weeks.append(week_days)
    return weeks


def _get_or_create_log_for_today(user_id: int) -> DailyLog:
    today = local_today()
    log = DailyLog.query.filter_by(user_id=user_id, log_date=today).first()
    if not log:
        log = DailyLog(user_id=user_id, log_date=today)
        db.session.add(log)
        db.session.flush()
    return log




def _normalize_beverage(beverage: str, custom_beverage: str = '') -> str:
    beverage_value = (beverage or 'water').strip().lower()
    custom_value = (custom_beverage or '').strip()
    if beverage_value == 'other':
        return custom_value or 'water'
    return beverage_value or 'water'


def _create_or_refresh_todo(user_id: int, title: str, task_date: date, description: str = '') -> Task:
    task = Task.query.filter_by(user_id=user_id, task_date=task_date, title=title).first()
    if not task:
        task = Task(user_id=user_id, title=title, description=description, task_date=task_date, completed=False)
        db.session.add(task)
    else:
        task.description = description or task.description
        task.completed = False
    db.session.flush()
    return task


def _ensure_daily_morning_prompt(user_id: int) -> HydrationPrompt | None:
    today = local_today()
    existing = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user_id,
        HydrationPrompt.prompt_type == 'morning',
        db.func.date(HydrationPrompt.due_at) == today,
    ).first()
    return existing


def _create_meal_hydration_prompts(user_id: int, log: DailyLog, meal_time: datetime) -> tuple[HydrationPrompt, HydrationPrompt]:
    immediate = HydrationPrompt.query.filter_by(user_id=user_id, log_id=log.id, prompt_type='meal_now').first()
    followup = HydrationPrompt.query.filter_by(user_id=user_id, log_id=log.id, prompt_type='meal_plus_2h').first()

    if not immediate:
        immediate = HydrationPrompt(
            user_id=user_id,
            log_id=log.id,
            prompt_type='meal_now',
            due_at=meal_time,
            message='Better to drink a glass of water after eating.',
            response_status='pending',
        )
        db.session.add(immediate)

    if not followup:
        followup = HydrationPrompt(
            user_id=user_id,
            log_id=log.id,
            prompt_type='meal_plus_2h',
            due_at=meal_time + timedelta(hours=2),
            message='It has been about 2 hours since your meal. Better to drink a glass of water.',
            response_status='pending',
        )
        db.session.add(followup)

    db.session.flush()
    session['open_hydration_prompt_id'] = immediate.id
    return immediate, followup


def _get_due_and_upcoming_prompt(user_id: int):
    now = local_now().replace(tzinfo=None)
    due_prompt = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user_id,
        HydrationPrompt.response_status.in_(['pending', 'not_yet']),
        HydrationPrompt.due_at <= now,
    ).order_by(HydrationPrompt.due_at.asc()).first()

    upcoming_prompt = HydrationPrompt.query.filter(
        HydrationPrompt.user_id == user_id,
        HydrationPrompt.response_status.in_(['pending', 'not_yet']),
        HydrationPrompt.due_at > now,
    ).order_by(HydrationPrompt.due_at.asc()).first()

    open_prompt_id = session.pop('open_hydration_prompt_id', None)
    if open_prompt_id:
        chosen = HydrationPrompt.query.filter_by(id=open_prompt_id, user_id=user_id).first()
        if chosen:
            due_prompt = chosen

    return due_prompt, upcoming_prompt


def _serialize_prompt(prompt: HydrationPrompt | None):
    if not prompt:
        return None
    return {
        'id': prompt.id,
        'prompt_type': prompt.prompt_type,
        'message': prompt.message,
        'due_at_iso': prompt.due_at.isoformat() if prompt.due_at else None,
        'response_status': prompt.response_status,
        'beverage': prompt.beverage,
    }


def register_routes(app):
    @app.context_processor
    def inject_nav_context():
        return {
            'nav_local_date': local_now().strftime('%A, %B %d, %Y'),
            'nav_local_time': local_now().strftime('%I:%M %p'),
        }

    @app.route('/')
    def index():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        return render_template('index.html')

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            confirm_password = request.form.get('confirm_password', '')

            if not username or not email or not password:
                flash('Please fill in all required fields.', 'danger')
                return render_template('register.html')
            if password != confirm_password:
                flash('Passwords do not match.', 'danger')
                return render_template('register.html')
            if User.query.filter((User.username == username) | (User.email == email)).first():
                flash('Username or email already exists.', 'danger')
                return render_template('register.html')

            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash('Account created successfully. Please log in.', 'success')
            return redirect(url_for('login'))

        return render_template('register.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            user = User.query.filter_by(email=email).first()

            if user and user.check_password(password):
                login_user(user)
                flash('Logged in successfully.', 'success')
                return redirect(url_for('dashboard'))

            flash('Invalid email or password.', 'danger')

        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        flash('You have been logged out.', 'info')
        return redirect(url_for('login'))

    @app.route('/dashboard')
    @login_required
    def dashboard():
        today = local_today()
        today_tasks = Task.query.filter_by(user_id=current_user.id, task_date=today).order_by(Task.completed.asc(), Task.created_at.asc()).all()
        recent_logs = DailyLog.query.filter_by(user_id=current_user.id).order_by(DailyLog.log_date.desc()).limit(5).all()
        today_sessions = PomodoroSession.query.filter(
            PomodoroSession.user_id == current_user.id,
            db.func.date(PomodoroSession.completed_at) == today
        ).order_by(PomodoroSession.completed_at.desc()).all()

        due_prompt, upcoming_prompt = _get_due_and_upcoming_prompt(current_user.id)
        morning_prompt_record = _ensure_daily_morning_prompt(current_user.id)

        today_focus_count = len(today_sessions)
        today_focus_minutes = sum(session.focus_minutes for session in today_sessions)

        return render_template(
            'dashboard.html',
            today_tasks=today_tasks,
            recent_logs=recent_logs,
            today_focus_count=today_focus_count,
            today_focus_minutes=today_focus_minutes,
            today=today,
            due_hydration_prompt=_serialize_prompt(due_prompt),
            upcoming_hydration_prompt=_serialize_prompt(upcoming_prompt),
            morning_prompt_exists=bool(morning_prompt_record),
        )

    @app.route('/logs', methods=['GET', 'POST'])
    @login_required
    def logs():
        if request.method == 'POST':
            log_date = _parse_date(request.form.get('log_date'))
            water_amount = request.form.get('water_ml') or '0'
            water_conversion = convert_drink_amount_to_ml('water', water_amount)
            water_ml = water_conversion['amount_ml']
            sleep_hours = float(request.form.get('sleep_hours') or 0)
            sleep_quality = request.form.get('sleep_quality', 'Average')
            steps = int(request.form.get('steps') or 0)
            exercise_minutes = int(request.form.get('exercise_minutes') or 0)
            notes = request.form.get('notes', '').strip()
            journal_text = request.form.get('journal_text', '').strip()
            activity_text = request.form.get('activity_text', '').strip()

            existing = DailyLog.query.filter_by(user_id=current_user.id, log_date=log_date).first()
            if existing:
                log = existing
                flash_message = 'Daily record updated.'
            else:
                log = DailyLog(user_id=current_user.id, log_date=log_date)
                db.session.add(log)
                flash_message = 'Daily record saved.'

            log.water_ml = water_ml
            log.sleep_hours = sleep_hours
            log.sleep_quality = sleep_quality
            log.steps = steps
            log.exercise_minutes = exercise_minutes
            log.notes = notes
            log.journal_text = journal_text
            log.activity_text = activity_text
            log.ai_meal_detected = False
            log.ai_meal_confidence = None
            log.ai_feedback = None

            text_for_ai = ' '.join(part for part in [activity_text, journal_text] if part).strip()
            analysis = analyze_meal_text(text_for_ai)
            if analysis.get('ate_meal'):
                meal_time = local_now().replace(tzinfo=None)
                log.ai_meal_detected = True
                log.ai_meal_confidence = analysis.get('confidence')
                log.ai_feedback = analysis.get('reason')
                log.last_meal_detected_at = meal_time
                db.session.flush()
                _create_meal_hydration_prompts(current_user.id, log, meal_time)
                flash('Meal detected from your latest note. Better to drink a glass of water.', 'warning')
            elif text_for_ai:
                log.ai_feedback = analysis.get('reason')
                log.ai_meal_confidence = analysis.get('confidence')

            db.session.commit()
            flash(flash_message, 'success')
            return redirect(url_for('logs', date=log_date.isoformat()))

        selected_date = _parse_date(request.args.get('date'), fallback=local_today())
        current_log = DailyLog.query.filter_by(user_id=current_user.id, log_date=selected_date).first()
        all_logs = DailyLog.query.filter_by(user_id=current_user.id).order_by(DailyLog.log_date.desc()).all()
        due_prompt, upcoming_prompt = _get_due_and_upcoming_prompt(current_user.id)
        morning_prompt_record = _ensure_daily_morning_prompt(current_user.id)
        return render_template(
            'logs.html',
            current_log=current_log,
            selected_date=selected_date,
            all_logs=all_logs,
            due_hydration_prompt=_serialize_prompt(due_prompt),
            upcoming_hydration_prompt=_serialize_prompt(upcoming_prompt),
            morning_prompt_exists=bool(morning_prompt_record),
        )

    @app.route('/calendar')
    @login_required
    def calendar_view():
        today = local_today()
        year = int(request.args.get('year') or today.year)
        month = int(request.args.get('month') or today.month)

        if month < 1:
            month = 12
            year -= 1
        elif month > 12:
            month = 1
            year += 1

        month_start = date(year, month, 1)
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)

        month_tasks = Task.query.filter(
            Task.user_id == current_user.id,
            Task.task_date >= month_start,
            Task.task_date < next_month,
        ).order_by(Task.task_date.asc(), Task.created_at.asc()).all()

        weeks = _month_grid(year, month, month_tasks)

        prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
        next_year, next_month_num = (year + 1, 1) if month == 12 else (year, month + 1)

        return render_template(
            'calendar.html',
            weeks=weeks,
            display_date=month_start,
            prev_year=prev_year,
            prev_month=prev_month,
            next_year=next_year,
            next_month=next_month_num,
        )

    @app.route('/tasks/add', methods=['POST'])
    @login_required
    def add_task():
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        task_date = _parse_date(request.form.get('task_date'))

        if not title:
            flash('Task title is required.', 'danger')
            return redirect(request.referrer or url_for('calendar_view'))

        task = Task(user_id=current_user.id, title=title, description=description, task_date=task_date)
        db.session.add(task)
        db.session.commit()
        flash('Task added successfully.', 'success')
        return redirect(request.referrer or url_for('calendar_view'))

    @app.route('/tasks/<int:task_id>/toggle', methods=['POST'])
    @login_required
    def toggle_task(task_id):
        task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
        task.completed = not task.completed
        db.session.commit()
        flash('Task updated.', 'success')
        return redirect(request.referrer or url_for('calendar_view'))

    @app.route('/tasks/<int:task_id>/delete', methods=['POST'])
    @login_required
    def delete_task(task_id):
        task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
        db.session.delete(task)
        db.session.commit()
        flash('Task deleted.', 'info')
        return redirect(request.referrer or url_for('calendar_view'))

    @app.route('/pomodoro/save', methods=['POST'])
    @login_required
    def save_pomodoro():
        data = request.get_json(silent=True) or {}
        focus_minutes = int(data.get('focus_minutes') or 25)
        break_minutes = int(data.get('break_minutes') or 5)
        cycle_number = int(data.get('cycle_number') or 1)

        session_row = PomodoroSession(
            user_id=current_user.id,
            focus_minutes=focus_minutes,
            break_minutes=break_minutes,
            cycle_number=cycle_number,
            completed_at=local_now().replace(tzinfo=None),
        )
        db.session.add(session_row)
        db.session.commit()
        return jsonify({'message': 'Pomodoro session saved.'})

    @app.route('/hydration/respond', methods=['POST'])
    @login_required
    def hydration_respond():
        data = request.get_json(silent=True) or request.form
        prompt_id = data.get('prompt_id')
        beverage = (data.get('beverage') or 'water').strip()
        custom_beverage = (data.get('custom_beverage') or '').strip()
        response_status = (data.get('response_status') or 'done').strip()
        prompt_type = (data.get('prompt_type') or 'morning').strip()
        amount_text = (data.get('amount_text') or '').strip()

        if beverage == 'other' and not custom_beverage:
            return jsonify({'ok': False, 'message': 'Please type the beverage name for Other.'}), 400

        resolved_beverage = _normalize_beverage(beverage, custom_beverage)
        amount_conversion = convert_drink_amount_to_ml(resolved_beverage, amount_text)
        amount_ml = amount_conversion['amount_ml']

        if prompt_id:
            prompt = HydrationPrompt.query.filter_by(id=int(prompt_id), user_id=current_user.id).first_or_404()
        else:
            today = local_today()
            prompt = HydrationPrompt.query.filter(
                HydrationPrompt.user_id == current_user.id,
                HydrationPrompt.prompt_type == prompt_type,
                db.func.date(HydrationPrompt.due_at) == today,
            ).first()
            if not prompt:
                prompt = HydrationPrompt(
                    user_id=current_user.id,
                    prompt_type=prompt_type,
                    due_at=local_now().replace(tzinfo=None),
                    message='Drink a glass of water to begin the day.' if prompt_type == 'morning' else 'Better to drink a glass of water.',
                )
                db.session.add(prompt)

        prompt.beverage = resolved_beverage
        prompt.custom_beverage = None
        prompt.responded_at = local_now().replace(tzinfo=None)

        today_log = _get_or_create_log_for_today(current_user.id)
        existing_note = today_log.notes or ''

        if response_status == 'done':
            prompt.response_status = 'done'
            today_log.water_ml = (today_log.water_ml or 0) + amount_ml
            hydration_note = f"Hydration response: {prompt.prompt_type} / {resolved_beverage} / done / {amount_ml} ml."
        elif response_status == 'not_yet':
            prompt.response_status = 'todo_added'
            task_amount_text = amount_text or 'a glass'
            todo_title = f"Drink {task_amount_text} of {resolved_beverage}"
            todo_description = f"Added from the {prompt.prompt_type.replace('_', ' ')} hydration reminder. Target: {amount_ml} ml."
            _create_or_refresh_todo(current_user.id, todo_title, local_today(), todo_description)
            hydration_note = f"Hydration response: {prompt.prompt_type} / {resolved_beverage} / added to todo / target {amount_ml} ml."
        else:
            prompt.response_status = 'skipped'
            hydration_note = f"Hydration response: {prompt.prompt_type} / {resolved_beverage} / skipped."

        today_log.notes = (existing_note + '\n' + hydration_note).strip()

        db.session.commit()
        return jsonify({
            'ok': True,
            'message': 'Hydration response saved.',
            'water_ml': today_log.water_ml,
            'amount_ml': amount_ml,
            'beverage': resolved_beverage,
            'conversion_source': amount_conversion['source'],
        })
