from calendar import Calendar
from datetime import date, datetime

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from . import db
from .models import DailyLog, PomodoroSession, Task, User


def _parse_date(value: str, fallback: date | None = None) -> date:
    if not value:
        return fallback or date.today()
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
                'is_today': day == date.today(),
            })
        weeks.append(week_days)
    return weeks


def register_routes(app):
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
        today = date.today()
        today_tasks = Task.query.filter_by(user_id=current_user.id, task_date=today).order_by(Task.completed.asc(), Task.created_at.asc()).all()
        recent_logs = DailyLog.query.filter_by(user_id=current_user.id).order_by(DailyLog.log_date.desc()).limit(5).all()
        today_sessions = PomodoroSession.query.filter(
            PomodoroSession.user_id == current_user.id,
            db.func.date(PomodoroSession.completed_at) == today
        ).order_by(PomodoroSession.completed_at.desc()).all()

        today_focus_count = len(today_sessions)
        today_focus_minutes = sum(session.focus_minutes for session in today_sessions)

        return render_template(
            'dashboard.html',
            today_tasks=today_tasks,
            recent_logs=recent_logs,
            today_focus_count=today_focus_count,
            today_focus_minutes=today_focus_minutes,
            today=today,
        )

    @app.route('/logs', methods=['GET', 'POST'])
    @login_required
    def logs():
        if request.method == 'POST':
            log_date = _parse_date(request.form.get('log_date'))
            water_ml = int(request.form.get('water_ml') or 0)
            sleep_hours = float(request.form.get('sleep_hours') or 0)
            sleep_quality = request.form.get('sleep_quality', 'Average')
            steps = int(request.form.get('steps') or 0)
            exercise_minutes = int(request.form.get('exercise_minutes') or 0)
            notes = request.form.get('notes', '').strip()

            existing = DailyLog.query.filter_by(user_id=current_user.id, log_date=log_date).first()
            if existing:
                existing.water_ml = water_ml
                existing.sleep_hours = sleep_hours
                existing.sleep_quality = sleep_quality
                existing.steps = steps
                existing.exercise_minutes = exercise_minutes
                existing.notes = notes
                flash('Daily record updated.', 'success')
            else:
                new_log = DailyLog(
                    user_id=current_user.id,
                    log_date=log_date,
                    water_ml=water_ml,
                    sleep_hours=sleep_hours,
                    sleep_quality=sleep_quality,
                    steps=steps,
                    exercise_minutes=exercise_minutes,
                    notes=notes,
                )
                db.session.add(new_log)
                flash('Daily record saved.', 'success')

            db.session.commit()
            return redirect(url_for('logs'))

        selected_date = _parse_date(request.args.get('date'), fallback=date.today())
        current_log = DailyLog.query.filter_by(user_id=current_user.id, log_date=selected_date).first()
        all_logs = DailyLog.query.filter_by(user_id=current_user.id).order_by(DailyLog.log_date.desc()).all()
        return render_template('logs.html', current_log=current_log, selected_date=selected_date, all_logs=all_logs)

    @app.route('/calendar')
    @login_required
    def calendar_view():
        today = date.today()
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

        session = PomodoroSession(
            user_id=current_user.id,
            focus_minutes=focus_minutes,
            break_minutes=break_minutes,
            cycle_number=cycle_number,
        )
        db.session.add(session)
        db.session.commit()
        return jsonify({'message': 'Pomodoro session saved.'})
