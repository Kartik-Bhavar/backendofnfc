from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from supabase import create_client, Client
from dotenv import load_dotenv
from bson import ObjectId
import os
import time
import uuid
import mimetypes
import logging

# Load environment variables
load_dotenv()

# Flask setup
app = Flask(__name__)
# CORS(app, resources={r"/*": {"origins": "http://localhost:5173"}}, supports_credentials=True)
CORS(app, resources={r"/*": {"origins": "http://192.168.1.6:5173"}}, supports_credentials=True)

# MongoDB setup
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI)
findmystuff_db = client["findmystuff"]
users_collection = findmystuff_db["users"]
items_collection = findmystuff_db["items"]

# Supabase setup
SUPABASE_URL = ("https://malqvwlecpvznvnatqoi.supabase.co")
SUPABASE_KEY = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1hbHF2d2xlY3B2em52bmF0cW9pIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDk0NjE2MzgsImV4cCI6MjA2NTAzNzYzOH0.JOiGzaTY-PJ754zJqxTwPuNJYiOq-X7yGhcJvfmt2A8")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# =========================
# ROUTES
# =========================

@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"message": "Server running"}), 200

# ---------- Registration ----------
@app.route("/register", methods=["POST"])
def register():
    data = request.json
    full_name = data.get("fullName")
    email = data.get("email")
    password = data.get("password")

    if not all([full_name, email, password]):
        return jsonify({"success": False, "message": "All fields are required"}), 400

    if users_collection.find_one({"email": email}):
        return jsonify({"success": False, "message": "Email already registered"}), 409

    hashed_password = generate_password_hash(password)
    users_collection.insert_one({
        "full_name": full_name,
        "email": email,
        "password": hashed_password
    })

    return jsonify({"success": True, "message": "User registered successfully"}), 201

# ---------- Login ----------
@app.route("/login", methods=["POST"])
def login():
    data = request.json
    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({"success": False, "message": "Email and password required"}), 400

    user = users_collection.find_one({"email": email})
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404

    if not check_password_hash(user["password"], password):
        return jsonify({"success": False, "message": "Incorrect password"}), 401

    return jsonify({
        "success": True,
        "message": "Login successful",
        "user": {
            "full_name": user["full_name"],
            "email": user["email"]
        }
    }), 200

# ---------- Upload Lost Item ----------
@app.route("/myitem", methods=["POST"])
def upload_image():
    try:
        file = request.files.get("image")
        item_name = request.form.get("name")
        description = request.form.get("description")

        # Validate input
        if not file or not item_name or not description:
            return jsonify({"success": False, "message": "All fields are required"}), 400

        # Optional: authenticate current user instead of using last one
        user = users_collection.find().sort([('_id', -1)]).limit(1)[0]
        user_id = str(user["_id"])

        # Validate file type
        filename = secure_filename(file.filename)
        allowed_extensions = {'png', 'jpg', 'jpeg'}
        if '.' not in filename or filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
            return jsonify({"success": False, "message": "Invalid file type"}), 400

        # Create a unique file name
        timestamp = int(time.time())
        unique_id = uuid.uuid4().hex[:8]
        file_ext = filename.rsplit('.', 1)[1].lower()
        unique_filename = f"{item_name}__{filename}__{unique_id}.{file_ext}"

        # Read file contents
        file_data = file.read()
        content_type = mimetypes.guess_type(unique_filename)[0] or 'application/octet-stream'

        # Upload to Supabase
        res = supabase.storage.from_("public-files").upload(
            path=unique_filename,
            file=file_data,
            file_options={"content-type": content_type}
        )

        if hasattr(res, "error") and res.error:
            logging.error(f"Supabase upload error: {res.error.message}")
            return jsonify({"success": False, "message": "Image upload failed"}), 500

        public_url_obj = supabase.storage.from_("public-files").get_public_url(unique_filename)
        public_url = public_url_obj.public_url if public_url_obj else None

        if not public_url:
            return jsonify({"success": False, "message": "Failed to get public image URL"}), 500

        # Store item in MongoDB
        item_data = {
            "name": item_name,
            "description": description,
            "image_url": public_url,
            "unique_id": unique_id,
            "uploaded_at": timestamp,
            "user_id": user_id
        }
        items_collection.insert_one(item_data)

        return jsonify({
            "success": True,
            "message": "Item uploaded successfully",
            "url": public_url,
            "unique_id": unique_id
        }), 200

    except Exception as e:
        logging.exception("Upload error occurred")
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500

# ---------- Dashboard ----------
@app.route("/dashboard", methods=["GET"])
def get_uploaded_items():
    try:
        items = list(items_collection.find({}, {"_id": 1, "name": 1, "description": 1, "image_url": 1, "unique_id": 1}))
        # Convert ObjectId to string for frontend
        for item in items:
            item["_id"] = str(item["_id"])
        return jsonify({"success": True, "items": items}), 200
    except Exception as e:
        logging.exception("Dashboard fetch error")
        return jsonify({"success": False, "message": f"Error fetching items: {str(e)}"}), 500

# ---------- QR Access by unique_id ----------
from flask import render_template

@app.route("/item/<string:unique_id>", methods=["GET"])
def get_item_by_unique_id(unique_id):
    try:
        item = items_collection.find_one({"unique_id": unique_id})
        if not item:
            return render_template("notfound.html", message="Item not found")

        return render_template("view_item.html", item=item)

    except Exception as e:
        return render_template("error.html", message=f"Error: {str(e)}")
    

# ================================
# MAIN
# ================================
# if __name__ == "__main__":
#     # app.run(debug=True)
#     app.run(host="0.0.0.0", port=5000, debug=True)
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)