from functools import wraps
from flask import abort
from flask_login import current_user


def owner_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_owner:
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped
