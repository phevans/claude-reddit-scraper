# Runs the existing Flask/gunicorn app inside AWS Lambda via the
# Lambda Web Adapter, which proxies Lambda invokes to a normal HTTP
# server. response_stream mode keeps the SSE endpoints (/scrape,
# /preview-playlists, /create-playlists) streaming end to end.
FROM public.ecr.aws/docker/library/python:3.12-slim

# The adapter ships as a Lambda extension; copy its binary in.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
ENV AWS_LWA_INVOKE_MODE=response_stream \
    AWS_LWA_READINESS_CHECK_PATH=/healthz \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Threaded workers so SSE responses can stream concurrently; 900s
# timeout matches Lambda's 15-min ceiling so a long run isn't killed
# by gunicorn first.
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000", \
     "--worker-class=gthread", "--threads=8", "--timeout=900"]
