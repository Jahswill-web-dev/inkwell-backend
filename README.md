# Inkwell Backend

Inkwell Backend is an asynchronous REST API built with FastAPI and PostgreSQL. The project is
organized into small, domain-focused modules so that API routes, business logic, database access,
validation, and infrastructure concerns remain separate as the application grows.

## Tech Stack

- Python 3.12+
- FastAPI and Uvicorn
- PostgreSQL 17
- SQLAlchemy 2 with async psycopg
- Alembic database migrations
- Pydantic and pydantic-settings
- Google Gen AI SDK with Gemini on Vertex AI
- Argon2 password hashing with pwdlib
- JWT authentication with PyJWT
- uv for dependency and virtual-environment management
- Pytest, Ruff, and mypy for testing and code quality
- Docker Compose for local PostgreSQL services

## Project Structure

```text
inkwell-backend/
|-- alembic/                  # Database migration environment
|   |-- versions/             # Versioned schema migrations
|   |-- env.py                # Alembic runtime configuration
|   `-- script.py.mako        # Migration file template
|-- app/
|   |-- api/                  # HTTP endpoints and routers
|   |   |-- health.py         # Health and database-readiness endpoints
|   |   `-- v1/               # Version 1 API routes
|   |-- core/                 # Settings, security, and shared exceptions
|   |-- db/                   # Database engine, sessions, models, and repositories
|   |   |-- models/           # SQLAlchemy database models
|   |   `-- repositories/     # Domain-specific database queries
|   |-- prompts/              # AI prompt definitions
|   |-- schemas/              # Pydantic request and response models
|   |-- services/             # Application and business logic
|   |-- tests/                # Unit and PostgreSQL integration tests
|   |-- workers/              # Background job entry points
|   `-- main.py               # FastAPI application factory and entry point
|-- .env.example              # Example environment configuration
|-- alembic.ini               # Alembic configuration
|-- docker-compose.yml        # Development and test PostgreSQL services
|-- pyproject.toml            # Dependencies and tool configuration
`-- uv.lock                   # Locked dependency versions
```

## Prerequisites

Install the following tools before starting:

- Python 3.12 or 3.13
- [uv](https://docs.astral.sh/uv/)
- Docker Desktop or Docker Engine with Docker Compose

## Getting Started

### 1. Install dependencies

From the project root, run:

```powershell
uv sync
```

### 2. Configure the environment

Create a local `.env` file from the example:

```powershell
Copy-Item .env.example .env
```

Replace `JWT_SECRET_KEY` in `.env` with a private random value containing at least 32 characters.
Do not commit the `.env` file.

### 3. Configure Vertex AI

Brief generation requires a Google Cloud project with billing and the Vertex AI API enabled. Install
the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install), then run:

```powershell
gcloud init
gcloud config set project YOUR_PROJECT_ID
gcloud services enable aiplatform.googleapis.com --project=YOUR_PROJECT_ID
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

The authenticated developer or runtime service account needs the Vertex AI User
(`roles/aiplatform.user`) role. An administrator can grant it to a developer with:

```powershell
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID `
  --member="user:YOUR_EMAIL" `
  --role="roles/aiplatform.user"
```

Configure the backend in `.env`:

```env
VERTEX_PROJECT_ID=your-google-cloud-project-id
VERTEX_LOCATION=global
VERTEX_MODEL_ID=gemini-2.5-flash
VERTEX_REQUEST_TIMEOUT_SECONDS=45
VERTEX_MAX_OUTPUT_TOKENS=4096
```

Application Default Credentials are discovered automatically and must not be copied into `.env`.
For production on Google Cloud, attach a dedicated service account to the workload. For deployments
outside Google Cloud, prefer Workload Identity Federation instead of a downloadable service-account
key. If `VERTEX_PROJECT_ID` is unset, the API still starts, but brief generation returns
`503 brief_generation_unavailable`.

### 4. Start PostgreSQL

```powershell
docker compose up -d postgres
```

The development database will be available on port `5432` using the connection configured in
`.env`.

### 5. Apply database migrations

```powershell
uv run alembic upgrade head
```

### 6. Start the API

