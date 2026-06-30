# Deploy: AWS Lambda (scale-to-zero)

Runs the Flask app as a container on Lambda behind a streaming Function
URL via the [Lambda Web Adapter](https://github.com/awslabs/aws-lambda-web-adapter).
No instance runs (and nothing is billed) until a request arrives; the
free tier covers personal weekly use, so idle cost is **$0**.

The SSE endpoints work because the Function URL uses `RESPONSE_STREAM`
invoke mode and the adapter runs with `AWS_LWA_INVOKE_MODE=response_stream`.

- **Account:** `891376947205`   **Region:** `eu-west-2`
- **Architecture:** `arm64` (cheaper; matches Apple-Silicon native builds)

## Prerequisites

- A container builder. Docker isn't installed on this machine. Lightweight option on macOS:
  ```sh
  brew install colima docker
  colima start
  ```
- AWS CLI authenticated as the `claude-reddit-scraper` IAM user (already configured).
- The runtime secrets to set in step 4 (values you already use on EB):
  `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`,
  `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REFRESH_TOKEN`,
  `BEATPORT_REFRESH_TOKEN`, plus two new ones:
  - `FLASK_SECRET_KEY` — random string; keeps login cookies valid across cold starts. `python -c "import secrets; print(secrets.token_hex(32))"`
  - `APP_PASSWORD` — the password that gates the public URL. Leave unset to keep the URL open (not recommended once it can write to your accounts).

## 1. Build & push the image to ECR

```sh
ACCOUNT=891376947205
REGION=eu-west-2
REPO=dnb-scraper
ECR=$ACCOUNT.dkr.ecr.$REGION.amazonaws.com

aws ecr create-repository --repository-name $REPO --region $REGION || true

aws ecr get-login-password --region $REGION \
  | docker login --username AWS --password-stdin $ECR

docker build --platform linux/arm64 -t $REPO:latest .
docker tag $REPO:latest $ECR/$REPO:latest
docker push $ECR/$REPO:latest
```

## 2. Lambda execution role (one-time)

```sh
aws iam create-role --role-name dnb-scraper-lambda \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

aws iam attach-role-policy --role-name dnb-scraper-lambda \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
```

## 3. Create the function from the image

```sh
aws lambda create-function --function-name dnb-scraper \
  --package-type Image \
  --code ImageUri=$ECR/$REPO:latest \
  --role arn:aws:iam::$ACCOUNT:role/dnb-scraper-lambda \
  --architectures arm64 \
  --memory-size 1024 \
  --timeout 900 \
  --region $REGION
```

## 4. Set secrets

```sh
aws lambda update-function-configuration --function-name dnb-scraper --region $REGION \
  --environment "Variables={REDDIT_CLIENT_ID=...,REDDIT_CLIENT_SECRET=...,REDDIT_USER_AGENT=...,SPOTIFY_CLIENT_ID=...,SPOTIFY_CLIENT_SECRET=...,SPOTIFY_REFRESH_TOKEN=...,BEATPORT_REFRESH_TOKEN=...,FLASK_SECRET_KEY=...,APP_PASSWORD=...}"
```

## 5. Streaming Function URL (public; app password gates it)

```sh
aws lambda create-function-url-config --function-name dnb-scraper --region $REGION \
  --auth-type NONE --invoke-mode RESPONSE_STREAM

aws lambda add-permission --function-name dnb-scraper --region $REGION \
  --statement-id FunctionURLAllowPublicAccess \
  --action lambda:InvokeFunctionUrl --principal "*" \
  --function-url-auth-type NONE

# REQUIRED since Oct 2025: public Function URLs (AuthType NONE) need
# lambda:InvokeFunction in the resource policy too, NOT just
# InvokeFunctionUrl. Without it, anonymous requests get 403 (while
# in-account IAM-signed calls still work — a confusing symptom).
# Note: no --function-url-auth-type here; that condition stops it applying.
aws lambda add-permission --function-name dnb-scraper --region $REGION \
  --statement-id AllowPublicInvoke \
  --action lambda:InvokeFunction --principal "*"
```

Note the `FunctionUrl` printed — that's your new app URL. (No CloudFront
needed — the direct Function URL is public and streams SSE; the app's
`APP_PASSWORD` gate protects it.)

## 6. Spotify auth (durable refresh token)

Lambda's /tmp is ephemeral, so capture a Spotify refresh token once and
set it as `SPOTIFY_REFRESH_TOKEN` (mirrors Beatport). Run:

```sh
python scripts/get_spotify_refresh_token.py   # HTTPS loopback OAuth
```

Prereq: register `https://127.0.0.1:8765/callback` (HTTPS — plain http
loopback triggers a Spotify `server_error`) on the app whose client id
matches `SPOTIFY_CLIENT_ID`. It prints `SPOTIFY_REFRESH_TOKEN=...`; add
that to the Lambda env (step 4). For in-app re-auth later, also register
`<PUBLIC_BASE_URL>/spotify/callback`. (Beatport needs no dashboard change.)

## 7. Smoke test

Open the Function URL, sign in with `APP_PASSWORD`, then run a full
scrape → preview → commit. Confirm the scrape streams (sections appear
incrementally — proves SSE streaming works end to end). `GET /healthz`
should return `ok`.

## 8. Decommission Elastic Beanstalk (only after step 7 passes)

```sh
eb terminate dnb-scraper-prod
```

This stops the ~$10/month charge. Leave it running until the Lambda URL
is verified so there's no gap.

## Redeploying later

Repeat step 1, then:

```sh
aws lambda update-function-code --function-name dnb-scraper --region $REGION \
  --image-uri $ECR/$REPO:latest --publish
```
