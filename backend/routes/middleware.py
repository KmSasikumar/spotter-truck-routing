"""Permissive CORS middleware for the take-home API.

The frontend is hosted on a different origin (Antideploy) than this API
(Render), so browsers need Access-Control-Allow-Origin on responses and an
answer to OPTIONS preflights. The API is public and keyless, so '*' is fine.
"""
from django.http import HttpResponse


class CorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "OPTIONS":
            response = HttpResponse()
        else:
            response = self.get_response(request)
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        return response
