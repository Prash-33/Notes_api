# Notes App — Backend API

A multi-user notes service built with Django REST Framework.

## Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | /register | No | Register new user |
| POST | /login | No | Login, get JWT (rate limited: 5/min/IP) |
| GET | /notes | Yes | List all notes (owned + shared). Supports ?page=1&page_size=10 |
| POST | /notes | Yes | Create a note |
| GET | /notes/{id} | Yes | Get a specific note |
| PUT | /notes/{id} | Yes | Update a note (owner only) |
| DELETE | /notes/{id} | Yes | Soft-delete a note (owner only) |
| POST | /notes/{id}/share | Yes | Share a note by email (owner only) |
| GET | /notes/{id}/activity | Yes | Activity log for a note |
| GET | /trash | Yes | View soft-deleted notes |
| POST | /notes/{id}/restore | Yes | Restore a trashed note |
| GET | /search?q=keyword | Yes | Full-text search |
| GET | /about | No | About + custom features |
| GET | /openapi.json | No | OpenAPI 3.0 spec |

## Custom Features

1. **Rate Limiting** — 5 failed login attempts per IP per minute triggers a 429 response.
2. **Soft Delete + Trash + Restore** — Deleted notes go to trash, not permanently removed.
3. **Activity Log** — Every view/create/update/delete/share action is recorded per note.
4. **Pagination** — GET /notes supports page and page_size query params.
5. **Full-Text Search** — GET /search?q=keyword searches title and content.

## Deploy to Render

1. Push this repo to GitHub.
2. Create a new **Web Service** on [render.com](https://render.com).
3. Set **Build Command**: `./build.sh`
4. Set **Start Command**: `gunicorn notes_project.wsgi --log-file -`
5. Add environment variables:
   - `SECRET_KEY` = a long random string
   - `DEBUG` = False
   - `ALLOWED_HOSTS` = your-app.render.com

## Local Development

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```
