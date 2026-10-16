"""Render-only config; never use on an origin directly exposed to the internet.

Render terminates TLS. Trust only the forwarded protocol, never forwarded host
or client identity. The launcher refuses non-Render environments.
"""
forwarded_allow_ips = '*'
secure_scheme_headers = {'X-FORWARDED-PROTO': 'https'}
workers = 1
timeout = 30
graceful_timeout = 10
# Avoid request logs containing submitted values or query strings.
accesslog = None
errorlog = '-'
