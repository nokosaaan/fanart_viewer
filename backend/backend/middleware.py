from django.conf import settings

class EnsureCorsHeaderMiddleware:
    """Fallback middleware to ensure CORS header is present on responses.

    This is a safety net: normally `django-cors-headers` should add the
    appropriate headers. If for any reason a response does not contain
    `Access-Control-Allow-Origin` but the request's Origin is allowed by
    settings, this middleware will add the header so browsers don't block
    legitimate traffic.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            origin = request.META.get('HTTP_ORIGIN')
            if not origin:
                return response
            # If corsheaders already set the header, do nothing
            if response.get('Access-Control-Allow-Origin'):
                return response
            # Allow all origins if configured
            if getattr(settings, 'CORS_ALLOW_ALL_ORIGINS', False):
                response['Access-Control-Allow-Origin'] = '*'
                return response
            allowed = getattr(settings, 'CORS_ALLOWED_ORIGINS', []) or []
            # Normalize
            allowed_norm = [a.rstrip('/') for a in allowed]
            if origin.rstrip('/') in allowed_norm:
                response['Access-Control-Allow-Origin'] = origin
        except Exception:
            # Never let this middleware raise and block responses
            pass
        return response
