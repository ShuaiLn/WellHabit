from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from pathlib import Path


db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.login_message_category = 'warning'


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    instance_path = Path(app.instance_path)
    instance_path.mkdir(parents=True, exist_ok=True)

    app.config.update(
        SECRET_KEY='dev-change-this-secret-key',
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{instance_path / 'wellness.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )

    db.init_app(app)
    login_manager.init_app(app)

    from . import routes  # noqa: F401
    from .models import User  # noqa: F401

    with app.app_context():
        db.create_all()

    routes.register_routes(app)
    return app
