"""
WSGI entry point for PythonAnywhere (and other WSGI hosts like gunicorn).

PythonAnywhere expects a module-level `application` variable.
Gunicorn usage:  gunicorn wsgi:application
"""
import os
import sys

# Ensure the project directory is on the path
project_dir = os.path.dirname(os.path.abspath(__file__))
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

os.chdir(project_dir)

from family_calendar_server import make_app_from_env

application = make_app_from_env()
