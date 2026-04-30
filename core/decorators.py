from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect
from .utils import role_home_url


def role_required(*allowed_roles: str):
    allowed = {r.lower() for r in allowed_roles}

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("/")

            if not allowed:
                return view_func(request, *args, **kwargs)

            user_role = (getattr(request.user, "role", "") or "").lower()
            if user_role not in allowed:
                messages.error(request, "Unauthorized access.")
                return redirect(role_home_url(user_role))
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator

