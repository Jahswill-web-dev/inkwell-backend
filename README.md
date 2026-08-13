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

### 3. Start PostgreSQL

```powershell
docker compose up -d postgres
```

The development database will be available on port `5432` using the connection configured in
`.env`.

### 4. Apply database migrations

```powershell
uv run alembic upgrade head
```

### 5. Start the API

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
Article intake routes persist the source material needed by the future AI brief-generation flow.
Other article-generation routes remain scaffolds and do not expose operations yet.

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
choices but do not generate a brief yet. Every read and mutation is scoped to the authenticated
user.

The five accepted `article_goal` values map to frontend labels as follows:

| API value | Display label |
| --- | --- |
| `inform_and_inspire` | Inform and inspire |
| `educate_with_practical_guidance` | Educate with practical guidance |
| `persuade_or_change_a_perspective` | Persuade or change a perspective |
| `inspire_readers_to_take_action` | Inspire readers to take action |
| `entertain_with_a_compelling_story` | Entertain with a compelling story |

All text fields are trimmed and must contain non-whitespace content. `notes` accepts at most
20,000 characters, `working_title` at most 200, and `target_audience` at most 500.

#### POST `/api/v1/articles`

Creates an article intake. All four fields are required.

```json
{
  "notes": "Research notes and an early idea",
  "working_title": "How small teams can publish consistently",
  "target_audience": "Independent writers and small content teams",
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
  "target_audience": "Independent writers and small content teams",
  "article_goal": "educate_with_practical_guidance",
  "created_at": "2026-08-12T12:00:00Z",
  "updated_at": "2026-08-12T12:00:00Z"
}
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

```json
{
  "working_title": "A revised working title",
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
