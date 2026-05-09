# Content Queue

A full-stack reading queue application with AI-powered semantic search. Save articles, organize them into lists, and discover related content using OpenAI embeddings.

![Tech Stack](https://img.shields.io/badge/Next.js-15-black)
![Tech Stack](https://img.shields.io/badge/FastAPI-0.110-009688)
![Tech Stack](https://img.shields.io/badge/PostgreSQL-16-4169E1)
![Tech Stack](https://img.shields.io/badge/Python-3.11+-3776AB)

## Features

- **Smart Content Saving**: Automatically extract metadata, thumbnails, and full text from URLs
- **Reading Queue Management**: Mark articles as read, archive, and track reading progress
- **Custom Lists**: Organize content into custom collections
- **Responsive Views**: Seamlessly toggle between comprehensive card views and ultra-dense index grid views based on device or preference
- **Semantic Search**: Find similar articles using AI-powered embeddings (OpenAI)
- **Rich Metadata**: Automatic extraction of title, description, reading time, word count
- **Responsive Design**: Works on desktop, tablet, and mobile
- **Background Processing**: Celery workers handle content extraction asynchronously

## Tech Stack

### Frontend
- **Framework**: Next.js 15 (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS v4
- **State Management**: React Context API
- **Authentication**: JWT tokens stored in localStorage

### Backend
- **Framework**: FastAPI (Python)
- **Database**: PostgreSQL with pgvector extension
- **Cache**: Redis
- **Task Queue**: Celery
- **AI/ML**: OpenAI API (text-embedding-3-small model)
- **Authentication**: JWT tokens with OAuth2

### Infrastructure
- **Web Scraping**: Trafilatura for content extraction
- **Vector Search**: pgvector for similarity queries
- **Rate Limiting**: Custom middleware with Redis backend

## Project Structure

content-queue/
├── frontend/                 # Next.js application
│   ├── app/                 # App router pages
│   │   ├── dashboard/       # Main dashboard
│   │   ├── lists/          # Lists management
│   │   ├── content/[id]/   # Article reader
│   │   ├── login/          # Authentication
│   │   └── register/
│   ├── components/          # Reusable React components
│   ├── contexts/            # React Context providers
│   ├── lib/                # API client and utilities
│   └── types/              # TypeScript type definitions
│
└── content-queue-backend/   # FastAPI application
├── app/
│   ├── api/            # API route handlers
│   ├── models/         # SQLAlchemy models
│   ├── schemas/        # Pydantic schemas
│   ├── core/           # Config, auth, database
│   ├── services/       # Business logic
│   ├── tasks/          # Celery background tasks
│   └── middleware/     # Rate limiting, CORS
└── alembic/            # Database migrations


## Getting Started

### Prerequisites

- **Node.js**: 18+ and npm
- **Python**: 3.11+
- **Poetry**: For Python dependency management ([Install Poetry](https://python-poetry.org/docs/#installation))
- **Docker & Docker Compose**: For running PostgreSQL and Redis
- **OpenAI API Key**: For semantic search features

### Quick Start with Docker Compose

The easiest way to get started is using Docker Compose to run PostgreSQL and Redis:

1. **Start infrastructure services:**
   ```bash
   # From project root directory
   docker-compose up -d
   ```

   This starts:
   - PostgreSQL 16 with pgvector extension on port **5433**
   - Redis 7 on port **6379**

2. **Verify services are running:**
   ```bash
   docker-compdose ps
   ```

### Backend Setup

1. **Navigate to backend directory:**
   ```bash
   cd content-queue-backend
   ```

2. **Install Poetry** (if not already installed):
   ```bash
   curl -sSL https://install.python-poetry.org | python3 -
   ```

3. **Install dependencies using Poetry:**
   ```bash
   poetry install
   ```

   This creates a virtual environment and installs all dependencies from `pyproject.toml`.

4. **Activate the Poetry shell:**
   ```bash
   poetry shell
   ```

5. **Create `.env` file in the backend directory:**
   ```bash
   # Create .env file with these contents
   DATABASE_URL=postgresql://postgres:postgres@localhost:5433/content_queue
   REDIS_URL=redis://localhost:6379/0
   SECRET_KEY=your-secret-key-here  # Generate with: openssl rand -hex 32
   ALGORITHM=HS256
   ACCESS_TOKEN_EXPIRE_MINUTES=10080
   OPENAI_API_KEY=your-openai-api-key
   DEBUG=True
   ```

   **Note**: The database runs on port **5433** (not 5432) to avoid conflicts with existing PostgreSQL installations.

6. **Run database migrations:**
   ```bash
   alembic upgrade head
   ```

   This creates all necessary tables in the PostgreSQL database.

7. **Start the FastAPI server:**
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

8. **Start Celery worker** (in a new terminal):
   ```bash
   cd content-queue-backend
   poetry shell
   celery -A app.core.celery_app worker --loglevel=info
   ```

### Frontend Setup

1. **Navigate to frontend directory:**

   ```bash
   cd frontend
   ```

2. **Install dependencies:**

   ```bash
   npm install
   ```

3. **Create .env.local file (if needed):**

   ```bash
   NEXT_PUBLIC_API_URL=http://localhost:8000
   ````

4. **Start development server:**

   ```bash
   npm run dev
   ````

5. **Open your browser:**

Navigate to http://localhost:3000


First-Time Setup
Register a new account at /register
Login at /login
Start adding content by pasting URLs on the dashboard

## API Documentation

Once the backend is running, visit:

Swagger UI: http://localhost:8000/docs
ReDoc: http://localhost:8000/redoc

## Key Features Explained

### Content Extraction
When you save a URL, the backend:

1. Validates the URL
2. Queues a Celery task for background processing
3. Extracts metadata (title, description, thumbnail)
4. Downloads and parses full article text using Trafilatura
5. Calculates word count and estimated reading time
6. Generates OpenAI embeddings for semantic search

### Semantic Search
Uses OpenAI's text-embedding-3-small model to:

- Generate 1536-dimensional vectors for each article
- Store vectors in PostgreSQL using pgvector
- Perform similarity searches with cosine distance
- Find related articles based on content similarity

### Lists & Organization

- Create unlimited custom lists
- Add multiple articles to lists
- Track content count per list
- Filter articles by read/unread/archived status

## Development

### Running Tests

```bash
# Backend tests (using Poetry)
cd content-queue-backend
poetry shell
pytest

# Frontend tests
cd frontend
npm test
```

### Database Migrations

```bash
# Make sure you're in the backend directory with Poetry shell active
cd content-queue-backend
poetry shell

# Create a new migration
alembic revision --autogenerate -m "Description of changes"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# View migration history
alembic history
```

### Code Formatting

```bash
# Backend (Python) - using Poetry
cd content-queue-backend
poetry shell

# Format with Black
poetry run black app/

# Lint with Ruff
poetry run ruff check app/

# Frontend (TypeScript)
cd frontend
npm run lint
```

### Adding Dependencies

```bash
# Backend - Add a new Python package
cd content-queue-backend
poetry add package-name

# Backend - Add a dev dependency
poetry add --group dev package-name

# Frontend - Add a new npm package
cd frontend
npm install package-name
```

### Docker Compose Commands

```bash
# Start all services in background
docker-compose up -d

# View logs
docker-compose logs -f

# Stop all services
docker-compose down

# Stop and remove volumes (deletes data!)
docker-compose down -v

# Restart a specific service
docker-compose restart postgres
docker-compose restart redis
```

## Deployment

Canonical deployment and operations workflow:

- `docs/engineering-workflow.md`

### Backend (Railway)

1. Connect your GitHub repository
2. Add PostgreSQL and Redis services
3. Set environment variables in Railway dashboard
4. Deploy main service and Celery worker as separate services

### Frontend (Vercel)

1. Connect your GitHub repository
2. Set NEXT_PUBLIC_API_URL to your Railway backend URL
3. Deploy automatically on push to main

See `docs/engineering-workflow.md` for the full development → CI → deploy workflow.

## Environment Variables

### Backend (.env)

| Variable | Description | Example |
|----------|-------------|---------|
| DATABASE_URL | PostgreSQL connection string | `postgresql://user:pass@localhost/db` |
| REDIS_URL | Redis connection string | `redis://localhost:6379/0` |
| SECRET_KEY | JWT signing key | Generate with `openssl rand -hex 32` |
| ALGORITHM | JWT algorithm | `HS256` |
| ACCESS_TOKEN_EXPIRE_MINUTES | Token expiration | `10080` (7 days) |
| OPENAI_API_KEY | OpenAI API key | `sk-...` |
| DEBUG | Debug mode | `True` or `False` |

### Frontend (.env.local)

| Variable | Description | Example |
|----------|-------------|---------|
| NEXT_PUBLIC_API_URL | Backend API URL | `http://localhost:8000` |

## Troubleshooting

### Backend Issues

Problem: ModuleNotFoundError: No module named 'app'

Solution: Make sure you're in the content-queue-backend directory and virtual environment is activated
Problem: Celery worker not processing tasks

Solution: Ensure Redis is running and REDIS_URL is correct in .env

### Frontend Issues
Problem: API error: 401 on all requests

Solution: Token expired. Clear localStorage and log in again
Problem: CORS error when calling API

Solution: Check that allow_origins in backend/app/main.py includes your frontend URL
Problem: Hydration errors in Next.js

Solution: Ensure client-side only data (like dates) are handled with useEffect hooks

## Contributing
Fork the repository
Create a feature branch (git checkout -b feature/amazing-feature)
Commit your changes (git commit -m 'Add amazing feature')
Push to the branch (git push origin feature/amazing-feature)
Open a Pull Request

## License
This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments
Built with Next.js
Backend powered by FastAPI
Content extraction using Trafilatura
AI embeddings by OpenAI
Vector search with pgvector