```powershell
uv run uvicorn app.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`.

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- OpenAPI schema: `http://127.0.0.1:8000/openapi.json`

## Database Migrations

Create a migration after changing a database model:

```powershell
uv run alembic revision --autogenerate -m "describe the change"
```

Review the generated migration, then apply it:

```powershell
uv run alembic upgrade head
```

Roll back the most recent migration:

```powershell
uv run alembic downgrade -1
```

## Testing

Run tests that do not require PostgreSQL:

```powershell
uv run pytest -m "not database"
```

Run the complete test suite with the isolated test database:

```powershell
docker compose --profile test up -d postgres-test
uv run pytest
docker compose --profile test stop postgres-test
```

The test database runs on port `5433` and uses temporary in-memory storage. It is separate from
the development database.

To make one real Vertex AI request after configuring credentials, run the opt-in smoke test:

```powershell
$env:RUN_VERTEX_SMOKE_TEST="true"
uv run pytest app/tests/integration/test_vertex_smoke.py
```

## Code Quality

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy app
```

To format the code automatically:

```powershell
uv run ruff format .
```

## Stopping Local Services

Stop the development database without deleting its stored data:

```powershell
docker compose stop postgres
```

Stop all services and remove their containers:

```powershell
docker compose down
```

## API Reference

This section is the integration contract for the endpoints currently exposed by the backend.
Article intake routes persist source material and can generate structured editorial briefs and
outlines through Gemini on Vertex AI and persists editable article drafts. Review routes remain
scaffolds.

### Connection Details

| Setting | Development value |
| --- | --- |
| Base URL | `http://127.0.0.1:8000` |
| Versioned API base | `http://127.0.0.1:8000/api/v1` |
| Request and response format | JSON |
| Interactive documentation | `http://127.0.0.1:8000/docs` |
| OpenAPI schema | `http://127.0.0.1:8000/openapi.json` |

For requests with a JSON body, send this header:

```http
Content-Type: application/json
```

The registration and login endpoints return a JWT access token. Send that token to protected
endpoints such as `/api/v1/auth/me` in the bearer format:

```http
Authorization: Bearer <access_token>
```

Browser clients must run from an origin included in `CORS_ORIGINS` in `.env`. The default example
allows `http://localhost:3000`.

### Endpoint Summary

| Method | Path | Authentication | Success status | Purpose |
| --- | --- | --- | --- | --- |
| `GET` | `/health` | None | `200 OK` | Check whether the API process is running |
| `GET` | `/ready` | None | `200 OK` | Check whether the API can connect to PostgreSQL |
| `POST` | `/api/v1/auth/register` | None | `201 Created` | Create an account and receive an access token |
| `POST` | `/api/v1/auth/login` | None | `200 OK` | Authenticate by email and receive an access token |
| `GET` | `/api/v1/auth/me` | Bearer token | `200 OK` | Return the authenticated user |
| `POST` | `/api/v1/articles` | Bearer token | `201 Created` | Create an article intake |
| `GET` | `/api/v1/articles` | Bearer token | `200 OK` | List the current user's articles |
| `GET` | `/api/v1/articles/{article_id}` | Bearer token | `200 OK` | Retrieve an owned article |
| `PATCH` | `/api/v1/articles/{article_id}` | Bearer token | `200 OK` | Partially update an owned article |
| `DELETE` | `/api/v1/articles/{article_id}` | Bearer token | `204 No Content` | Permanently delete an owned article |
| `POST` | `/api/v1/articles/{article_id}/brief` | Bearer token | `200 OK` | Generate or replace an article brief |
| `GET` | `/api/v1/articles/{article_id}/brief` | Bearer token | `200 OK` | Retrieve an article brief |
| `PATCH` | `/api/v1/articles/{article_id}/brief` | Bearer token | `200 OK` | Partially update an article brief |
| `POST` | `/api/v1/articles/{article_id}/outline` | Bearer token | `200 OK` | Generate or replace an article outline |
| `GET` | `/api/v1/articles/{article_id}/outline` | Bearer token | `200 OK` | Retrieve an article outline |
| `PATCH` | `/api/v1/articles/{article_id}/outline` | Bearer token | `200 OK` | Replace an outline's sections |
| `DELETE` | `/api/v1/articles/{article_id}/outline` | Bearer token | `204 No Content` | Delete an article outline |
| `GET` | `/api/v1/articles/{article_id}/draft` | Bearer token | `200 OK` | Retrieve and reconcile an article draft |
| `POST` | `/api/v1/articles/{article_id}/draft` | Bearer token | `200 OK` | Idempotently create an article draft |
| `PATCH` | `/api/v1/articles/{article_id}/draft` | Bearer token | `200 OK` | Replace a draft's ordered sections |
| `POST` | `/api/v1/articles/{article_id}/draft/sections/{section_id}/talking-points` | Bearer token | `200 OK` | Generate transient talking points for a draft section |

