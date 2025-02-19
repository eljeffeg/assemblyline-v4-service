import functools
import os

import requests
from flask import jsonify, make_response, request, send_from_directory, send_file, Flask
from werkzeug.exceptions import Unauthorized, ServiceUnavailable

session = requests.session()
app = Flask('service_updater')
AUTH_KEY = os.environ.get('AL_INSTANCE_KEY', 'ThisIsARandomAuthKey...ChangeMe!')
AL_ROOT_CA = os.environ.get('AL_ROOT_CA', '/etc/assemblyline/ssl/al_root-ca.crt')

ssl_context = None
if os.path.exists(AL_ROOT_CA):
    ssl_context = ('/etc/assemblyline/ssl/al_updates/tls.crt', '/etc/assemblyline/ssl/al_updates/tls.key')


@app.route('/healthz/live')
def container_ready():
    """Only meant to convey if the container is running, not if updates are ready."""
    return make_response("OK")


@app.route('/status')
def update_status():
    """A report on readiness for services to run."""
    request = session.get('http://localhost:9999')
    request.raise_for_status()
    response = app.response_class(
        response=request.text,
        status=200,
        mimetype='application/json'
    )
    return response


def api_login(func):
    @functools.wraps(func)
    def base(*args, **kwargs):
        # Before anything else, check that the API key is set
        apikey = request.environ.get('HTTP_X_APIKEY', None)
        if AUTH_KEY != apikey:
            app.logger.warning(f'Client provided wrong api key [{apikey}]')
            raise Unauthorized("Unauthorized access denied")
        return func(*args, **kwargs)

    return base


def get_paths():
    try:
        request = session.get('http://localhost:9999')
        request.raise_for_status()
        path = request.json()['_directory']
        tar = request.json()['_tar']
        if path is None or not os.path.isdir(path):
            raise ValueError()
        if tar is None or not os.path.isfile(tar):
            raise ValueError()
    except Exception:
        raise ServiceUnavailable("No update ready")
    return path, tar


@app.route('/files')
@api_login
def list_files():
    """Get a directory listing of files in the current update."""
    path, _ = get_paths()

    entries = []
    for dirname, _, file_names in os.walk(path):
        entries.extend([os.path.join(dirname, _f) for _f in file_names])

    return make_response(jsonify({
        'files': entries
    }))


@app.route('/files/<path:name>')
@api_login
def get_file(name):
    """Download a specific file from the directory listing of the current update."""
    path, _ = get_paths()
    return send_from_directory(path, name)


@app.route('/tar')
@api_login
def get_all_files():
    """Download a tar containing all the files in the current update."""
    _, path = get_paths()
    return send_file(path)


if __name__ == '__main__':
    app.run(ssl_context=ssl_context)
