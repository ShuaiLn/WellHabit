from .auth import bp as auth_bp
from .care import bp as care_bp
from .hooks import bp as hooks_bp
from .main import bp as main_bp
from .reminders import bp as reminders_bp
from .tasks import bp as tasks_bp


def register_blueprints(app):
    for blueprint in [hooks_bp, auth_bp, main_bp, tasks_bp, care_bp, reminders_bp]:
        app.register_blueprint(blueprint)
