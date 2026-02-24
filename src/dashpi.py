#!/usr/bin/env python3

# set up logging
import os, logging.config

logging.config.fileConfig(os.path.join(os.path.dirname(__file__), 'config', 'logging.conf'))

import logging
import argparse
from utils.app_utils import generate_startup_image
from flask import Flask
from config import Config
from display.display_manager import DisplayManager
from refresh_task import RefreshTask
from blueprints.main import main_bp
from blueprints.settings import settings_bp
from blueprints.plugin import plugin_bp
from blueprints.apikeys import apikeys_bp
from blueprints.loops import loops_bp
from jinja2 import ChoiceLoader, FileSystemLoader
from plugins.plugin_registry import load_plugins
from waitress import serve


logger = logging.getLogger(__name__)

# Parse command line arguments
parser = argparse.ArgumentParser(description='DashPi Display Server')
parser.add_argument('--dev', action='store_true', help='Run in development mode')
args = parser.parse_args()

# Set development mode settings
if args.dev:
    Config.config_file = os.path.join(Config.BASE_DIR, "config", "device_dev.json")
    DEV_MODE = True
    PORT = 8080
    logger.info("Starting DashPi in DEVELOPMENT mode on port 8080")
else:
    DEV_MODE = False
    PORT = 80
    logger.info("Starting DashPi in PRODUCTION mode on port 80")
logging.getLogger('waitress.queue').setLevel(logging.ERROR)
app = Flask(__name__)
template_dirs = [
   os.path.join(os.path.dirname(__file__), "templates"),    # Default template folder
   os.path.join(os.path.dirname(__file__), "plugins"),      # Plugin templates
]
app.jinja_loader = ChoiceLoader([FileSystemLoader(directory) for directory in template_dirs])

device_config = Config()
display_manager = DisplayManager(device_config)
refresh_task = RefreshTask(device_config, display_manager)

load_plugins(device_config.get_plugins())

# Store dependencies
app.config['DEVICE_CONFIG'] = device_config
app.config['DISPLAY_MANAGER'] = display_manager
app.config['REFRESH_TASK'] = refresh_task

# Set additional parameters
app.config['MAX_FORM_PARTS'] = 10_000

# Register Blueprints
app.register_blueprint(main_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(plugin_bp)
app.register_blueprint(apikeys_bp)
app.register_blueprint(loops_bp)

# Inject project_name and version into all templates
@app.context_processor
def inject_globals():
    from blueprints.main import get_version
    return dict(project_name="DashPi", version=get_version())

# Register opener for HEIF/HEIC images
try:
    from pi_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    logger.debug("pi_heif not available, HEIF/HEIC support disabled")

if __name__ == '__main__':

    # start the background refresh task
    refresh_task.start()

    # display default dashpi image on startup
    if device_config.get_config("startup") is True:
        logger.info("Startup flag is set, displaying startup image")
        img = generate_startup_image(device_config.get_resolution())
        display_manager.display_image(img)
        device_config.update_value("startup", False, write=True)

    try:
        # Run the Flask app
        app.secret_key = os.urandom(24).hex()

        # Get local IP address for display (only in dev mode when running on non-Pi)
        if DEV_MODE:
            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                logger.info(f"Serving on http://{local_ip}:{PORT}")
            except (OSError, socket.error):
                pass  # Ignore if we can't get the IP

        serve(app, host="0.0.0.0", port=PORT, threads=2)
    finally:
        refresh_task.stop()
        # Clean up HTTP session connection pool
        from utils.http_client import close_http_session
        close_http_session()
