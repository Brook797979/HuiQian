from functools import wraps

from flask import g, jsonify, request

import attendance


def _bearer_token():
    scheme, _, token = request.headers.get('Authorization', '').partition(' ')
    return token.strip() if scheme.lower() == 'bearer' else ''


def _current_admin_session():
    token = _bearer_token()
    if not token:
        return None, (jsonify(ok=False, code='ADMIN_AUTH_REQUIRED', msg='administrator authentication is required'), 401)
    session = attendance.resolve_admin_session(token)
    if session is None:
        return None, (jsonify(ok=False, code='ADMIN_SESSION_INVALID', msg='administrator session is invalid'), 401)
    return session, None


def require_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        session, error = _current_admin_session()
        if error is not None:
            return error
        g.admin_session = session
        g.admin = session
        return view(*args, **kwargs)
    return wrapped


def require_super_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        session, error = _current_admin_session()
        if error is not None:
            return error
        if not attendance.is_super_admin(session):
            return jsonify(ok=False, code='ADMIN_PERMISSION_DENIED', msg='super administrator permission is required'), 403
        g.admin_session = session
        g.admin = session
        return view(*args, **kwargs)
    return wrapped
