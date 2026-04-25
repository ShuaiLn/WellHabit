from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta

from app import create_app, db
from app.models import ActivityEntry, BreakSession, CalendarEvent, DailyLog, PomodoroSession, Task, User

DEMO_USERS = [
    {
        'username': 'richdemo',
        'email': 'richdemo@example.com',
        'avatar_emoji': '🌿',
        'age': 17,
        'daily_water_goal_ml': 2200,
        'daily_sleep_goal_hours': 8.5,
        'daily_step_goal': 9000,
        'daily_exercise_goal_minutes': 30,
        'goal_progress_intensity': 'medium',
        'hydration_score': 66,
        'energy_score': 61,
        'fitness_score': 58,
        'focus_score': 72,
        'mood_score': 69,
        'overall_wellness_score': 65,
        'optimal_bedtime': '23:00',
        'optimal_wake_time': '07:30',
        'hydration_wake_time': '07:30',
        'hydration_breakfast_time': '08:30',
        'hydration_lunch_time': '12:30',
        'hydration_dinner_time': '18:30',
    },
    {
        'username': 'steadyjune',
        'email': 'steadyjune@example.com',
        'avatar_emoji': '🌙',
        'age': 21,
        'daily_water_goal_ml': 2000,
        'daily_sleep_goal_hours': 8.0,
        'daily_step_goal': 8000,
        'daily_exercise_goal_minutes': 25,
        'goal_progress_intensity': 'easy',
        'hydration_score': 59,
        'energy_score': 64,
        'fitness_score': 55,
        'focus_score': 63,
        'mood_score': 71,
        'overall_wellness_score': 62,
        'optimal_bedtime': '23:30',
        'optimal_wake_time': '07:30',
        'hydration_wake_time': '07:45',
        'hydration_breakfast_time': '08:45',
        'hydration_lunch_time': '12:30',
        'hydration_dinner_time': '18:45',
    },
    {
        'username': 'nightowl',
        'email': 'nightowl@example.com',
        'avatar_emoji': '☁️',
        'age': 19,
        'daily_water_goal_ml': 2400,
        'daily_sleep_goal_hours': 8.0,
        'daily_step_goal': 7500,
        'daily_exercise_goal_minutes': 20,
        'goal_progress_intensity': 'medium',
        'hydration_score': 54,
        'energy_score': 49,
        'fitness_score': 51,
        'focus_score': 57,
        'mood_score': 60,
        'overall_wellness_score': 54,
        'optimal_bedtime': '00:30',
        'optimal_wake_time': '08:30',
        'hydration_wake_time': '08:30',
        'hydration_breakfast_time': '09:00',
        'hydration_lunch_time': '13:00',
        'hydration_dinner_time': '19:30',
    },
]


def _seed_user(user: User, user_index: int) -> None:
    today = date.today()
    if DailyLog.query.filter_by(user_id=user.id, log_date=today).first() is None:
        db.session.add(DailyLog(
            user_id=user.id,
            log_date=today,
            water_ml=900 + (user_index * 150),
            sleep_hours=7.0 + (user_index * 0.5),
            steps=4200 + (user_index * 600),
            exercise_minutes=15 + (user_index * 5),
            journal_text='Seeded demo entry for first-run UI checks.',
            mood_label='okay',
            activity_text='Seed demo walkthrough',
        ))

    if not Task.query.filter_by(user_id=user.id, task_date=today).count():
        db.session.add_all([
            Task(user_id=user.id, task_date=today, title='Drink water', sort_order=1),
            Task(user_id=user.id, task_date=today, title='Focus block', sort_order=2),
            Task(user_id=user.id, task_date=today, title='Wind down', sort_order=3),
        ])

    if not CalendarEvent.query.filter_by(user_id=user.id, event_date=today).count():
        db.session.add(CalendarEvent(user_id=user.id, event_date=today, title='Stretch break'))
        db.session.add(CalendarEvent(user_id=user.id, event_date=today + timedelta(days=1), title='Prep tomorrow'))

    if not PomodoroSession.query.filter_by(user_id=user.id).count():
        db.session.add(PomodoroSession(user_id=user.id, focus_minutes=25, break_minutes=5, cycle_number=1, activity_label='Seed session'))

    if not ActivityEntry.query.filter_by(user_id=user.id).count():
        db.session.add(ActivityEntry(user_id=user.id, entry_type='seed', title='Demo data created', description='Created by seed_demo.py'))


    if not BreakSession.query.filter_by(user_id=user.id).count():
        for offset, report, trigger, exercises in [
            (1, 'better', 'fatigue', '["box_breathing","seated_cat_cow"]'),
            (3, 'same', 'manual', '["quiet_timer"]'),
            (5, 'better', 'manual', '["neck_rolls","shoulder_opener"]'),
        ]:
            started = datetime.combine(today - timedelta(days=offset), datetime.min.time()).replace(hour=15 + user_index, minute=10)
            db.session.add(BreakSession(
                user_id=user.id,
                started_at=started,
                ended_at=started + timedelta(minutes=4),
                trigger=trigger,
                exercises_done=exercises,
                self_report=report,
                fatigue_signal_snapshot='{}',
            ))


def main() -> None:
    app = create_app()
    password = secrets.token_urlsafe(12)

    with app.app_context():
        db.create_all()

        existing = User.query.filter(User.username.in_([item['username'] for item in DEMO_USERS])).all()
        for user in existing:
            db.session.delete(user)
        db.session.flush()

        created: list[tuple[str, str]] = []
        for index, payload in enumerate(DEMO_USERS, start=1):
            user = User(
                username=payload['username'],
                email=payload['email'],
                avatar_emoji=payload['avatar_emoji'],
                age=payload['age'],
                daily_water_goal_ml=payload['daily_water_goal_ml'],
                daily_sleep_goal_hours=payload['daily_sleep_goal_hours'],
                daily_step_goal=payload['daily_step_goal'],
                daily_exercise_goal_minutes=payload['daily_exercise_goal_minutes'],
                goal_progress_intensity=payload['goal_progress_intensity'],
                hydration_score=payload['hydration_score'],
                energy_score=payload['energy_score'],
                fitness_score=payload['fitness_score'],
                focus_score=payload['focus_score'],
                mood_score=payload['mood_score'],
                overall_wellness_score=payload['overall_wellness_score'],
                optimal_bedtime=payload['optimal_bedtime'],
                optimal_wake_time=payload['optimal_wake_time'],
                hydration_wake_time=payload['hydration_wake_time'],
                hydration_breakfast_time=payload['hydration_breakfast_time'],
                hydration_lunch_time=payload['hydration_lunch_time'],
                hydration_dinner_time=payload['hydration_dinner_time'],
                wellness_summary='Demo account seeded locally. Not for production use.',
            )
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            _seed_user(user, index)
            created.append((user.username, user.email))

        db.session.commit()

    print('Seeded demo users with a fresh shared password for this local database only:')
    for username, email in created:
        print(f'  - {username} <{email}>')
    print(f'Password: {password}')


if __name__ == '__main__':
    main()