### Standard Error Format

All handled API errors use the same top-level structure:

```json
{
  "error": {
    "code": "machine_readable_code",
    "message": "Human-readable explanation",
    "details": null
  }
}
```

The `details` field is included only when extra information is available. Client applications
should use `error.code` for program logic and `error.message` for display or logging.

### GET `/health`

Checks whether the FastAPI process is running. This endpoint does not contact PostgreSQL, making it
suitable for a lightweight application liveness check.

Request body: none

Example request:

```bash
curl http://127.0.0.1:8000/health
```

Successful response - `200 OK`:

```json
{
  "status": "ok"
}
```

### GET `/ready`

Checks whether the application can connect to PostgreSQL and execute a simple query. Use it for
deployment readiness checks or to confirm that the local database configuration is working.

Request body: none

Example request:

```bash
curl http://127.0.0.1:8000/ready
```

Successful response - `200 OK`:

```json
{
  "status": "ready"
}
```

Database unavailable - `503 Service Unavailable`:

```json
{
  "error": {
    "code": "database_unavailable",
    "message": "The database is unavailable"
  }
}
```

### POST `/api/v1/auth/register`

Creates a user account, stores an Argon2 password hash, and returns the public user data together
with a signed JWT access token.

Authentication: none

Request body:

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `email` | string | Yes | Valid email; surrounding whitespace is removed and the value is lowercased |
| `username` | string | Yes | 3-30 characters; lowercase letters, numbers, and underscores only |
| `password` | string | Yes | 8-128 characters; the value is not trimmed or otherwise changed |

The username is trimmed and lowercased before validation and storage. For example, `Writer_01`
becomes `writer_01`. Email and username uniqueness checks are therefore case-insensitive.

Example request:

```bash
curl --request POST http://127.0.0.1:8000/api/v1/auth/register \
  --header "Content-Type: application/json" \
  --data '{
    "email": "writer@example.com",
    "username": "writer_01",
    "password": "correct horse battery staple"
  }'
```

Successful response - `201 Created`:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "id": "46a42280-6ad8-4bb6-a29c-1604adbf0c31",
    "email": "writer@example.com",
    "username": "writer_01",
    "created_at": "2026-08-11T12:00:00Z",
    "updated_at": "2026-08-11T12:00:00Z"
  }
}
```

The plaintext password and stored password hash are never included in the response. The token's
`sub` claim contains the returned user ID. Token lifetime, issuer, and audience come from the JWT
settings in `.env`.

Frontend example:

```javascript
const response = await fetch("http://127.0.0.1:8000/api/v1/auth/register", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    email: "writer@example.com",
    username: "writer_01",
    password: "correct horse battery staple",
  }),
});

const body = await response.json();

if (!response.ok) {
  throw new Error(body.error?.message ?? "Registration failed");
}

