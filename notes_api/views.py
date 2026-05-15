from django.utils import timezone
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate
from django.core.cache import cache

from .models import User, Note, SharedNote, ActivityLog
from .serializers import RegisterSerializer, NoteSerializer, ActivityLogSerializer


# ─── helpers ────────────────────────────────────────────────────────────────

def log_activity(note, user, action, detail=""):
    ActivityLog.objects.create(note=note, user=user, action=action, detail=detail)


def get_accessible_note(note_id, user):
    """Return note if user is owner or it was shared with them, else None."""
    try:
        note = Note.objects.get(pk=note_id, is_deleted=False)
    except Note.DoesNotExist:
        return None
    if note.owner == user:
        return note
    if note.shared_with.filter(shared_with=user).exists():
        return note
    return None


# ─── auth ───────────────────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    serializer = RegisterSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({"message": "User registered successfully."}, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    # ── Rate limiting: max 5 attempts per IP per minute ──
    ip = request.META.get("REMOTE_ADDR", "unknown")
    cache_key = f"login_attempts_{ip}"
    attempts = cache.get(cache_key, 0)
    if attempts >= 5:
        return Response(
            {"message": "Too many login attempts. Please try again after 1 minute."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    email = request.data.get("email", "").strip()
    password = request.data.get("password", "")

    if not email or not password:
        return Response({"message": "Email and password are required."}, status=status.HTTP_400_BAD_REQUEST)

    user = authenticate(request, username=email, password=password)
    if user is None:
        cache.set(cache_key, attempts + 1, timeout=60)
        return Response({"message": "Invalid email or password."}, status=status.HTTP_401_UNAUTHORIZED)

    # Reset on success
    cache.delete(cache_key)
    refresh = RefreshToken.for_user(user)
    return Response({"access_token": str(refresh.access_token)}, status=status.HTTP_200_OK)


# ─── notes ──────────────────────────────────────────────────────────────────

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def notes_list(request):
    if request.method == "GET":
        # owned notes + notes shared with user
        owned = Note.objects.filter(owner=request.user, is_deleted=False)
        shared_ids = SharedNote.objects.filter(shared_with=request.user).values_list("note_id", flat=True)
        shared = Note.objects.filter(id__in=shared_ids, is_deleted=False)
        notes = (owned | shared).distinct().order_by("-created_at")

        # Pagination
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 10))
        if page < 1 or page_size < 1 or page_size > 100:
            return Response({"message": "Invalid pagination parameters."}, status=status.HTTP_400_BAD_REQUEST)
        total = notes.count()
        start = (page - 1) * page_size
        end = start + page_size
        serializer = NoteSerializer(notes[start:end], many=True)
        return Response({
            "total": total,
            "page": page,
            "page_size": page_size,
            "results": serializer.data,
        })

    # POST
    serializer = NoteSerializer(data=request.data)
    if serializer.is_valid():
        note = serializer.save(owner=request.user)
        log_activity(note, request.user, "created")
        return Response(NoteSerializer(note).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET", "PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def note_detail(request, note_id):
    note = get_accessible_note(note_id, request.user)
    if note is None:
        return Response({"message": "Note not found or access denied."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        log_activity(note, request.user, "viewed")
        return Response(NoteSerializer(note).data)

    # Only owner can edit/delete
    if note.owner != request.user:
        return Response({"message": "Only the note owner can perform this action."}, status=status.HTTP_403_FORBIDDEN)

    if request.method in ("PUT", "PATCH"):
        partial = request.method == "PATCH"
        serializer = NoteSerializer(note, data=request.data, partial=partial)
        if serializer.is_valid():
            updated = serializer.save()
            log_activity(updated, request.user, "updated")
            return Response(NoteSerializer(updated).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    if request.method == "DELETE":
        note.is_deleted = True
        note.deleted_at = timezone.now()
        note.save()
        log_activity(note, request.user, "deleted")
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def share_note(request, note_id):
    try:
        note = Note.objects.get(pk=note_id, is_deleted=False)
    except Note.DoesNotExist:
        return Response({"message": "Note not found."}, status=status.HTTP_404_NOT_FOUND)

    if note.owner != request.user:
        return Response({"message": "Only the note owner can share it."}, status=status.HTTP_403_FORBIDDEN)

    share_email = request.data.get("share_with_email", "").strip()
    if not share_email:
        return Response({"message": "share_with_email is required."}, status=status.HTTP_400_BAD_REQUEST)

    if share_email == request.user.email:
        return Response({"message": "You cannot share a note with yourself."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        target_user = User.objects.get(email=share_email)
    except User.DoesNotExist:
        return Response({"message": "User with this email does not exist."}, status=status.HTTP_404_NOT_FOUND)

    _, created = SharedNote.objects.get_or_create(note=note, shared_with=target_user)
    if not created:
        return Response({"message": "Note already shared with this user."}, status=status.HTTP_200_OK)

    log_activity(note, request.user, "shared", detail=f"Shared with {share_email}")
    return Response({"message": f"Note successfully shared with {share_email}."}, status=status.HTTP_200_OK)


# ─── activity log ───────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def note_activity(request, note_id):
    note = get_accessible_note(note_id, request.user)
    if note is None:
        return Response({"message": "Note not found or access denied."}, status=status.HTTP_404_NOT_FOUND)
    logs = note.activity_logs.all()
    return Response(ActivityLogSerializer(logs, many=True).data)


# ─── soft delete: trash & restore ───────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def trash(request):
    notes = Note.objects.filter(owner=request.user, is_deleted=True).order_by("-deleted_at")
    return Response(NoteSerializer(notes, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def restore_note(request, note_id):
    try:
        note = Note.objects.get(pk=note_id, owner=request.user, is_deleted=True)
    except Note.DoesNotExist:
        return Response({"message": "Note not found in trash."}, status=status.HTTP_404_NOT_FOUND)
    note.is_deleted = False
    note.deleted_at = None
    note.save()
    log_activity(note, request.user, "restored")
    return Response({"message": "Note restored successfully.", "note": NoteSerializer(note).data})


# ─── search ─────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def search_notes(request):
    q = request.query_params.get("q", "").strip()
    if not q:
        return Response({"message": "Query parameter 'q' is required."}, status=status.HTTP_400_BAD_REQUEST)

    owned = Note.objects.filter(owner=request.user, is_deleted=False)
    shared_ids = SharedNote.objects.filter(shared_with=request.user).values_list("note_id", flat=True)
    shared = Note.objects.filter(id__in=shared_ids, is_deleted=False)
    results = (owned | shared).distinct().filter(
        Q(title__icontains=q) | Q(content__icontains=q)
    ).order_by("-updated_at")

    return Response(NoteSerializer(results, many=True).data)


# ─── meta ────────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([AllowAny])
def about(request):
    return Response({
        "name": "Prashanth",
        "email": "prashanthsiva3@gmail.com",
        "my_features": {
            "Rate Limiting on /login": (
                "Blocks an IP after 5 failed login attempts within 1 minute. "
                "Chosen to prevent brute-force attacks and secure user accounts."
            ),
            "Soft Delete with Trash & Restore": (
                "DELETE moves a note to trash instead of permanently removing it. "
                "GET /trash lists deleted notes; POST /notes/{id}/restore brings them back. "
                "Mirrors real-world app behavior and prevents accidental data loss."
            ),
            "Activity Log": (
                "Every view, create, update, delete, restore, and share action on a note "
                "is recorded with the acting user and timestamp. "
                "GET /notes/{id}/activity returns the full history. "
                "Chosen to support auditability and collaboration transparency."
            ),
            "Pagination on GET /notes": (
                "Supports ?page=1&page_size=10 query params. "
                "Returns total count, current page, and results. "
                "Prevents large payloads and improves performance at scale."
            ),
            "Full-Text Search": (
                "GET /search?q=keyword searches across title and content of all accessible notes. "
                "Chosen for discoverability and real-world usability."
            ),
        },
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def openapi_json(request):
    base = request.build_absolute_uri("/")
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Notes API", "version": "1.0.0", "description": "Multi-user notes service"},
        "servers": [{"url": base}],
        "components": {
            "securitySchemes": {
                "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
            },
            "schemas": {
                "Note": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "created_at": {"type": "string", "format": "date-time"},
                        "updated_at": {"type": "string", "format": "date-time"},
                    },
                },
                "ActivityLog": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "user_email": {"type": "string"},
                        "action": {"type": "string"},
                        "detail": {"type": "string"},
                        "timestamp": {"type": "string", "format": "date-time"},
                    },
                },
            },
        },
        "paths": {
            "/register": {
                "post": {
                    "summary": "Register a new user",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "required": ["email", "password"], "properties": {"email": {"type": "string"}, "password": {"type": "string"}}}}}},
                    "responses": {"201": {"description": "User registered"}, "400": {"description": "Validation error"}},
                }
            },
            "/login": {
                "post": {
                    "summary": "Login and get JWT token (rate limited: 5 attempts/min per IP)",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "required": ["email", "password"], "properties": {"email": {"type": "string"}, "password": {"type": "string"}}}}}},
                    "responses": {"200": {"description": "JWT access token"}, "401": {"description": "Invalid credentials"}, "429": {"description": "Too many attempts"}},
                }
            },
            "/notes": {
                "get": {
                    "summary": "Get all notes (owned + shared). Supports ?page=1&page_size=10",
                    "security": [{"BearerAuth": []}],
                    "parameters": [
                        {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
                        {"name": "page_size", "in": "query", "schema": {"type": "integer", "default": 10}},
                    ],
                    "responses": {"200": {"description": "Paginated list of notes"}},
                },
                "post": {
                    "summary": "Create a new note",
                    "security": [{"BearerAuth": []}],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "required": ["title", "content"], "properties": {"title": {"type": "string"}, "content": {"type": "string"}}}}}},
                    "responses": {"201": {"description": "Note created"}, "400": {"description": "Validation error"}},
                },
            },
            "/notes/{id}": {
                "get": {
                    "summary": "Get a specific note by ID",
                    "security": [{"BearerAuth": []}],
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Note data"}, "404": {"description": "Not found"}},
                },
                "put": {
                    "summary": "Update a note (owner only)",
                    "security": [{"BearerAuth": []}],
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {"title": {"type": "string"}, "content": {"type": "string"}}}}}},
                    "responses": {"200": {"description": "Updated note"}, "403": {"description": "Forbidden"}, "404": {"description": "Not found"}},
                },
                "delete": {
                    "summary": "Soft-delete a note (moves to trash, owner only)",
                    "security": [{"BearerAuth": []}],
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"204": {"description": "Deleted"}, "403": {"description": "Forbidden"}, "404": {"description": "Not found"}},
                },
            },
            "/notes/{id}/share": {
                "post": {
                    "summary": "Share a note with another user by email (owner only)",
                    "security": [{"BearerAuth": []}],
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "required": ["share_with_email"], "properties": {"share_with_email": {"type": "string"}}}}}},
                    "responses": {"200": {"description": "Shared"}, "404": {"description": "User or note not found"}},
                }
            },
            "/notes/{id}/activity": {
                "get": {
                    "summary": "Get activity log for a note (owner or shared user)",
                    "security": [{"BearerAuth": []}],
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "List of activity log entries"}},
                }
            },
            "/trash": {
                "get": {
                    "summary": "Get all soft-deleted notes for the authenticated user",
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {"description": "List of trashed notes"}},
                }
            },
            "/notes/{id}/restore": {
                "post": {
                    "summary": "Restore a soft-deleted note from trash (owner only)",
                    "security": [{"BearerAuth": []}],
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Note restored"}, "404": {"description": "Not in trash"}},
                }
            },
            "/search": {
                "get": {
                    "summary": "Full-text search across title and content of accessible notes",
                    "security": [{"BearerAuth": []}],
                    "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Matching notes"}},
                }
            },
            "/about": {
                "get": {
                    "summary": "About this API and its custom features",
                    "responses": {"200": {"description": "About info"}},
                }
            },
            "/openapi.json": {
                "get": {
                    "summary": "OpenAPI 3.0 specification",
                    "responses": {"200": {"description": "OpenAPI spec JSON"}},
                }
            },
        },
    }
    return Response(spec)
