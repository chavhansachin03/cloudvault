"""
CloudVault - a self-hosted, Google-Drive-style file storage app with
client-side encryption.

Every file is encrypted with a key derived from the user's own password
BEFORE it is written to disk (storage/). The storage backend, and anyone
who can read the storage/ folder or the database, only ever sees ciphertext.

"""

import mimetypes
import os
import secrets
import uuid
from datetime import datetime
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file, jsonify, abort, Response
)
from werkzeug.security import generate_password_hash, check_password_hash

from database import get_db, init_db
from encryption import generate_salt, derive_key, encrypt_bytes, decrypt_bytes, InvalidToken

BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "storage"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("CLOUDVAULT_SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024  # 512 MB per request, adjust as needed

# ---------------------------------------------------------------------------
# In-memory session -> encryption key map.
#
# We deliberately do NOT put the derived encryption key inside the Flask
# session cookie: Flask's default session is signed but NOT encrypted, so
# its contents are readable by anyone who has the cookie. Instead we keep a
# random opaque token in the (httponly) cookie, and hold the real key only
# in server RAM, indexed by that token. Restarting the server clears
# everyone's key (they simply log in again) - that trade-off is fine for a
# self-hosted / demo app. For a multi-process production deployment you'd
# replace this dict with a server-side session store (e.g. Redis) that is
# itself encrypted at rest.
# ---------------------------------------------------------------------------
SESSION_STORE = {}  # token -> {"user_id": int, "username": str, "key": bytes}
COOKIE_NAME = "cv_session"


def current_session():
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return SESSION_STORE.get(token)


def login_required(view):
    def wrapped(*args, **kwargs):
        if current_session() is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    wrapped.__name__ = view.__name__
    return wrapped


def user_storage_path(user_id: int) -> Path:
    p = STORAGE_DIR / str(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def build_breadcrumb(db, folder_id, user_id):
    crumbs = []
    fid = folder_id
    while fid:
        row = db.execute(
            "SELECT id, name, parent_id FROM folders WHERE id = ? AND user_id = ?",
            (fid, user_id),
        ).fetchone()
        if not row:
            break
        crumbs.insert(0, {"id": row["id"], "name": row["name"]})
        fid = row["parent_id"]
    return crumbs


def all_descendant_folder_ids(db, folder_id, user_id):
    """Recursively collect a folder and all its sub-folder ids."""
    ids = [folder_id]
    children = db.execute(
        "SELECT id FROM folders WHERE parent_id = ? AND user_id = ?",
        (folder_id, user_id),
    ).fetchall()
    for c in children:
        ids.extend(all_descendant_folder_ids(db, c["id"], user_id))
    return ids


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")

    if len(username) < 3:
        return render_template("register.html", error="Username must be at least 3 characters.")
    if len(password) < 8:
        return render_template("register.html", error="Password must be at least 8 characters.")
    if password != confirm:
        return render_template("register.html", error="Passwords do not match.")

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        db.close()
        return render_template("register.html", error="That username is already taken.")

    salt = generate_salt()
    password_hash = generate_password_hash(password)
    db.execute(
        "INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)",
        (username, password_hash, salt),
    )
    db.commit()
    db.close()
    return redirect(url_for("login", registered="1"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", registered=request.args.get("registered"))

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    db.close()

    if not user or not check_password_hash(user["password_hash"], password):
        return render_template("login.html", error="Invalid username or password.")

    # Derive this user's encryption key from their password + stored salt.
    # This key never touches the database or the browser - it lives only
    # in server memory for the lifetime of this login session.
    key = derive_key(password, user["salt"])

    token = secrets.token_urlsafe(32)
    SESSION_STORE[token] = {"user_id": user["id"], "username": user["username"], "key": key}

    resp = redirect(url_for("index"))
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="Lax")
    return resp


@app.route("/logout")
def logout():
    token = request.cookies.get(COOKIE_NAME)
    if token:
        SESSION_STORE.pop(token, None)
    resp = redirect(url_for("login"))
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ---------------------------------------------------------------------------
# Drive UI
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    sess = current_session()
    db = get_db()

    folder_id = request.args.get("folder", type=int)
    query = request.args.get("q", "").strip()

    if query:
        files = db.execute(
            "SELECT * FROM files WHERE user_id = ? AND original_name LIKE ? ORDER BY uploaded_at DESC",
            (sess["user_id"], f"%{query}%"),
        ).fetchall()
        folders = db.execute(
            "SELECT * FROM folders WHERE user_id = ? AND name LIKE ? ORDER BY name",
            (sess["user_id"], f"%{query}%"),
        ).fetchall()
        breadcrumb = []
    else:
        if folder_id:
            valid = db.execute(
                "SELECT id FROM folders WHERE id = ? AND user_id = ?",
                (folder_id, sess["user_id"]),
            ).fetchone()
            if not valid:
                folder_id = None

        files = db.execute(
            "SELECT * FROM files WHERE user_id = ? AND folder_id IS ? ORDER BY uploaded_at DESC",
            (sess["user_id"], folder_id),
        ).fetchall()
        folders = db.execute(
            "SELECT * FROM folders WHERE user_id = ? AND parent_id IS ? ORDER BY name",
            (sess["user_id"], folder_id),
        ).fetchall()
        breadcrumb = build_breadcrumb(db, folder_id, sess["user_id"])

    total_bytes = db.execute(
        "SELECT COALESCE(SUM(size), 0) AS total FROM files WHERE user_id = ?",
        (sess["user_id"],),
    ).fetchone()["total"]

    file_count = db.execute(
        "SELECT COUNT(*) AS c FROM files WHERE user_id = ?", (sess["user_id"],)
    ).fetchone()["c"]

    db.close()

    return render_template(
        "index.html",
        files=files,
        folders=folders,
        current_folder=folder_id,
        breadcrumb=breadcrumb,
        username=sess["username"],
        storage_used=human_size(total_bytes),
        file_count=file_count,
        search_query=query,
        human_size=human_size,
    )


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

@app.route("/folder/create", methods=["POST"])
@login_required
def create_folder():
    sess = current_session()
    name = request.form.get("name", "").strip() or "Untitled folder"
    parent_id = request.form.get("parent_id", type=int)

    db = get_db()
    db.execute(
        "INSERT INTO folders (user_id, name, parent_id) VALUES (?, ?, ?)",
        (sess["user_id"], name, parent_id),
    )
    db.commit()
    db.close()
    return redirect(url_for("index", folder=parent_id) if parent_id else url_for("index"))


@app.route("/folder/<int:folder_id>/rename", methods=["POST"])
@login_required
def rename_folder(folder_id):
    sess = current_session()
    new_name = request.form.get("name", "").strip()
    db = get_db()
    folder = db.execute(
        "SELECT * FROM folders WHERE id = ? AND user_id = ?", (folder_id, sess["user_id"])
    ).fetchone()
    if not folder:
        db.close()
        abort(404)
    if new_name:
        db.execute("UPDATE folders SET name = ? WHERE id = ?", (new_name, folder_id))
        db.commit()
    parent_id = folder["parent_id"]
    db.close()
    return redirect(url_for("index", folder=parent_id) if parent_id else url_for("index"))


@app.route("/folder/<int:folder_id>/delete", methods=["POST"])
@login_required
def delete_folder(folder_id):
    sess = current_session()
    db = get_db()
    folder = db.execute(
        "SELECT * FROM folders WHERE id = ? AND user_id = ?", (folder_id, sess["user_id"])
    ).fetchone()
    if not folder:
        db.close()
        abort(404)

    parent_id = folder["parent_id"]
    ids_to_delete = all_descendant_folder_ids(db, folder_id, sess["user_id"])

    placeholders = ",".join("?" * len(ids_to_delete))
    files_to_delete = db.execute(
        f"SELECT * FROM files WHERE folder_id IN ({placeholders}) AND user_id = ?",
        (*ids_to_delete, sess["user_id"]),
    ).fetchall()

    storage_dir = user_storage_path(sess["user_id"])
    for f in files_to_delete:
        try:
            (storage_dir / f["stored_name"]).unlink(missing_ok=True)
        except OSError:
            pass

    db.execute(f"DELETE FROM files WHERE folder_id IN ({placeholders}) AND user_id = ?",
               (*ids_to_delete, sess["user_id"]))
    db.execute(f"DELETE FROM folders WHERE id IN ({placeholders}) AND user_id = ?",
               (*ids_to_delete, sess["user_id"]))
    db.commit()
    db.close()
    return redirect(url_for("index", folder=parent_id) if parent_id else url_for("index"))


# ---------------------------------------------------------------------------
# Files: upload / download / preview / rename / delete
# ---------------------------------------------------------------------------

@app.route("/upload", methods=["POST"])
@login_required
def upload():
    sess = current_session()
    folder_id = request.form.get("folder_id", type=int)
    uploaded_files = request.files.getlist("files")

    if not uploaded_files:
        return jsonify({"ok": False, "error": "No files received."}), 400

    db = get_db()
    storage_dir = user_storage_path(sess["user_id"])
    saved = []

    for f in uploaded_files:
        if not f or f.filename == "":
            continue
        plaintext = f.read()
        ciphertext = encrypt_bytes(sess["key"], plaintext)

        stored_name = uuid.uuid4().hex
        with open(storage_dir / stored_name, "wb") as out:
            out.write(ciphertext)

        mime_type = f.mimetype or mimetypes.guess_type(f.filename)[0] or "application/octet-stream"

        db.execute(
            """INSERT INTO files (user_id, folder_id, original_name, stored_name, mime_type, size)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sess["user_id"], folder_id, f.filename, stored_name, mime_type, len(plaintext)),
        )
        saved.append(f.filename)

    db.commit()
    db.close()
    return jsonify({"ok": True, "saved": saved})


def _get_owned_file(db, file_id, user_id):
    row = db.execute(
        "SELECT * FROM files WHERE id = ? AND user_id = ?", (file_id, user_id)
    ).fetchone()
    return row


def _decrypt_file_row(sess, row):
    storage_dir = user_storage_path(sess["user_id"])
    path = storage_dir / row["stored_name"]
    with open(path, "rb") as fh:
        ciphertext = fh.read()
    try:
        return decrypt_bytes(sess["key"], ciphertext)
    except InvalidToken:
        abort(500, description="Decryption failed - wrong key or corrupted data.")


@app.route("/file/<int:file_id>/download")
@login_required
def download_file(file_id):
    sess = current_session()
    db = get_db()
    row = _get_owned_file(db, file_id, sess["user_id"])
    db.close()
    if not row:
        abort(404)
    plaintext = _decrypt_file_row(sess, row)
    return Response(
        plaintext,
        mimetype=row["mime_type"] or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{row["original_name"]}"'},
    )


@app.route("/file/<int:file_id>/raw")
@login_required
def raw_file(file_id):
    """Serves decrypted bytes inline, for previewing images/video/audio/pdf/text."""
    sess = current_session()
    db = get_db()
    row = _get_owned_file(db, file_id, sess["user_id"])
    db.close()
    if not row:
        abort(404)
    plaintext = _decrypt_file_row(sess, row)
    return Response(
        plaintext,
        mimetype=row["mime_type"] or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{row["original_name"]}"'},
    )


@app.route("/file/<int:file_id>/rename", methods=["POST"])
@login_required
def rename_file(file_id):
    sess = current_session()
    new_name = request.form.get("name", "").strip()
    db = get_db()
    row = _get_owned_file(db, file_id, sess["user_id"])
    if not row:
        db.close()
        abort(404)
    if new_name:
        db.execute("UPDATE files SET original_name = ? WHERE id = ?", (new_name, file_id))
        db.commit()
    folder_id = row["folder_id"]
    db.close()
    return redirect(url_for("index", folder=folder_id) if folder_id else url_for("index"))


@app.route("/file/<int:file_id>/delete", methods=["POST"])
@login_required
def delete_file(file_id):
    sess = current_session()
    db = get_db()
    row = _get_owned_file(db, file_id, sess["user_id"])
    if not row:
        db.close()
        abort(404)
    storage_dir = user_storage_path(sess["user_id"])
    (storage_dir / row["stored_name"]).unlink(missing_ok=True)
    db.execute("DELETE FROM files WHERE id = ?", (file_id,))
    db.commit()
    folder_id = row["folder_id"]
    db.close()
    return redirect(url_for("index", folder=folder_id) if folder_id else url_for("index"))


if __name__ == "__main__":
    init_db()
    STORAGE_DIR.mkdir(exist_ok=True)
    app.run(debug=True, port=5000)