const { access_token, user } = body;
```

Possible responses:

| Status | Error code | Meaning |
| --- | --- | --- |
| `201 Created` | N/A | Account created successfully |
| `409 Conflict` | `email_already_registered` | An account already uses the normalized email |
| `409 Conflict` | `username_taken` | An account already uses the normalized username |
| `422 Unprocessable Entity` | `validation_error` | One or more request fields failed validation |
| `500 Internal Server Error` | `internal_server_error` | An unexpected server error occurred |

Duplicate email example - `409 Conflict`:

```json
{
  "error": {
    "code": "email_already_registered",
    "message": "An account with this email already exists"
  }
}
```

Duplicate username example - `409 Conflict`:

```json
{
  "error": {
    "code": "username_taken",
    "message": "This username is already taken"
  }
}
```

Validation errors return `422 Unprocessable Entity`. The `details` array identifies the invalid
field and validation rule so that clients can map errors back to form inputs:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": [
      {
        "type": "string_too_short",
        "loc": ["body", "password"],
        "msg": "String should have at least 8 characters"
      }
    ]
  }
}
```

### POST `/api/v1/auth/login`

Authenticates an existing user by email and password. A successful request returns the same user
and access-token structure as registration.

Authentication: none

Request body:

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `email` | string | Yes | Valid email; surrounding whitespace is removed and the value is lowercased |
| `password` | string | Yes | 1-128 characters; the value is not trimmed or otherwise changed |

Example request:

```bash
curl --request POST http://127.0.0.1:8000/api/v1/auth/login \
  --header "Content-Type: application/json" \
  --data '{
    "email": "writer@example.com",
    "password": "correct horse battery staple"
  }'
```

Successful response - `200 OK`:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "id": "46a42280-6ad8-4bb6-a29c-1604adbf0c31",
    "email": "writer@example.com",
    "username": "writer_01",
    "created_at": "2026-08-11T12:00:00Z",
    "updated_at": "2026-08-11T12:00:00Z"
  }
}
```

Frontend example:

```javascript
const response = await fetch("http://127.0.0.1:8000/api/v1/auth/login", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    email: "writer@example.com",
    password: "correct horse battery staple",
  }),
});

const body = await response.json();

if (!response.ok) {
  const retryAfter = response.headers.get("Retry-After");
  throw new Error(
    retryAfter
      ? `${body.error.message} Try again in ${retryAfter} seconds.`
      : body.error.message,
  );
}

const { access_token, user } = body;
```

Possible responses:

| Status | Error code | Meaning |
| --- | --- | --- |
| `200 OK` | N/A | Credentials accepted |
| `401 Unauthorized` | `invalid_credentials` | Email is unknown or the password is incorrect |
| `422 Unprocessable Entity` | `validation_error` | Email or password failed request validation |
| `429 Too Many Requests` | `too_many_login_attempts` | The email or client IP exceeded the failure limit |
| `500 Internal Server Error` | `internal_server_error` | An unexpected server error occurred |

Unknown emails and incorrect passwords intentionally return the same response to avoid revealing
whether an account exists:

```json
{
  "error": {
    "code": "invalid_credentials",
    "message": "Invalid email or password"
  }
}
```

A `401 Unauthorized` response includes:

```http
WWW-Authenticate: Bearer
```

Login failures are limited to 5 attempts per normalized email and 20 attempts per client IP during
each 15-minute window. These defaults can be changed with
`LOGIN_RATE_LIMIT_EMAIL_FAILURES`, `LOGIN_RATE_LIMIT_IP_FAILURES`, and
`LOGIN_RATE_LIMIT_WINDOW_SECONDS`.

Rate-limited response - `429 Too Many Requests`:

```json
{
  "error": {
    "code": "too_many_login_attempts",
    "message": "Too many login attempts. Please try again later"
  }
}
```

The response includes a `Retry-After` header containing the number of seconds before the current
limit window expires:

```http
Retry-After: 742
```

The backend derives the rate-limit IP from `request.client.host`. When deploying behind a reverse
proxy, configure Uvicorn's trusted proxy settings so forwarded client addresses are accepted only
from known proxy IPs. Without this configuration, all requests may appear to come from the proxy
and share one IP limit. Never trust forwarded IP headers from arbitrary internet clients.

### GET `/api/v1/auth/me`

Validates an access token and returns the user identified by its `sub` claim. Frontends can call
this endpoint when the application loads to restore authentication state and decide whether to
show protected pages.

Authentication: bearer access token required

Example request:

```bash
curl http://127.0.0.1:8000/api/v1/auth/me \
  --header "Authorization: Bearer <access_token>"
