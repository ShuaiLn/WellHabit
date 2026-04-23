from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from .. import db
from ..models import User
from ..services.wellness import _ensure_baseline_scores
from ..utils.text import USERNAME_RE, _clean_text, _is_valid_email

bp = Blueprint('auth', __name__)


@bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template('index.html')


@bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        username = _clean_text(request.form.get('username'), 30)
        email = _clean_text(request.form.get('email'), 120).lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not username or not email or not password:
            flash('Please fill in all required fields.', 'danger')
            return render_template('register.html')
        if not USERNAME_RE.match(username):
            flash('Username must be 3 to 30 characters and use only letters, numbers, dots, underscores, or hyphens.', 'danger')
            return render_template('register.html')
        if not _is_valid_email(email):
            flash('Please enter a valid email address.', 'danger')
            return render_template('register.html')
        if len(password) < 8:
            flash('Password must be at least 8 characters long.', 'danger')
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
        return redirect(url_for('auth.login'))

    return render_template('register.html')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        email = _clean_text(request.form.get('email'), 120).lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            login_user(user)
            if not user.wellness_updated_at:
                _ensure_baseline_scores(user)
                db.session.commit()
            flash('Logged in successfully.', 'success')
            return redirect(url_for('main.dashboard'))

        flash('Invalid email or password.', 'danger')

    return render_template('login.html')


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


