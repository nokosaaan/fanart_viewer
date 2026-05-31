from django.http import HttpResponse


class SimpleCorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'OPTIONS':
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)

        response['Access-Control-Allow-Origin'] = '*'
        response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type, Accept, Authorization, X-Requested-With'
        response['Access-Control-Max-Age'] = '86400'
        return response