import time
from collections import defaultdict
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # CSP
        csp = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self' http://localhost:5173 http://127.0.0.1:5173;"
        )
        response.headers["Content-Security-Policy"] = csp
        # Other security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.ip_data = defaultdict(lambda: {"count": 0, "reset_time": time.time() + 60})

    async def dispatch(self, request: Request, call_next):
        # Allow health checks and webhooks without strict limits
        if request.url.path.startswith("/api/v1/health") or request.url.path.startswith("/api/v1/github/webhook"):
            return await call_next(request)
            
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        
        ip_record = self.ip_data[client_ip]
        if now > ip_record["reset_time"]:
            ip_record["count"] = 0
            ip_record["reset_time"] = now + 60
            
        if ip_record["count"] >= self.requests_per_minute:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please try again later."}
            )
            
        ip_record["count"] += 1
        
        response = await call_next(request)
        return response