```

Successful response - `200 OK`:

```json
{
  "id": "46a42280-6ad8-4bb6-a29c-1604adbf0c31",
  "email": "writer@example.com",
  "username": "writer_01",
  "created_at": "2026-08-11T12:00:00Z",
  "updated_at": "2026-08-11T12:00:00Z"
}
```

Frontend session-restoration example:

```javascript
async function loadCurrentUser(accessToken) {
  const response = await fetch("http://127.0.0.1:8000/api/v1/auth/me", {
    headers: {
      Authorization: `Bearer ${accessToken}`,
    },
  });

  if (response.status === 401) {
    // Clear local authentication state and redirect to the login page.
    return null;
  }

  if (!response.ok) {
    throw new Error("Unable to load the current user");
  }

  return response.json();
}
```

Possible responses:

| Status | Error code | Meaning |
| --- | --- | --- |
| `200 OK` | N/A | Token is valid and its user exists |
| `401 Unauthorized` | `authentication_required` | Bearer credentials were not supplied |
| `401 Unauthorized` | `invalid_token` | Token is invalid, expired, or references a missing user |
| `500 Internal Server Error` | `internal_server_error` | An unexpected server error occurred |

Missing credentials response - `401 Unauthorized`:

```json
{
  "error": {
    "code": "authentication_required",
    "message": "Authentication is required"
  }
}
```

Invalid token response - `401 Unauthorized`:

```json
{
  "error": {
    "code": "invalid_token",
    "message": "The access token is invalid or expired"
  }
}
```

Both authentication errors include:

```http
WWW-Authenticate: Bearer
```

### Article intake

Article intake endpoints require a bearer access token. They save the user's notes and planning
choices used for brief generation. Every read and mutation is scoped to the authenticated user.

The five accepted `article_goal` values map to frontend labels as follows:

| API value | Display label |
| --- | --- |
| `inform_and_inspire` | Inform and inspire |
| `educate_with_practical_guidance` | Educate with practical guidance |
| `persuade_or_change_a_perspective` | Persuade or change a perspective |
| `inspire_readers_to_take_action` | Inspire readers to take action |
| `entertain_with_a_compelling_story` | Entertain with a compelling story |

All text fields are trimmed and must contain non-whitespace content. `notes` accepts at most
20,000 characters and `working_title` at most 200. `target_audience` must be a JSON array containing
1 through 10 strings. Each audience accepts at most 500 characters. Audiences must be unique when
compared case-insensitively; their original spelling, casing, and order are otherwise preserved.
Sending a single string instead of an array fails validation.

#### POST `/api/v1/articles`

Creates an article intake. All four fields are required.

```json
{
  "notes": "Research notes and an early idea",
  "working_title": "How small teams can publish consistently",
  "target_audience": [
    "Independent writers",
    "Small content teams"
  ],
  "article_goal": "educate_with_practical_guidance"
}
```

Successful response - `201 Created`:

```json
{
  "id": "be5579e3-24fd-4272-a35f-f74740c3887e",
  "user_id": "46a42280-6ad8-4bb6-a29c-1604adbf0c31",
  "notes": "Research notes and an early idea",
  "working_title": "How small teams can publish consistently",
  "target_audience": [
    "Independent writers",
    "Small content teams"
  ],
  "article_goal": "educate_with_practical_guidance",
  "created_at": "2026-08-12T12:00:00Z",
  "updated_at": "2026-08-12T12:00:00Z"
}
```

Frontend example:

```javascript
const targetAudience = ["Independent writers", "Small content teams"];

const response = await fetch("http://127.0.0.1:8000/api/v1/articles", {
  method: "POST",
  headers: {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    notes: "Research notes and an early idea",
    working_title: "How small teams can publish consistently",
    target_audience: targetAudience,
    article_goal: "educate_with_practical_guidance",
  }),
});

const article = await response.json();

if (!response.ok) {
  throw new Error(article.error?.message ?? "Unable to create article");
}

