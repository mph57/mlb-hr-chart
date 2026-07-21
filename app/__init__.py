"""Application factory for the daily MLB home-run chart."""
import os

from flask import Flask

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover - dotenv optional at runtime
    pass

from .config import Config


def create_app(config_class: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    from .main import main_bp
    from .api import api_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

    return app
