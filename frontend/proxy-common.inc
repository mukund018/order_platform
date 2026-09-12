proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;

# Pass the browser's request id through when it sets one, and let the service mint one
# when it does not. Either way the id the user sees is the id in the logs.
proxy_set_header X-Request-ID $http_x_request_id;

# Longer than any timeout the services use between themselves, so a slow upstream shows
# up in the UI as the service's own error rather than as an nginx 504.
proxy_connect_timeout 5s;
proxy_read_timeout 30s;
