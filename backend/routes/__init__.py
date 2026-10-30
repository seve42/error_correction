"""
路由注册模块 — 将所有 Blueprint 注册到 Flask app
"""

from .auth import bp as auth_bp
from .upload import bp as upload_bp
from .device import bp as device_bp
from .questions import bp as questions_bp
from .chat import bp as chat_bp
from .stats import bp as stats_bp
from .settings import bp as settings_bp
from .notes import bp as notes_bp
from .projects import bp as projects_bp
from .usage import bp as usage_bp
from .answers import bp as answers_bp


def register_routes(app):
    """注册所有 Blueprint 到 Flask app"""
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(upload_bp, url_prefix='/api')
    app.register_blueprint(device_bp, url_prefix='/api/device')
    app.register_blueprint(questions_bp, url_prefix='/api')
    app.register_blueprint(chat_bp, url_prefix='/api')
    app.register_blueprint(stats_bp, url_prefix='/api')
    app.register_blueprint(settings_bp, url_prefix='/api')
    app.register_blueprint(notes_bp, url_prefix='/api/notes')
    app.register_blueprint(projects_bp, url_prefix='/api')
    app.register_blueprint(usage_bp, url_prefix='/api')
    app.register_blueprint(answers_bp, url_prefix='/api')