// target_audience is always string[] in article responses.
console.log(article.target_audience);
```

#### GET `/api/v1/articles`

Returns the current user's articles newest first. `offset` defaults to `0`; `limit` defaults to
`20` and accepts values from 1 through 100.

```http
GET /api/v1/articles?offset=0&limit=20
Authorization: Bearer <access_token>
```

```json
{
  "items": [],
  "total": 0,
  "offset": 0,
  "limit": 20
}
```

#### GET `/api/v1/articles/{article_id}`

Returns one article owned by the authenticated user. A missing article or an article owned by a
different user returns `404 Not Found` with the `article_not_found` error code.

#### PATCH `/api/v1/articles/{article_id}`

Updates any non-empty subset of `notes`, `working_title`, `target_audience`, and `article_goal`.
An empty object or an explicit `null` value fails validation with `422 Unprocessable Entity`.
When supplied, `target_audience` replaces the complete existing array.

```json
{
  "working_title": "A revised working title",
  "target_audience": ["Editors", "Publishers"],
  "article_goal": "inform_and_inspire"
}
```

#### DELETE `/api/v1/articles/{article_id}`

Permanently deletes an owned article and returns `204 No Content` with an empty response body.

Possible article endpoint responses:

| Status | Error code | Meaning |
| --- | --- | --- |
| `401 Unauthorized` | `authentication_required` or `invalid_token` | A valid bearer token is required |
| `404 Not Found` | `article_not_found` | The article is missing or belongs to another user |
| `422 Unprocessable Entity` | `validation_error` | A body, path, or pagination value failed validation |

### Article briefs

Article brief endpoints synchronously call Gemini on Vertex AI and may take several seconds. The
backend sends the saved article's working title, notes, target audiences, and goal to Gemini and
validates the generated JSON before saving it. It does not send user credentials or authentication
tokens to Gemini.

#### POST `/api/v1/articles/{article_id}/brief`

Generates the first brief or replaces the current brief. Send no request body. Regeneration keeps
the brief ID but replaces its generated content and metadata.

```http
POST /api/v1/articles/be5579e3-24fd-4272-a35f-f74740c3887e/brief
Authorization: Bearer <access_token>
```

Successful response - `200 OK`:

```json
{
  "id": "ccbfce42-98bf-4f44-b4cf-206cc3661f11",
  "article_id": "be5579e3-24fd-4272-a35f-f74740c3887e",
  "summary": "A practical guide to building a repeatable publishing process for small teams.",
  "core_angle": "Consistency comes from a clear workflow rather than individual discipline.",
  "audience_insights": [
    "Independent writers need a lightweight process they can maintain alone",
    "Small teams need explicit ownership and review stages"
  ],
  "tone_and_style": "Practical, encouraging, and specific",
  "key_takeaways": [
    "Define a small repeatable workflow",
    "Assign ownership for every stage",
    "Review the process using a consistent cadence"
  ],
  "evidence_gaps": ["Examples showing the workflow in use"],
  "call_to_action": "Create a one-page checklist for the next article.",
  "seo": {
    "suggested_titles": [
      "A Repeatable Publishing Process for Small Teams",
      "How Small Teams Can Publish Consistently",
      "Build a Content Workflow Your Team Can Sustain"
    ],
    "primary_keyword": "publishing process",
    "secondary_keywords": ["content workflow", "consistent publishing"],
    "meta_description": "Build a practical publishing process that helps writers and small teams create content consistently."
  },
  "model_id": "gemini-2.5-flash",
  "prompt_version": "article_brief_v1",
  "input_token_count": 275,
  "output_token_count": 640,
  "generation_duration_ms": 3240,
  "is_stale": false,
  "created_at": "2026-08-14T12:00:00Z",
  "updated_at": "2026-08-14T12:00:00Z"
}
```

Frontend example:

```javascript
async function generateBrief(articleId, accessToken) {
  const response = await fetch(
    `http://127.0.0.1:8000/api/v1/articles/${articleId}/brief`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${accessToken}` },
    },
  );
  const body = await response.json();

  if (!response.ok) {
    throw new Error(body.error?.message ?? "Unable to generate brief");
  }

  return body;
}
```

#### GET `/api/v1/articles/{article_id}/brief`

Returns the saved brief without calling Gemini. If the article inputs have changed since generation,
the brief remains available with `is_stale: true`. The frontend should offer regeneration when this
flag is true.

#### PATCH `/api/v1/articles/{article_id}/brief`

Partially updates saved brief content without calling Gemini. Send at least one content field; empty
bodies and explicit `null` values are rejected. Generation metadata is read-only. SEO supports
nested partial updates, so a client can update only `meta_description`, for example:

```json
{
  "core_angle": "Consistency comes from reducing handoffs and making ownership visible.",
  "seo": {
    "meta_description": "Build a visible publishing workflow with clear ownership."
  }
}
```

Changes to non-SEO brief content mark an existing outline stale. SEO-only changes do not.

Possible brief endpoint responses:

| Status | Error code | Meaning |
| --- | --- | --- |
| `401 Unauthorized` | `authentication_required` or `invalid_token` | A valid bearer token is required |
| `404 Not Found` | `article_not_found` or `brief_not_found` | The owned article or its brief does not exist |
| `422 Unprocessable Entity` | `brief_generation_blocked` | Vertex AI rejected the supplied content |
| `502 Bad Gateway` | `brief_generation_failed` | Vertex returned missing or invalid structured output |
| `503 Service Unavailable` | `brief_generation_unavailable` | Vertex configuration, credentials, quota, or service is unavailable |
| `504 Gateway Timeout` | `brief_generation_timeout` | Generation exceeded the configured timeout |

### Article outlines

An outline is generated from the current saved, non-SEO brief content. A brief must exist first.
Outline generation does not accept a request body and does not read directly from frontend-provided
brief fields.

#### POST `/api/v1/articles/{article_id}/outline`

Generates the first outline or replaces the current outline while keeping its ID. Generation is
synchronous and returns `200 OK`:

```json
{
  "id": "a8d789b6-6e71-436e-a981-51d25e66f538",
  "article_id": "be5579e3-24fd-4272-a35f-f74740c3887e",
  "sections": [
    {
      "id": "10000000-0000-4000-8000-000000000000",
      "heading": "Why publishing consistency breaks down",
      "purpose": "Establish the operational causes of inconsistent publishing",
      "key_points": ["Unclear ownership", "Irregular review cycles"]
    },
    {
      "id": "10000000-0000-4000-8000-000000000001",
      "heading": "Design a workflow the team can sustain",
      "purpose": "Show how to choose a minimal set of publishing stages",
      "key_points": ["Limit work in progress", "Define completion criteria"]
    },
    {
      "id": "10000000-0000-4000-8000-000000000002",
      "heading": "Turn the workflow into a habit",
      "purpose": "Give readers a practical implementation path",
      "key_points": ["Choose a cadence", "Review and improve the process"]
    }
  ],
  "model_id": "gemini-2.5-flash",
  "prompt_version": "article_outline_v1",
  "input_token_count": 310,
  "output_token_count": 420,
  "generation_duration_ms": 2410,
  "is_stale": false,
  "created_at": "2026-08-18T12:00:00Z",
  "updated_at": "2026-08-18T12:00:00Z"
}
```

#### GET `/api/v1/articles/{article_id}/outline`

Returns the saved outline without calling Gemini. `is_stale` becomes `true` after a relevant brief
edit or full brief regeneration. The outline remains available and editable.

#### PATCH `/api/v1/articles/{article_id}/outline`

Replaces the complete `sections` array. Supply 3-10 sections; each section requires a non-empty
`heading`, `purpose`, and 1-5 `key_points`. Include `id` to preserve an existing section; omit it
for a new section. Omitted existing sections are deleted, unknown or duplicate IDs are rejected,
and request order is preserved. Editing a stale outline preserves its stale state. Regeneration
creates new section IDs because it is a full semantic replacement.

#### DELETE `/api/v1/articles/{article_id}/outline`

Permanently deletes the outline and returns `204 No Content`.

Possible outline endpoint responses:

| Status | Error code | Meaning |
| --- | --- | --- |
| `401 Unauthorized` | `authentication_required` or `invalid_token` | A valid bearer token is required |
| `404 Not Found` | `article_not_found`, `brief_not_found`, or `outline_not_found` | A required owned resource does not exist |
| `422 Unprocessable Entity` | `validation_error` or `outline_generation_blocked` | Input validation failed or Vertex rejected the brief |
| `502 Bad Gateway` | `outline_generation_failed` | Vertex returned missing or invalid structured output |
| `503 Service Unavailable` | `outline_generation_unavailable` | Vertex configuration, credentials, quota, or service is unavailable |
| `504 Gateway Timeout` | `outline_generation_timeout` | Generation exceeded the configured timeout |

### Article drafts

An article has at most one draft. Draft creation requires a current outline, accepts no request
body, and is idempotent. Each initial draft section copies the outline heading to `title`, purpose
to `goal`, and ID to `outline_section_id`; checklist items start empty and editor content starts as
valid empty Lexical JSON.

#### GET `/api/v1/articles/{article_id}/draft`

Returns the saved draft and reconciles it with the current outline. Linked titles and goals are
refreshed by `outline_section_id` while editor content, checklist progress, section IDs, and draft
ordering are preserved. Removed outline links become `null`; new outline sections append in outline
order. If the outline itself was deleted, the draft remains available with all links set to `null`.

#### POST `/api/v1/articles/{article_id}/draft`

Creates and returns a draft from the current outline, or returns the existing draft unchanged.
Returns `outline_not_found` if an initial draft cannot be created because no outline exists.

#### PATCH `/api/v1/articles/{article_id}/draft`

Replaces the complete ordered section collection. Section and linked outline IDs must be unique,
checklist IDs must be unique within their section, and `editor_state` must contain a valid Lexical
root object. A nullable `outline_section_id` represents a section created directly in the editor.

```json
{
  "id": "30000000-0000-4000-8000-000000000000",
  "article_id": "be5579e3-24fd-4272-a35f-f74740c3887e",
  "sections": [
    {
      "id": "20000000-0000-4000-8000-000000000000",
      "outline_section_id": "10000000-0000-4000-8000-000000000000",
      "title": "Why publishing consistency breaks down",
      "goal": "Establish the operational causes of inconsistent publishing",
      "checklist": [],
      "editor_state": "{\"root\":{\"children\":[],\"direction\":\"ltr\",\"format\":\"\",\"indent\":0,\"type\":\"root\",\"version\":1}}"
    }
  ],
  "created_at": "2026-08-21T12:00:00Z",
  "updated_at": "2026-08-21T12:05:00Z"
}
```

#### POST `/api/v1/articles/{article_id}/draft/sections/{section_id}/talking-points`

Generates three to five concise talking points that complement the selected draft section's saved
content and the rest of the article. `section_id` is the draft section ID, so both outline-linked
and directly-created sections are supported. The generated points are not persisted; the frontend
can insert them into the Lexical editor and save them with the draft `PATCH` endpoint.

The request body is optional. When present, `instruction` can provide up to 1,000 characters of
additional focus:

```json
{
  "instruction": "Focus on the operational costs"
}
```

```json
{
  "section_id": "20000000-0000-4000-8000-000000000000",
  "points": [
    "Clarify who owns each publishing stage.",
    "Show how inconsistent review cycles create delays.",
    "Connect unclear completion criteria to repeated rework."
  ]
}
```

Possible draft endpoint responses:

| Status | Error code | Meaning |
| --- | --- | --- |
| `401 Unauthorized` | `authentication_required` or `invalid_token` | A valid bearer token is required |
| `404 Not Found` | `article_not_found`, `brief_not_found`, `outline_not_found`, `draft_not_found`, or `draft_section_not_found` | A required owned resource does not exist |
| `422 Unprocessable Entity` | `validation_error` or `talking_points_generation_blocked` | Input validation failed or Vertex rejected the article content |
| `502 Bad Gateway` | `talking_points_generation_failed` | Vertex returned invalid talking-point output |
| `503 Service Unavailable` | `talking_points_generation_unavailable` | Vertex configuration, credentials, quota, or service is unavailable |
| `504 Gateway Timeout` | `talking_points_generation_timeout` | Talking-point generation exceeded the configured timeout |
