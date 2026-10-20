import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import smtplib
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from functools import wraps
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import and_, delete, func, inspect, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from werkzeug.security import check_password_hash, generate_password_hash

PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
USERNAME_PATTERN = re.compile(r"^\w+$", re.UNICODE)
CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

ALLOWED_MEDIA_EXTENSIONS = {
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"),
    ".gif": ("image", "image/gif"),
    ".webp": ("image", "image/webp"),
    ".mp4": ("video", "video/mp4"),
    ".m4v": ("video", "video/mp4"),
    ".mov": ("video", "video/quicktime"),
    ".webm": ("video", "video/webm"),
    ".ogv": ("video", "video/ogg"),
}
ALLOWED_AVATAR_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
DISPLAY_NAME_MAX_LENGTH = 20
BIO_MAX_LENGTH = 80
MESSAGE_MAX_LENGTH = 1000
MESSAGE_RETENTION_DAYS = 7
MESSAGE_RETENTION = timedelta(days=MESSAGE_RETENTION_DAYS)

db = SQLAlchemy()


def load_env_file(path):
    """Load simple KEY=VALUE pairs from a local .env without extra dependencies."""
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file(Path(__file__).with_name(".env"))


class SmsServiceError(Exception):
    """A user-safe error raised when the SMS service cannot complete a request."""


class MediaUploadError(Exception):
    """A user-safe error raised when an uploaded file is rejected."""


def _percent_encode(value):
    return quote(str(value), safe="~")


def _sms_error_message(code):
    if code in (
        "isv.BUSINESS_LIMIT_CONTROL",
        "isv.MINUTE_LIMIT_CONTROL",
        "isv.HOUR_LIMIT_CONTROL",
        "isv.DAY_LIMIT_CONTROL",
    ):
        return "验证码发送过于频繁，请稍后再试。"
    if code in ("isv.MOBILE_NUMBER_ILLEGAL", "isv.INVALID_PARAMETERS"):
        return "手机号或短信参数不正确，请检查后重试。"
    if code in ("isv.AMOUNT_NOT_ENOUGH", "isv.OUT_OF_SERVICE"):
        return "短信服务暂时不可用，请联系网站管理员。"
    if code in (
        "InvalidAccessKeyId.NotFound",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "Forbidden.RAM",
        "Forbidden",
    ):
        return "短信服务配置有误，请联系网站管理员。"
    return "短信发送失败，请稍后重试。"


def _aliyun_rpc(app, action, business_params):
    """Call an Alibaba Cloud Dypnsapi RPC API using the RPC signature scheme."""
    access_key_id = app.config.get("ALIYUN_ACCESS_KEY_ID", "")
    access_key_secret = app.config.get("ALIYUN_ACCESS_KEY_SECRET", "")
    if not access_key_id or not access_key_secret:
        raise SmsServiceError("短信服务尚未配置，请联系网站管理员。")

    params = {
        "AccessKeyId": access_key_id,
        "Action": action,
        "Format": "JSON",
        "RegionId": app.config.get("ALIYUN_SMS_REGION_ID", "cn-hangzhou"),
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25",
    }
    params.update(business_params)

    canonical_query = "&".join(
        f"{_percent_encode(key)}={_percent_encode(params[key])}"
        for key in sorted(params)
    )
    string_to_sign = f"GET&{_percent_encode('/')}&{_percent_encode(canonical_query)}"
    signature = base64.b64encode(
        hmac.new(
            (access_key_secret + "&").encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("ascii")

    endpoint = app.config.get(
        "ALIYUN_SMS_ENDPOINT", "https://dypnsapi.aliyuncs.com/"
    ).rstrip("/")
    url = f"{endpoint}/?{canonical_query}&Signature={_percent_encode(signature)}"
    request_object = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ysan-community/1.0",
        },
    )

    try:
        with urlopen(request_object, timeout=10) as response:
            raw_body = response.read().decode("utf-8")
    except HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        try:
            error_payload = json.loads(raw_body)
        except (ValueError, TypeError):
            error_payload = {}
        error_code = error_payload.get("Code", "")
        app.logger.warning(
            "Aliyun SMS HTTP error action=%s code=%s message=%s request_id=%s",
            action,
            error_code,
            error_payload.get("Message", ""),
            error_payload.get("RequestId", ""),
        )
        raise SmsServiceError(_sms_error_message(error_code)) from exc
    except (URLError, TimeoutError, OSError) as exc:
        app.logger.warning("Aliyun SMS network error action=%s: %s", action, exc)
        raise SmsServiceError("短信服务暂时不可用，请稍后重试。") from exc

    try:
        payload = json.loads(raw_body)
    except (ValueError, TypeError) as exc:
        app.logger.warning("Aliyun SMS returned invalid JSON for action=%s", action)
        raise SmsServiceError("短信服务返回异常，请稍后重试。") from exc

    if payload.get("Code") != "OK":
        app.logger.warning(
            "Aliyun SMS API error action=%s code=%s message=%s request_id=%s",
            action,
            payload.get("Code", ""),
            payload.get("Message", ""),
            payload.get("RequestId", ""),
        )
        raise SmsServiceError(_sms_error_message(payload.get("Code", "")))

    return payload


def send_sms_login_code(app, phone):
    """Send a code using Alibaba Cloud's shared SMS authentication signature."""
    return _aliyun_rpc(
        app,
        "SendSmsVerifyCode",
        {
            "PhoneNumber": phone,
            "CountryCode": "86",
            "SignName": app.config["ALIYUN_SMS_SIGN_NAME"],
            "TemplateCode": app.config["ALIYUN_SMS_TEMPLATE_CODE"],
            "TemplateParam": json.dumps(
                {"code": "##code##", "min": "5"}, separators=(",", ":")
            ),
            "CodeLength": "6",
            "ValidTime": "300",
            "Interval": "60",
            "DuplicatePolicy": "1",
            "CodeType": "1",
        },
    )


def check_sms_login_code(app, phone, code):
    payload = _aliyun_rpc(
        app,
        "CheckSmsVerifyCode",
        {
            "PhoneNumber": phone,
            "CountryCode": "86",
            "VerifyCode": code,
        },
    )
    result = (payload.get("Model") or {}).get("VerifyResult", "")
    return str(result).upper() == "PASS"


def mail_notifications_enabled(app):
    return bool(
        app.config.get("MAIL_USERNAME")
        and app.config.get("MAIL_AUTH_CODE")
        and app.config.get("MAIL_TO")
    )


def _send_site_mail(app, subject, body):
    """Deliver a notification mail, never letting a mail failure break a request."""
    username = app.config["MAIL_USERNAME"]
    auth_code = app.config["MAIL_AUTH_CODE"]
    recipient = app.config["MAIL_TO"]
    host = app.config.get("MAIL_HOST", "smtp.qq.com")
    try:
        port = int(app.config.get("MAIL_PORT", 465))
    except (TypeError, ValueError):
        port = 465

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr(
        (app.config.get("MAIL_SENDER_NAME", "耶稣爱你.胜利"), username)
    )
    message["To"] = recipient
    message.set_content(body)

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
                smtp.login(username, auth_code)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(username, auth_code)
                smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        app.logger.warning("Could not send site notification mail: %s", exc)
        return

    app.logger.info("Sent site notification to %s", recipient)


def notify_site_owner(app, subject, body):
    """Queue an email to the site owner after a successful post or comment."""
    if not mail_notifications_enabled(app):
        return

    if app.config.get("TESTING") or not app.config.get("MAIL_ASYNC", True):
        _send_site_mail(app, subject, body)
        return

    threading.Thread(
        target=_send_site_mail,
        args=(app, subject, body),
        daemon=True,
        name="site-mail-notifier",
    ).start()


def local_time_text(value):
    local = value.replace(tzinfo=timezone.utc).astimezone(CHINA_TZ)
    return local.strftime("%Y-%m-%d %H:%M:%S")


def excerpt_text(value, limit=800):
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


IP_LOCATION_CACHE = {}
IP_LOCATION_CACHE_TTL = timedelta(days=7)


def lookup_ip_location(ip_address):
    """Resolve an IP's location with a small in-memory cache."""
    try:
        address = ipaddress.ip_address(ip_address)
    except ValueError:
        return "未知 IP"

    if address.is_private or address.is_loopback or address.is_link_local:
        return "本地或内网地址"

    normalized = str(address)
    cached = IP_LOCATION_CACHE.get(normalized)
    if cached and utcnow() - cached[1] < IP_LOCATION_CACHE_TTL:
        return cached[0]

    try:
        request_object = Request(
            f"https://ipwho.is/{quote(normalized, safe=':')}?lang=zh-CN",
            headers={
                "Accept": "application/json",
                "User-Agent": "ysan-community/1.0",
            },
        )
        with urlopen(request_object, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        current_app.logger.warning(
            "IP location lookup failed for %s: %s", normalized, exc
        )
        return "暂时无法获取归属地"

    if not isinstance(payload, dict) or not payload.get("success"):
        return "归属地未知"

    parts = [
        payload.get("country"),
        payload.get("region"),
        payload.get("city"),
    ]
    location = " ".join(str(part) for part in parts if part)
    connection = payload.get("connection") or {}
    isp = connection.get("isp") or connection.get("org")
    if isp:
        isp = str(isp)
        location = f"{location}（{isp}）" if location else isp

    location = location or "归属地未知"
    IP_LOCATION_CACHE[normalized] = (location, utcnow())
    return location


def utcnow():
    """Return a naive UTC timestamp for consistent storage in SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), nullable=False)
    username_key = db.Column(db.String(40), nullable=False, unique=True, index=True)
    nickname = db.Column(db.String(20), nullable=True)
    bio = db.Column(db.String(80), nullable=True)
    avatar_filename = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(20), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    posts = db.relationship("Post", back_populates="author", lazy="dynamic")
    comments = db.relationship("Comment", back_populates="author", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def display_name(self):
        return self.nickname or self.username

    @property
    def initial(self):
        name = self.display_name
        return name[:1].upper() if name else "?"


class Post(db.Model):
    __tablename__ = "posts"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    author = db.relationship("User", back_populates="posts")
    comments = db.relationship(
        "Comment",
        back_populates="post",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    media = db.relationship(
        "PostMedia",
        back_populates="post",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PostMedia.position",
    )


class Comment(db.Model):
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    post = db.relationship("Post", back_populates="comments")
    author = db.relationship("User", back_populates="comments")


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    recipient_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=utcnow, index=True
    )
    read_at = db.Column(db.DateTime, nullable=True)

    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])


class PostMedia(db.Model):
    __tablename__ = "post_media"

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)
    filename = db.Column(db.String(120), nullable=False, unique=True, index=True)
    position = db.Column(db.Integer, nullable=False, default=0)

    post = db.relationship("Post", back_populates="media")


class SmsSendLog(db.Model):
    __tablename__ = "sms_send_logs"

    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), nullable=False, index=True)
    ip_address = db.Column(db.String(64), nullable=True)
    verify_attempts = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


class Visitor(db.Model):
    __tablename__ = "visitors"

    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(64), nullable=False, unique=True, index=True)
    user_agent = db.Column(db.String(255), nullable=True)
    visit_count = db.Column(db.Integer, nullable=False, default=0)
    first_seen = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_seen = db.Column(
        db.DateTime, nullable=False, default=utcnow, index=True
    )


def purge_expired_messages():
    """Drop private messages older than the retention window."""
    cutoff = utcnow() - MESSAGE_RETENTION
    db.session.execute(delete(Message).where(Message.created_at < cutoff))
    db.session.commit()


def load_or_create_secret_key(instance_path):
    """Persist a local development key so sessions survive app restarts."""
    configured = os.environ.get("SECRET_KEY")
    if configured:
        return configured

    key_file = Path(instance_path) / ".secret_key"
    try:
        existing = key_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass

    key = secrets.token_hex(32)
    key_file.write_text(key, encoding="utf-8")
    return key


def safe_next_url(target):
    """Only allow redirects to a local path."""
    if not target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    if "\\" in target:
        return None
    return target


def _valid_media_signature(path, extension):
    try:
        with open(path, "rb") as file_handle:
            header = file_handle.read(32)
    except OSError:
        return False

    if extension in (".jpg", ".jpeg"):
        return header.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".gif":
        return header.startswith((b"GIF87a", b"GIF89a"))
    if extension == ".webp":
        return header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    if extension in (".mp4", ".m4v", ".mov"):
        return header[4:8] == b"ftyp"
    if extension == ".webm":
        return header.startswith(b"\x1a\x45\xdf\xa3")
    if extension == ".ogv":
        return header.startswith(b"OggS")
    return False


def save_media_uploads(files, app):
    files = [item for item in files if item and item.filename]
    if not files:
        return []

    max_files = int(app.config["MEDIA_MAX_FILES"])
    if len(files) > max_files:
        raise MediaUploadError(f"最多只能上传 {max_files} 个文件。")

    media_folder = Path(app.config["MEDIA_FOLDER"])
    try:
        media_folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        app.logger.exception("Could not create media folder")
        raise MediaUploadError("媒体存储目录不可用，请联系网站管理员。") from exc

    saved_paths = []
    saved_media = []
    total_size = 0

    try:
        for upload in files:
            extension = Path(upload.filename).suffix.lower()
            media_type = ALLOWED_MEDIA_EXTENSIONS.get(extension)
            if media_type is None:
                raise MediaUploadError(
                    "有文件格式不受支持。仅支持 JPG、PNG、GIF、WebP、"
                    "MP4、M4V、MOV、WebM 和 OGV。"
                )

            kind, _mimetype = media_type
            max_size = int(
                app.config[
                    "MEDIA_MAX_IMAGE_BYTES"
                    if kind == "image"
                    else "MEDIA_MAX_VIDEO_BYTES"
                ]
            )
            filename = f"{uuid.uuid4().hex}{extension}"
            destination = media_folder / filename

            try:
                upload.save(destination)
            except OSError as exc:
                app.logger.exception("Could not save uploaded media")
                raise MediaUploadError("文件保存失败，请稍后重试。") from exc
            saved_paths.append(destination)

            file_size = destination.stat().st_size
            if file_size == 0:
                raise MediaUploadError("不能上传空文件。")
            if file_size > max_size:
                category = "图片" if kind == "image" else "视频"
                raise MediaUploadError(
                    f"{category}大小不能超过 {max_size // (1024 * 1024)} MB。"
                )

            total_size += file_size
            if total_size > int(app.config["MEDIA_MAX_TOTAL_BYTES"]):
                limit_mb = int(app.config["MEDIA_MAX_TOTAL_BYTES"]) // (1024 * 1024)
                raise MediaUploadError(
                    f"图片和视频总大小不能超过 {limit_mb} MB。"
                )

            if not _valid_media_signature(destination, extension):
                raise MediaUploadError("文件内容与扩展名不匹配，请检查文件后再上传。")

            saved_media.append({"kind": kind, "filename": filename})
    except Exception:
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise

    return saved_media


MEDIA_UPLOAD_TOKEN_SALT = "media-upload"
MEDIA_UPLOAD_TOKEN_MAX_AGE = 24 * 60 * 60


def encode_media_upload_token(app, user_id, media):
    """Sign a pre-uploaded media reference so it can be attached later."""
    serializer = URLSafeTimedSerializer(
        app.config["SECRET_KEY"], salt=MEDIA_UPLOAD_TOKEN_SALT
    )
    return serializer.dumps(
        {
            "user_id": user_id,
            "filename": media["filename"],
            "kind": media["kind"],
        }
    )


def decode_media_upload_token(app, token, user_id):
    """Return the media described by a valid token owned by this user."""
    if not token:
        return None
    serializer = URLSafeTimedSerializer(
        app.config["SECRET_KEY"], salt=MEDIA_UPLOAD_TOKEN_SALT
    )
    try:
        data = serializer.loads(token, max_age=MEDIA_UPLOAD_TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None

    if not isinstance(data, dict) or data.get("user_id") != user_id:
        return None

    filename = data.get("filename")
    kind = data.get("kind")
    if not isinstance(filename, str) or kind not in ("image", "video"):
        return None
    if not re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]+", filename):
        return None
    media_type = ALLOWED_MEDIA_EXTENSIONS.get(Path(filename).suffix)
    if media_type is None or media_type[0] != kind:
        return None
    return {"kind": kind, "filename": filename}


def poster_filename_for(media_filename):
    return Path(media_filename).stem + ".jpg"


def duration_filename_for(media_filename):
    return Path(media_filename).stem + ".duration"


def delete_poster_file(app, media_filename):
    poster_folder = Path(app.config["POSTER_FOLDER"])
    for name in (
        poster_filename_for(media_filename),
        duration_filename_for(media_filename),
    ):
        try:
            (poster_folder / name).unlink(missing_ok=True)
        except OSError:
            app.logger.warning(
                "Could not delete %s for %s", name, media_filename, exc_info=True
            )


def _read_mp4_box_header(handle, offset, file_size):
    handle.seek(offset)
    header = handle.read(8)
    if len(header) < 8:
        return None
    size = int.from_bytes(header[:4], "big")
    box_type = header[4:8]
    header_size = 8
    if size == 1:
        large = handle.read(8)
        if len(large) < 8:
            return None
        size = int.from_bytes(large, "big")
        header_size = 16
    elif size == 0:
        size = file_size - offset
    if size < header_size:
        return None
    return box_type, offset + header_size, min(offset + size, file_size)


def mp4_duration_seconds(path):
    """Read the movie duration from an MP4/MOV mvhd box without decoding it."""
    try:
        file_size = path.stat().st_size
    except OSError:
        return None

    try:
        with path.open("rb") as handle:
            offset = 0
            moov = None
            while offset + 8 <= file_size:
                header = _read_mp4_box_header(handle, offset, file_size)
                if header is None:
                    break
                box_type, payload_start, box_end = header
                if box_type == b"moov":
                    moov = (payload_start, box_end)
                    break
                if box_end <= offset:
                    break
                offset = box_end

            if moov is None:
                return None

            offset = moov[0]
            while offset + 8 <= moov[1]:
                header = _read_mp4_box_header(handle, offset, moov[1])
                if header is None:
                    break
                box_type, payload_start, box_end = header
                if box_type == b"mvhd":
                    handle.seek(payload_start)
                    version = handle.read(1)
                    handle.seek(payload_start + 4)
                    if version == b"\x01":
                        handle.read(16)
                        timescale = int.from_bytes(handle.read(4), "big")
                        duration = int.from_bytes(handle.read(8), "big")
                    else:
                        handle.read(8)
                        timescale = int.from_bytes(handle.read(4), "big")
                        duration = int.from_bytes(handle.read(4), "big")
                    if timescale:
                        return duration / timescale
                    return None
                if box_end <= offset:
                    break
                offset = box_end
    except (OSError, ValueError):
        return None
    return None


def media_total_size(media_folder, items):
    total = 0
    for item in items:
        try:
            total += (media_folder / item["filename"]).stat().st_size
        except OSError:
            continue
    return total


def pending_media_from_tokens(app, tokens, user_id):
    """Collect existing pre-uploaded files referenced by signed tokens."""
    media_folder = Path(app.config["MEDIA_FOLDER"])
    items = []
    seen = set()
    for token in tokens:
        media = decode_media_upload_token(app, token, user_id)
        if media is None or media["filename"] in seen:
            continue
        path = media_folder / media["filename"]
        try:
            size = path.stat().st_size
        except OSError:
            continue
        seen.add(media["filename"])
        items.append({**media, "token": token, "size": size})
    return items


MEDIA_CHUNK_TOKEN_SALT = "media-chunk-upload"


def encode_chunk_upload_token(app, user_id, upload_id, kind, extension, size):
    serializer = URLSafeTimedSerializer(
        app.config["SECRET_KEY"], salt=MEDIA_CHUNK_TOKEN_SALT
    )
    return serializer.dumps(
        {
            "user_id": user_id,
            "upload_id": upload_id,
            "kind": kind,
            "extension": extension,
            "size": size,
        }
    )


def decode_chunk_upload_token(app, token, user_id):
    """Return a validated chunk-upload session owned by this user."""
    if not token:
        return None
    serializer = URLSafeTimedSerializer(
        app.config["SECRET_KEY"], salt=MEDIA_CHUNK_TOKEN_SALT
    )
    try:
        data = serializer.loads(token, max_age=MEDIA_UPLOAD_TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None

    if not isinstance(data, dict) or data.get("user_id") != user_id:
        return None

    upload_id = data.get("upload_id")
    kind = data.get("kind")
    extension = data.get("extension")
    size = data.get("size")
    if not isinstance(upload_id, str) or not re.fullmatch(
        r"[0-9a-f]{32}", upload_id
    ):
        return None
    if kind not in ("image", "video") or not isinstance(extension, str):
        return None
    media_type = ALLOWED_MEDIA_EXTENSIONS.get(extension)
    if media_type is None or media_type[0] != kind:
        return None
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return None
    max_size = int(
        app.config[
            "MEDIA_MAX_IMAGE_BYTES"
            if kind == "image"
            else "MEDIA_MAX_VIDEO_BYTES"
        ]
    )
    if size > max_size:
        return None

    chunk_size = int(app.config["MEDIA_CHUNK_SIZE"])
    return {
        "upload_id": upload_id,
        "kind": kind,
        "extension": extension,
        "size": size,
        "chunk_size": chunk_size,
        "chunk_count": (size + chunk_size - 1) // chunk_size,
    }


def cleanup_stale_chunk_uploads(folder, max_age_seconds=24 * 60 * 60):
    """Remove unfinished chunk directories that have been idle for a day."""
    try:
        entries = list(folder.iterdir())
    except OSError:
        return
    cutoff = time.time() - max_age_seconds
    for entry in entries:
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def save_avatar_upload(upload, app):
    """Validate and store a profile avatar, returning its generated filename."""
    if upload is None or not upload.filename:
        raise MediaUploadError("请选择要上传的头像图片。")

    extension = Path(upload.filename).suffix.lower()
    if extension not in ALLOWED_AVATAR_EXTENSIONS:
        raise MediaUploadError("头像仅支持 JPG、PNG、GIF 和 WebP 格式。")

    avatar_folder = Path(app.config["AVATAR_FOLDER"])
    try:
        avatar_folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        app.logger.exception("Could not create avatar folder")
        raise MediaUploadError("头像存储目录不可用，请联系网站管理员。") from exc

    filename = f"{uuid.uuid4().hex}{extension}"
    destination = avatar_folder / filename
    try:
        upload.save(destination)
        file_size = destination.stat().st_size
        if file_size == 0:
            raise MediaUploadError("不能上传空文件。")
        max_size = int(app.config["AVATAR_MAX_BYTES"])
        if file_size > max_size:
            raise MediaUploadError(
                f"头像大小不能超过 {max_size // (1024 * 1024)} MB。"
            )
        if not _valid_media_signature(destination, extension):
            raise MediaUploadError("文件内容与扩展名不匹配，请检查文件后再上传。")
    except MediaUploadError:
        destination.unlink(missing_ok=True)
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        app.logger.exception("Could not save avatar")
        raise MediaUploadError("头像保存失败，请稍后重试。") from exc

    return filename


def delete_avatar_file(app, filename):
    """Remove a stored avatar, ignoring already-missing files."""
    if not filename:
        return
    try:
        (Path(app.config["AVATAR_FOLDER"]) / Path(filename).name).unlink(
            missing_ok=True
        )
    except OSError:
        app.logger.warning("Could not delete avatar file %s", filename)


def display_name_taken(name, exclude_user_id=None):
    """Check nicknames and usernames together so display names stay unique."""
    key = name.lower()
    statement = db.select(User.id).where(
        db.or_(User.username_key == key, func.lower(User.nickname) == key)
    )
    if exclude_user_id is not None:
        statement = statement.where(User.id != exclude_user_id)
    return db.session.scalar(statement) is not None


def generate_local_phone():
    """Reserve a unique internal value for accounts without a phone number."""
    for _ in range(20):
        phone = f"local-{uuid.uuid4().hex[:14]}"
        if db.session.scalar(db.select(User.id).where(User.phone == phone)) is None:
            return phone
    return f"local-{uuid.uuid4().hex}"[:20]


def create_phone_user(phone):
    """Create a verified phone account with a friendly generated username."""
    for _ in range(20):
        username = f"新朋友{secrets.randbelow(10000):04d}"
        username_key = username.lower()
        if display_name_taken(username):
            continue

        user = User(username=username, username_key=username_key, phone=phone)
        user.set_password(secrets.token_urlsafe(32))
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            existing = db.session.scalar(db.select(User).where(User.phone == phone))
            if existing is not None:
                return existing
            continue
        return user

    return None


def ensure_admin_user(username, password):
    """Return the local user account backing the configured administrator."""
    username_key = username.lower()
    user = db.session.scalar(
        db.select(User).where(User.username_key == username_key)
    )

    if user is None:
        phone = f"admin:{username}"[:20]
        if db.session.scalar(db.select(User.id).where(User.phone == phone)):
            phone = f"admin-{uuid.uuid4().hex[:12]}"
        user = User(username=username, username_key=username_key, phone=phone)
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return db.session.scalar(
                db.select(User).where(User.username_key == username_key)
            )
        except Exception:
            db.session.rollback()
            return None
        return user

    user.set_password(password)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    return user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("请先登录，再参与社区讨论。", "info")
            target = request.full_path if request.method == "GET" else request.path
            return redirect(url_for("login", next=safe_next_url(target)))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_authenticated"):
            flash("请先登录管理后台。", "info")
            return redirect(url_for("admin"))
        return view(*args, **kwargs)

    return wrapped


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    app.config.from_mapping(
        SECRET_KEY=load_or_create_secret_key(app.instance_path),
        SQLALCHEMY_DATABASE_URI=(
            "sqlite:///" + (Path(app.instance_path) / "community.db").as_posix()
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        MAX_CONTENT_LENGTH=360 * 1024 * 1024,
        MEDIA_FOLDER=str(Path(app.instance_path) / "media"),
        AVATAR_FOLDER=str(Path(app.instance_path) / "avatars"),
        AVATAR_MAX_BYTES=5 * 1024 * 1024,
        MEDIA_MAX_FILES=6,
        MEDIA_MAX_IMAGE_BYTES=10 * 1024 * 1024,
        MEDIA_MAX_VIDEO_BYTES=300 * 1024 * 1024,
        MEDIA_MAX_TOTAL_BYTES=320 * 1024 * 1024,
        MEDIA_CHUNK_SIZE=40 * 1024 * 1024,
        MEDIA_CHUNK_THRESHOLD=20 * 1024 * 1024,
        MEDIA_CHUNK_FOLDER=str(Path(app.instance_path) / "media_chunks"),
        POSTER_FOLDER=str(Path(app.instance_path) / "posters"),
        POSTER_MAX_BYTES=2 * 1024 * 1024,
        ALIYUN_ACCESS_KEY_ID=os.environ.get("ALIYUN_ACCESS_KEY_ID", ""),
        ALIYUN_ACCESS_KEY_SECRET=os.environ.get("ALIYUN_ACCESS_KEY_SECRET", ""),
        ALIYUN_SMS_SIGN_NAME=os.environ.get(
            "ALIYUN_SMS_SIGN_NAME", "速通互联验证码"
        ),
        ALIYUN_SMS_TEMPLATE_CODE=os.environ.get("ALIYUN_SMS_TEMPLATE_CODE", "100001"),
        ALIYUN_SMS_REGION_ID=os.environ.get("ALIYUN_SMS_REGION_ID", "cn-hangzhou"),
        ALIYUN_SMS_ENDPOINT=os.environ.get(
            "ALIYUN_SMS_ENDPOINT", "https://dypnsapi.aliyuncs.com/"
        ),
        MAIL_HOST=os.environ.get("MAIL_HOST", "smtp.qq.com"),
        MAIL_PORT=os.environ.get("MAIL_PORT", "465"),
        MAIL_USERNAME=os.environ.get("MAIL_USERNAME", ""),
        MAIL_AUTH_CODE=(
            os.environ.get("MAIL_AUTH_CODE", "")
            or os.environ.get("MAIL_PASSWORD", "")
        ),
        MAIL_TO=(
            os.environ.get("MAIL_TO", "")
            or os.environ.get("MAIL_USERNAME", "")
        ),
        MAIL_SENDER_NAME=os.environ.get(
            "MAIL_SENDER_NAME", "耶稣爱你.胜利"
        ),
        MAIL_ASYNC=True,
        ADMIN_USERNAME=(
            os.environ.get("ADMIN_USERNAME", "")
            or os.environ.get("admin_name", "")
        ),
        ADMIN_PASSWORD=(
            os.environ.get("ADMIN_PASSWORD", "")
            or os.environ.get("admin_password", "")
        ),
    )

    if test_config:
        app.config.update(test_config)

    Path(app.config["MEDIA_FOLDER"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["AVATAR_FOLDER"]).mkdir(parents=True, exist_ok=True)
    db.init_app(app)

    @app.before_request
    def load_user_and_protect_forms():
        user_id = session.get("user_id")
        g.user = db.session.get(User, user_id) if user_id else None
        if user_id and g.user is None:
            session.clear()

        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)

        if request.method == "POST":
            supplied = request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not supplied or not expected or not hmac.compare_digest(supplied, expected):
                abort(400, description="表单已过期，请刷新页面后重试。")

    @app.before_request
    def record_visitor():
        if request.method != "GET" or request.endpoint is None:
            return
        endpoint = request.endpoint
        if endpoint in (
            "static",
            "media_file",
            "avatar_file",
            "pending_media_file",
            "poster_file",
        ):
            return
        if endpoint.startswith("admin"):
            return

        ip_address = request.remote_addr
        if not ip_address:
            return

        now = utcnow()
        visitor = db.session.scalar(
            db.select(Visitor).where(Visitor.ip_address == ip_address)
        )
        if visitor is None:
            visitor = Visitor(
                ip_address=ip_address,
                user_agent=(request.user_agent.string or "")[:255] or None,
                visit_count=1,
                first_seen=now,
                last_seen=now,
            )
            db.session.add(visitor)
        else:
            visitor.visit_count += 1
            visitor.last_seen = now
            user_agent = (request.user_agent.string or "")[:255]
            if user_agent:
                visitor.user_agent = user_agent

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Could not record visitor")

    @app.before_request
    def purge_expired_messages_periodically():
        now_ts = time.time()
        if now_ts - app.extensions.get("message_purge_at", 0) < 3600:
            return
        app.extensions["message_purge_at"] = now_ts
        try:
            purge_expired_messages()
        except Exception:
            db.session.rollback()
            app.logger.exception("Could not purge expired messages")

    @app.context_processor
    def inject_template_globals():
        unread_message_count = 0
        if getattr(g, "user", None) is not None:
            unread_message_count = (
                db.session.scalar(
                    db.select(func.count(Message.id)).where(
                        Message.recipient_id == g.user.id,
                        Message.read_at.is_(None),
                        Message.created_at >= utcnow() - MESSAGE_RETENTION,
                    )
                )
                or 0
            )
        return {
            "csrf_token": session.get("csrf_token", ""),
            "unread_message_count": unread_message_count,
            "message_max_length": MESSAGE_MAX_LENGTH,
            "message_retention_days": MESSAGE_RETENTION_DAYS,
            "media_max_files": app.config["MEDIA_MAX_FILES"],
            "media_max_image_bytes": app.config["MEDIA_MAX_IMAGE_BYTES"],
            "media_max_video_bytes": app.config["MEDIA_MAX_VIDEO_BYTES"],
            "media_max_total_bytes": app.config["MEDIA_MAX_TOTAL_BYTES"],
            "media_chunk_threshold": app.config["MEDIA_CHUNK_THRESHOLD"],
            "media_max_image_mb": app.config["MEDIA_MAX_IMAGE_BYTES"] // (1024 * 1024),
            "media_max_video_mb": app.config["MEDIA_MAX_VIDEO_BYTES"] // (1024 * 1024),
            "avatar_max_bytes": app.config["AVATAR_MAX_BYTES"],
            "avatar_max_mb": app.config["AVATAR_MAX_BYTES"] // (1024 * 1024),
            "display_name_max_length": DISPLAY_NAME_MAX_LENGTH,
            "bio_max_length": BIO_MAX_LENGTH,
        }

    @app.template_global()
    def media_poster_url(media_filename):
        poster_name = poster_filename_for(media_filename)
        if (Path(app.config["POSTER_FOLDER"]) / poster_name).is_file():
            return url_for("poster_file", filename=poster_name)
        return None

    @app.template_global()
    def media_duration_label(media_filename):
        poster_folder = Path(app.config["POSTER_FOLDER"])
        sidecar = poster_folder / duration_filename_for(media_filename)
        seconds = None
        try:
            seconds = float(sidecar.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            extension = Path(media_filename).suffix.lower()
            if extension in (".mp4", ".m4v", ".mov"):
                media_path = Path(app.config["MEDIA_FOLDER"]) / media_filename
                seconds = mp4_duration_seconds(media_path)
                if seconds:
                    try:
                        poster_folder.mkdir(parents=True, exist_ok=True)
                        sidecar.write_text(f"{seconds:.3f}", encoding="utf-8")
                    except OSError:
                        pass

        if not seconds or seconds <= 0:
            return ""
        total = int(round(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    @app.template_filter("timeago")
    def timeago(value):
        if not value:
            return ""
        delta = utcnow() - value
        seconds = max(0, int(delta.total_seconds()))
        if seconds < 60:
            return "刚刚"
        if seconds < 3600:
            return f"{seconds // 60} 分钟前"
        if seconds < 86400:
            return f"{seconds // 3600} 小时前"
        if seconds < 86400 * 7:
            return f"{seconds // 86400} 天前"

        local = value.replace(tzinfo=timezone.utc).astimezone(CHINA_TZ)
        if local.year == datetime.now(CHINA_TZ).year:
            return local.strftime("%m月%d日")
        return local.strftime("%Y年%m月%d日")

    @app.template_filter("localtime")
    def localtime(value):
        if not value:
            return ""
        local = value.replace(tzinfo=timezone.utc).astimezone(CHINA_TZ)
        return local.strftime("%Y-%m-%d %H:%M")

    @app.get("/")
    def index():
        page = request.args.get("page", 1, type=int) or 1
        page = max(page, 1)
        statement = (
            db.select(Post)
            .options(selectinload(Post.author))
            .order_by(Post.created_at.desc(), Post.id.desc())
        )
        pagination = db.paginate(statement, page=page, per_page=6, error_out=False)
        post_ids = [post.id for post in pagination.items]
        counts = {}
        if post_ids:
            rows = db.session.execute(
                db.select(Comment.post_id, func.count(Comment.id))
                .where(Comment.post_id.in_(post_ids))
                .group_by(Comment.post_id)
            ).all()
            counts = {post_id: count for post_id, count in rows}

        return render_template(
            "index.html",
            pagination=pagination,
            counts=counts,
        )

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user is not None:
            return redirect(url_for("index"))

        form = {"username": ""}
        errors = {}
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            form = {"username": username}

            if not 2 <= len(username) <= 20:
                errors["username"] = "用户名需为 2–20 个字符。"
            elif not USERNAME_PATTERN.fullmatch(username):
                errors["username"] = "用户名只能包含中文、英文、数字和下划线。"
            elif not 6 <= len(password) <= 128:
                errors["password"] = "密码长度需为 6–128 位。"
            elif password != confirm:
                errors["confirm_password"] = "两次输入的密码不一致。"
            elif display_name_taken(username):
                errors["username"] = "这个用户名已经被使用了。"

            if not errors:
                user = User(
                    username=username,
                    username_key=username.lower(),
                    phone=generate_local_phone(),
                )
                user.set_password(password)
                db.session.add(user)
                try:
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    flash("用户名已存在，请换一个试试。", "error")
                else:
                    session.clear()
                    session["user_id"] = user.id
                    session.permanent = True
                    flash(f"注册成功，欢迎加入耶稣爱你.胜利，{user.username}。", "success")
                    return redirect(url_for("index"))

        return render_template("auth/register.html", form=form, errors=errors)

    @app.post("/login/sms-code")
    def send_login_sms_code():
        phone = request.form.get("phone", "").strip()
        if not PHONE_PATTERN.fullmatch(phone):
            return (
                jsonify(ok=False, message="请输入有效的 11 位中国大陆手机号。"),
                400,
            )

        now = utcnow()
        db.session.execute(
            delete(SmsSendLog).where(SmsSendLog.created_at < now - timedelta(days=1))
        )
        db.session.commit()

        last_sent = db.session.scalar(
            db.select(SmsSendLog.created_at)
            .where(SmsSendLog.phone == phone)
            .order_by(SmsSendLog.created_at.desc())
            .limit(1)
        )
        if last_sent is not None:
            elapsed = int((now - last_sent).total_seconds())
            if elapsed < 60:
                retry_after = max(1, 60 - elapsed)
                return (
                    jsonify(
                        ok=False,
                        message=f"请等待 {retry_after} 秒后再获取验证码。",
                        retry_after=retry_after,
                    ),
                    429,
                )

        hour_start = now - timedelta(hours=1)
        phone_send_count = (
            db.session.scalar(
                db.select(func.count(SmsSendLog.id)).where(
                    SmsSendLog.phone == phone,
                    SmsSendLog.created_at >= hour_start,
                )
            )
            or 0
        )
        if phone_send_count >= 5:
            return (
                jsonify(
                    ok=False,
                    message="该手机号 1 小时内获取验证码次数过多，请稍后再试。",
                    retry_after=3600,
                ),
                429,
            )

        ip_address = request.remote_addr or ""
        if ip_address:
            ip_send_count = (
                db.session.scalar(
                    db.select(func.count(SmsSendLog.id)).where(
                        SmsSendLog.ip_address == ip_address,
                        SmsSendLog.created_at >= hour_start,
                    )
                )
                or 0
            )
            if ip_send_count >= 20:
                return (
                    jsonify(
                        ok=False,
                        message="获取验证码过于频繁，请稍后再试。",
                        retry_after=3600,
                    ),
                    429,
                )

        try:
            send_sms_login_code(app, phone)
        except SmsServiceError as exc:
            return jsonify(ok=False, message=str(exc)), 503

        db.session.add(SmsSendLog(phone=phone, ip_address=ip_address or None))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to save SMS send log")

        return jsonify(
            ok=True,
            message="验证码已发送，请在 5 分钟内输入。",
            retry_after=60,
            expires_in=300,
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user is not None:
            return redirect(url_for("index"))

        active_method = "phone"
        form = {"phone": "", "username": ""}
        next_url = safe_next_url(request.args.get("next"))

        if request.method == "POST":
            active_method = (
                "username" if request.form.get("method") == "username" else "phone"
            )
            next_url = safe_next_url(request.form.get("next"))

            if active_method == "phone":
                phone = request.form.get("phone", "").strip()
                code = request.form.get("code", "").strip()
                form["phone"] = phone
                user = (
                    db.session.scalar(db.select(User).where(User.phone == phone))
                    if PHONE_PATTERN.fullmatch(phone)
                    else None
                )

                verified = False
                sms_error = None
                if re.fullmatch(r"\d{6}", code):
                    now = utcnow()
                    latest_send = db.session.scalar(
                        db.select(SmsSendLog)
                        .where(
                            SmsSendLog.phone == phone,
                            SmsSendLog.created_at >= now - timedelta(minutes=15),
                        )
                        .order_by(SmsSendLog.created_at.desc())
                        .limit(1)
                    )
                    if latest_send is None:
                        sms_error = "验证码错误或已过期，请重新获取。"
                    elif latest_send.verify_attempts >= 5:
                        sms_error = "验证码尝试次数过多，请重新获取验证码。"
                    else:
                        latest_send.verify_attempts += 1
                        db.session.commit()
                        try:
                            verified = check_sms_login_code(app, phone, code)
                        except SmsServiceError as exc:
                            sms_error = str(exc)

                if verified:
                    is_new_user = user is None
                    if is_new_user:
                        user = create_phone_user(phone)
                    if user is not None:
                        session.clear()
                        session["user_id"] = user.id
                        session.permanent = True
                        if is_new_user:
                            flash(
                                f"验证成功，已自动创建账号并登录。你的用户名是 {user.username}。",
                                "success",
                            )
                        else:
                            flash(f"欢迎回来，{user.display_name}。", "success")
                        return redirect(next_url or url_for("index"))
                    sms_error = "账号创建失败，请稍后重试。"

                flash(
                    sms_error or "手机号或验证码不正确，请重新获取后再试。",
                    "error",
                )
            else:
                username = request.form.get("username", "").strip()
                password = request.form.get("password", "")
                form["username"] = username

                admin_username = app.config.get("ADMIN_USERNAME", "")
                admin_password = app.config.get("ADMIN_PASSWORD", "")
                if (
                    admin_username
                    and admin_password
                    and username
                    and password
                    and hmac.compare_digest(
                        username.lower().encode("utf-8"),
                        admin_username.lower().encode("utf-8"),
                    )
                    and hmac.compare_digest(
                        password.encode("utf-8"),
                        admin_password.encode("utf-8"),
                    )
                ):
                    admin_user = ensure_admin_user(admin_username, admin_password)
                    if admin_user is not None:
                        session.clear()
                        session["user_id"] = admin_user.id
                        session["admin_authenticated"] = True
                        session.permanent = True
                        flash(
                            "管理员登录成功。你已拥有普通用户权限，"
                            "可点击“进入后台”管理站点。",
                            "success",
                        )
                        return redirect(next_url or url_for("index"))

                user = (
                    db.session.scalar(
                        db.select(User).where(User.username_key == username.lower())
                    )
                    if username
                    else None
                )

                if user is not None and password and user.check_password(password):
                    session.clear()
                    session["user_id"] = user.id
                    session.permanent = True
                    flash(f"欢迎回来，{user.display_name}。", "success")
                    return redirect(next_url or url_for("index"))

                flash("用户名或密码不正确，请重试。", "error")

        return render_template(
            "auth/login.html",
            form=form,
            active_method=active_method,
            next_url=next_url or "",
        )

    @app.post("/logout")
    def logout():
        session.clear()
        flash("你已安全退出，愿平安与你同在。", "info")
        return redirect(url_for("index"))

    @app.route("/admin", methods=["GET", "POST"])
    def admin():
        admin_username = app.config.get("ADMIN_USERNAME", "")
        admin_password = app.config.get("ADMIN_PASSWORD", "")
        configured = bool(admin_username and admin_password)

        if request.method == "POST":
            if not configured:
                flash(
                    "管理员账号尚未配置，请在 .env 中设置 "
                    "admin_name 和 admin_password。",
                    "error",
                )
                return (
                    render_template(
                        "admin/login.html",
                        admin_username="",
                        configured=False,
                    ),
                    503,
                )

            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            username_ok = hmac.compare_digest(
                username.lower().encode("utf-8"),
                admin_username.lower().encode("utf-8"),
            )
            password_ok = hmac.compare_digest(
                password.encode("utf-8"), admin_password.encode("utf-8")
            )
            if username_ok and password_ok:
                admin_user = ensure_admin_user(admin_username, admin_password)
                if admin_user is not None:
                    session["user_id"] = admin_user.id
                session["admin_authenticated"] = True
                session.permanent = False
                flash("管理员登录成功。", "success")
                return redirect(url_for("admin"))

            flash("管理员用户名或密码不正确。", "error")
            return (
                render_template(
                    "admin/login.html",
                    admin_username=username,
                    configured=True,
                ),
                401,
            )

        if not session.get("admin_authenticated"):
            return render_template(
                "admin/login.html",
                admin_username="",
                configured=configured,
            )

        since_24h = utcnow() - timedelta(hours=24)
        stats = {
            "users": db.session.scalar(db.select(func.count(User.id))) or 0,
            "posts": db.session.scalar(db.select(func.count(Post.id))) or 0,
            "comments": db.session.scalar(db.select(func.count(Comment.id))) or 0,
            "visitors_24h": (
                db.session.scalar(
                    db.select(func.count(Visitor.id)).where(
                        Visitor.last_seen >= since_24h
                    )
                )
                or 0
            ),
            "visitors_total": (
                db.session.scalar(db.select(func.count(Visitor.id))) or 0
            ),
        }
        recent_posts = db.session.scalars(
            db.select(Post)
            .options(selectinload(Post.author))
            .order_by(Post.created_at.desc(), Post.id.desc())
            .limit(8)
        ).all()
        recent_comments = db.session.scalars(
            db.select(Comment)
            .options(
                selectinload(Comment.author),
                selectinload(Comment.post),
            )
            .order_by(Comment.created_at.desc(), Comment.id.desc())
            .limit(8)
        ).all()

        return render_template(
            "admin/dashboard.html",
            stats=stats,
            recent_posts=recent_posts,
            recent_comments=recent_comments,
            admin_username=admin_username,
        )

    @app.post("/admin/logout")
    def admin_logout():
        session.pop("admin_authenticated", None)
        flash("已退出管理后台。", "info")
        return redirect(url_for("admin"))

    @app.get("/admin/visitors")
    @admin_required
    def admin_visitors():
        page = request.args.get("page", 1, type=int) or 1
        page = max(page, 1)
        pagination = db.paginate(
            db.select(Visitor).order_by(
                Visitor.last_seen.desc(), Visitor.id.desc()
            ),
            page=page,
            per_page=30,
            error_out=False,
        )
        return render_template(
            "admin/visitors.html",
            pagination=pagination,
        )

    @app.get("/admin/visitors/<int:visitor_id>")
    @admin_required
    def admin_visitor_detail(visitor_id):
        visitor = db.session.get(Visitor, visitor_id)
        if visitor is None:
            abort(404)
        return render_template(
            "admin/visitor_detail.html",
            visitor=visitor,
            location=lookup_ip_location(visitor.ip_address),
        )

    @app.get("/users/<int:user_id>")
    def user_profile(user_id):
        user = db.session.get(User, user_id)
        if user is None:
            abort(404)

        page = request.args.get("page", 1, type=int) or 1
        page = max(page, 1)
        statement = (
            db.select(Post)
            .where(Post.user_id == user.id)
            .options(selectinload(Post.author))
            .order_by(Post.created_at.desc(), Post.id.desc())
        )
        pagination = db.paginate(statement, page=page, per_page=6, error_out=False)
        post_ids = [post.id for post in pagination.items]
        counts = {}
        if post_ids:
            rows = db.session.execute(
                db.select(Comment.post_id, func.count(Comment.id))
                .where(Comment.post_id.in_(post_ids))
                .group_by(Comment.post_id)
            ).all()
            counts = {post_id: count for post_id, count in rows}

        return render_template(
            "user_profile.html",
            profile_user=user,
            pagination=pagination,
            counts=counts,
            post_count=user.posts.count(),
            is_self=g.user is not None and g.user.id == user.id,
        )

    @app.get("/messages")
    @login_required
    def message_inbox():
        purge_expired_messages()
        cutoff = utcnow() - MESSAGE_RETENTION
        messages = db.session.scalars(
            db.select(Message)
            .where(
                or_(
                    Message.sender_id == g.user.id,
                    Message.recipient_id == g.user.id,
                ),
                Message.created_at >= cutoff,
            )
            .options(
                selectinload(Message.sender),
                selectinload(Message.recipient),
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
        ).all()

        conversations = {}
        for message in messages:
            other = (
                message.recipient
                if message.sender_id == g.user.id
                else message.sender
            )
            if other is None:
                continue
            conversation = conversations.get(other.id)
            if conversation is None:
                conversation = {"user": other, "latest": message, "unread": 0}
                conversations[other.id] = conversation
            if message.recipient_id == g.user.id and message.read_at is None:
                conversation["unread"] += 1

        return render_template(
            "messages.html",
            conversations=list(conversations.values()),
            retention_days=MESSAGE_RETENTION_DAYS,
        )

    @app.route("/messages/<int:user_id>", methods=["GET", "POST"])
    @login_required
    def message_thread(user_id):
        purge_expired_messages()
        other_user = db.session.get(User, user_id)
        if other_user is None:
            abort(404)
        if other_user.id == g.user.id:
            flash("不能给自己发送私信。", "info")
            return redirect(url_for("message_inbox"))

        error = None
        draft = ""
        if request.method == "POST":
            draft = request.form.get("body", "").strip()
            if not 1 <= len(draft) <= MESSAGE_MAX_LENGTH:
                error = f"私信内容需为 1–{MESSAGE_MAX_LENGTH} 个字符。"
            else:
                message = Message(
                    sender=g.user, recipient=other_user, body=draft
                )
                db.session.add(message)
                db.session.commit()
                return redirect(
                    url_for(
                        "message_thread",
                        user_id=other_user.id,
                        _anchor=f"message-{message.id}",
                    )
                )

        cutoff = utcnow() - MESSAGE_RETENTION
        conversation = db.session.scalars(
            db.select(Message)
            .where(
                Message.created_at >= cutoff,
                or_(
                    and_(
                        Message.sender_id == g.user.id,
                        Message.recipient_id == other_user.id,
                    ),
                    and_(
                        Message.sender_id == other_user.id,
                        Message.recipient_id == g.user.id,
                    ),
                ),
            )
            .options(selectinload(Message.sender))
            .order_by(Message.created_at.asc(), Message.id.asc())
        ).all()

        unread = [
            message
            for message in conversation
            if message.recipient_id == g.user.id and message.read_at is None
        ]
        if unread:
            now = utcnow()
            for message in unread:
                message.read_at = now
            db.session.commit()

        return (
            render_template(
                "message_thread.html",
                other_user=other_user,
                messages=conversation,
                error=error,
                draft=draft,
                retention_days=MESSAGE_RETENTION_DAYS,
            ),
            400 if error else 200,
        )

    @app.get("/profile")
    @login_required
    def profile():
        return render_template(
            "profile.html",
            nickname_form=g.user.nickname or "",
            bio_form=g.user.bio or "",
            errors={},
        )

    @app.post("/profile/nickname")
    @login_required
    def update_nickname():
        nickname = request.form.get("nickname", "").strip()
        errors = {}

        if nickname:
            if not 2 <= len(nickname) <= DISPLAY_NAME_MAX_LENGTH:
                errors["nickname"] = (
                    f"昵称需为 2–{DISPLAY_NAME_MAX_LENGTH} 个字符。"
                )
            elif not USERNAME_PATTERN.fullmatch(nickname):
                errors["nickname"] = "昵称只能包含中文、英文、数字和下划线。"
            elif display_name_taken(nickname, exclude_user_id=g.user.id):
                errors["nickname"] = "这个昵称已经被使用了。"

        if errors:
            return (
                render_template(
                    "profile.html",
                    nickname_form=nickname,
                    bio_form=g.user.bio or "",
                    errors=errors,
                ),
                400,
            )

        g.user.nickname = nickname or None
        db.session.commit()
        if nickname:
            flash("昵称已更新。", "success")
        else:
            flash("已恢复使用用户名作为显示名称。", "success")
        return redirect(url_for("profile"))

    @app.post("/profile/bio")
    @login_required
    def update_bio():
        bio = request.form.get("bio", "").strip()

        if len(bio) > BIO_MAX_LENGTH:
            errors = {
                "bio": f"一句话介绍不能超过 {BIO_MAX_LENGTH} 个字符。"
            }
            return (
                render_template(
                    "profile.html",
                    nickname_form=g.user.nickname or "",
                    bio_form=bio,
                    errors=errors,
                ),
                400,
            )

        g.user.bio = bio or None
        db.session.commit()
        if bio:
            flash("一句话介绍已更新。", "success")
        else:
            flash("已清空一句话介绍。", "success")
        return redirect(url_for("profile"))

    @app.post("/profile/avatar")
    @login_required
    def update_avatar():
        try:
            filename = save_avatar_upload(request.files.get("avatar"), app)
        except MediaUploadError as exc:
            flash(str(exc), "error")
            return redirect(url_for("profile"))
        except Exception:
            app.logger.exception("Unexpected error while saving avatar")
            flash("头像上传失败，请稍后重试。", "error")
            return redirect(url_for("profile"))

        old_filename = g.user.avatar_filename
        g.user.avatar_filename = filename
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            delete_avatar_file(app, filename)
            app.logger.exception("Could not save avatar filename")
            flash("头像保存失败，请稍后重试。", "error")
            return redirect(url_for("profile"))

        if old_filename and old_filename != filename:
            delete_avatar_file(app, old_filename)
        flash("头像已更新。", "success")
        return redirect(url_for("profile"))

    @app.post("/profile/avatar/remove")
    @login_required
    def remove_avatar():
        old_filename = g.user.avatar_filename
        if old_filename:
            g.user.avatar_filename = None
            db.session.commit()
            delete_avatar_file(app, old_filename)
            flash("已恢复默认头像。", "success")
        return redirect(url_for("profile"))

    @app.get("/avatars/<filename>")
    def avatar_file(filename):
        exists = db.session.scalar(
            db.select(User.id).where(User.avatar_filename == filename)
        )
        if exists is None:
            abort(404)

        response = send_from_directory(
            app.config["AVATAR_FOLDER"],
            filename,
            max_age=30 * 24 * 60 * 60,
            conditional=True,
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/media/<filename>")
    def media_file(filename):
        exists = db.session.scalar(
            db.select(PostMedia.id).where(PostMedia.filename == filename)
        )
        if exists is None:
            abort(404)

        response = send_from_directory(
            app.config["MEDIA_FOLDER"],
            filename,
            max_age=30 * 24 * 60 * 60,
            conditional=True,
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.post("/media/upload")
    @login_required
    def upload_media():
        upload = request.files.get("media")
        if upload is None or not upload.filename:
            return jsonify(ok=False, message="请选择要上传的文件。"), 400

        try:
            saved = save_media_uploads([upload], app)
        except MediaUploadError as exc:
            return jsonify(ok=False, message=str(exc)), 400
        except Exception:
            app.logger.exception("Unexpected error while uploading media")
            return (
                jsonify(ok=False, message="文件上传失败，请稍后重试。"),
                500,
            )

        item = saved[0]
        token = encode_media_upload_token(app, g.user.id, item)
        return jsonify(
            ok=True,
            token=token,
            kind=item["kind"],
            url=url_for("pending_media_file", token=token),
        )

    @app.post("/media/poster")
    @login_required
    def upload_media_poster():
        media = decode_media_upload_token(
            app, request.form.get("token", ""), g.user.id
        )
        if media is None or media["kind"] != "video":
            return jsonify(ok=False, message="视频信息无效。"), 400

        poster = request.files.get("poster")
        has_poster = poster is not None and bool(poster.filename)
        duration = None
        try:
            value = float(request.form.get("duration", ""))
            if 0 < value <= 7 * 24 * 60 * 60:
                duration = value
        except (TypeError, ValueError):
            duration = None

        if not has_poster and duration is None:
            return jsonify(ok=False, message="缺少视频封面或时长。"), 400

        poster_folder = Path(app.config["POSTER_FOLDER"])
        try:
            poster_folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            app.logger.exception("Could not create poster folder")
            return (
                jsonify(ok=False, message="封面保存失败，请稍后重试。"),
                500,
            )

        if has_poster:
            poster_name = poster_filename_for(media["filename"])
            destination = poster_folder / poster_name
            temporary = (
                poster_folder
                / f"{Path(poster_name).stem}.{uuid.uuid4().hex}.uploading.jpg"
            )
            try:
                poster.save(temporary)
                size = temporary.stat().st_size
                if size <= 0 or size > int(app.config["POSTER_MAX_BYTES"]):
                    temporary.unlink(missing_ok=True)
                    return jsonify(ok=False, message="视频封面大小无效。"), 400
                if not _valid_media_signature(temporary, ".jpg"):
                    temporary.unlink(missing_ok=True)
                    return jsonify(ok=False, message="视频封面格式无效。"), 400
                temporary.replace(destination)
            except OSError:
                temporary.unlink(missing_ok=True)
                app.logger.exception("Could not save media poster")
                return (
                    jsonify(ok=False, message="封面保存失败，请稍后重试。"),
                    500,
                )

        if duration is not None:
            sidecar = poster_folder / duration_filename_for(media["filename"])
            temporary = (
                poster_folder
                / f"{Path(sidecar).stem}.{uuid.uuid4().hex}.uploading.duration"
            )
            try:
                temporary.write_text(f"{duration:.3f}", encoding="utf-8")
                temporary.replace(sidecar)
            except OSError:
                temporary.unlink(missing_ok=True)
                app.logger.exception("Could not save media duration")
                return (
                    jsonify(ok=False, message="视频时长保存失败，请稍后重试。"),
                    500,
                )

        return jsonify(ok=True)

    @app.get("/posters/<filename>")
    def poster_file(filename):
        if not re.fullmatch(r"[0-9a-f]{32}\.jpg", filename):
            abort(404)

        stem = Path(filename).stem
        exists = db.session.scalar(
            db.select(PostMedia.id).where(
                PostMedia.kind == "video",
                PostMedia.filename.like(f"{stem}.%"),
            )
        )
        if exists is None:
            abort(404)

        response = send_from_directory(
            app.config["POSTER_FOLDER"],
            filename,
            max_age=30 * 24 * 60 * 60,
            conditional=True,
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.post("/media/upload/init")
    @login_required
    def init_media_chunk_upload():
        filename = request.form.get("filename", "").strip()
        try:
            size = int(request.form.get("size", "0"))
        except (TypeError, ValueError):
            return jsonify(ok=False, message="文件信息无效。"), 400

        extension = Path(filename).suffix.lower()
        media_type = ALLOWED_MEDIA_EXTENSIONS.get(extension)
        if media_type is None:
            return jsonify(ok=False, message="文件格式不受支持。"), 400

        kind = media_type[0]
        max_size = int(
            app.config[
                "MEDIA_MAX_IMAGE_BYTES"
                if kind == "image"
                else "MEDIA_MAX_VIDEO_BYTES"
            ]
        )
        if size <= 0:
            return jsonify(ok=False, message="不能上传空文件。"), 400
        if size > max_size:
            category = "图片" if kind == "image" else "视频"
            return (
                jsonify(
                    ok=False,
                    message=(
                        f"{category}大小不能超过 "
                        f"{max_size // (1024 * 1024)} MB。"
                    ),
                ),
                400,
            )

        chunk_size = int(app.config["MEDIA_CHUNK_SIZE"])
        chunk_count = (size + chunk_size - 1) // chunk_size
        chunk_root = Path(app.config["MEDIA_CHUNK_FOLDER"])
        try:
            chunk_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            app.logger.exception("Could not create chunk upload folder")
            return (
                jsonify(ok=False, message="上传目录不可用，请联系管理员。"),
                500,
            )

        cleanup_stale_chunk_uploads(chunk_root)
        upload_id = uuid.uuid4().hex
        try:
            (chunk_root / upload_id).mkdir(parents=False, exist_ok=False)
        except OSError:
            app.logger.exception("Could not create chunk upload session")
            return (
                jsonify(ok=False, message="上传初始化失败，请稍后重试。"),
                500,
            )

        token = encode_chunk_upload_token(
            app, g.user.id, upload_id, kind, extension, size
        )
        return jsonify(
            ok=True,
            upload_token=token,
            chunk_size=chunk_size,
            chunk_count=chunk_count,
        )

    @app.post("/media/upload/chunk")
    @login_required
    def upload_media_chunk():
        upload_session = decode_chunk_upload_token(
            app, request.form.get("token", ""), g.user.id
        )
        if upload_session is None:
            return (
                jsonify(ok=False, message="上传会话已过期，请重新选择文件。"),
                400,
            )

        try:
            index = int(request.form.get("index", "-1"))
        except (TypeError, ValueError):
            index = -1
        if index < 0 or index >= upload_session["chunk_count"]:
            return jsonify(ok=False, message="分片序号无效。"), 400

        upload = request.files.get("chunk")
        if upload is None or not upload.filename:
            return jsonify(ok=False, message="缺少分片内容。"), 400

        chunk_dir = (
            Path(app.config["MEDIA_CHUNK_FOLDER"]) / upload_session["upload_id"]
        )
        if not chunk_dir.is_dir():
            return (
                jsonify(ok=False, message="上传会话已过期，请重新选择文件。"),
                400,
            )

        start = index * upload_session["chunk_size"]
        expected_size = min(
            upload_session["chunk_size"], upload_session["size"] - start
        )
        destination = chunk_dir / f"{index:06d}.part"
        temporary = chunk_dir / f"{index:06d}.tmp"
        try:
            upload.save(temporary)
            actual_size = temporary.stat().st_size
            if actual_size != expected_size:
                temporary.unlink(missing_ok=True)
                return jsonify(ok=False, message="分片大小不正确，请重试。"), 400
            temporary.replace(destination)
        except OSError:
            temporary.unlink(missing_ok=True)
            app.logger.exception("Could not save media chunk")
            return (
                jsonify(ok=False, message="分片保存失败，请稍后重试。"),
                500,
            )

        return jsonify(ok=True, index=index)

    @app.post("/media/upload/complete")
    @login_required
    def complete_media_chunk_upload():
        upload_session = decode_chunk_upload_token(
            app, request.form.get("token", ""), g.user.id
        )
        if upload_session is None:
            return (
                jsonify(ok=False, message="上传会话已过期，请重新选择文件。"),
                400,
            )

        chunk_dir = (
            Path(app.config["MEDIA_CHUNK_FOLDER"]) / upload_session["upload_id"]
        )
        if not chunk_dir.is_dir():
            return (
                jsonify(ok=False, message="上传会话已过期，请重新选择文件。"),
                400,
            )

        chunks = []
        total_size = 0
        for index in range(upload_session["chunk_count"]):
            part = chunk_dir / f"{index:06d}.part"
            start = index * upload_session["chunk_size"]
            expected_size = min(
                upload_session["chunk_size"], upload_session["size"] - start
            )
            try:
                actual_size = part.stat().st_size
            except OSError:
                return (
                    jsonify(ok=False, message="分片尚未上传完整，请重试。"),
                    400,
                )
            if actual_size != expected_size:
                return (
                    jsonify(ok=False, message="分片不完整，请重新上传。"),
                    400,
                )
            chunks.append(part)
            total_size += actual_size

        if total_size != upload_session["size"]:
            return (
                jsonify(ok=False, message="文件大小校验失败，请重新上传。"),
                400,
            )

        media_folder = Path(app.config["MEDIA_FOLDER"])
        try:
            media_folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            app.logger.exception("Could not create media folder")
            return (
                jsonify(ok=False, message="媒体存储目录不可用，请联系管理员。"),
                500,
            )

        filename = f"{uuid.uuid4().hex}{upload_session['extension']}"
        destination = media_folder / filename
        try:
            with destination.open("wb") as output:
                for part in chunks:
                    with part.open("rb") as source:
                        shutil.copyfileobj(source, output, 1024 * 1024)
            if not _valid_media_signature(
                destination, upload_session["extension"]
            ):
                destination.unlink(missing_ok=True)
                shutil.rmtree(chunk_dir, ignore_errors=True)
                return (
                    jsonify(
                        ok=False,
                        message="文件内容与扩展名不匹配，请检查文件后再上传。",
                    ),
                    400,
                )
        except OSError:
            destination.unlink(missing_ok=True)
            app.logger.exception("Could not assemble uploaded media")
            return (
                jsonify(ok=False, message="文件合并失败，请稍后重试。"),
                500,
            )

        shutil.rmtree(chunk_dir, ignore_errors=True)
        token = encode_media_upload_token(
            app,
            g.user.id,
            {"kind": upload_session["kind"], "filename": filename},
        )
        return jsonify(
            ok=True,
            token=token,
            kind=upload_session["kind"],
            url=url_for("pending_media_file", token=token),
        )

    @app.post("/media/upload/cancel")
    @login_required
    def cancel_media_chunk_upload():
        upload_session = decode_chunk_upload_token(
            app, request.form.get("token", ""), g.user.id
        )
        if upload_session is not None:
            shutil.rmtree(
                Path(app.config["MEDIA_CHUNK_FOLDER"])
                / upload_session["upload_id"],
                ignore_errors=True,
            )
        return jsonify(ok=True)

    @app.post("/media/discard")
    @login_required
    def discard_media():
        token = request.form.get("token", "")
        media = decode_media_upload_token(app, token, g.user.id)
        if media is not None:
            referenced = db.session.scalar(
                db.select(PostMedia.id).where(
                    PostMedia.filename == media["filename"]
                )
            )
            if referenced is None:
                try:
                    (Path(app.config["MEDIA_FOLDER"]) / media["filename"]).unlink(
                        missing_ok=True
                    )
                except OSError:
                    app.logger.warning(
                        "Could not discard media file %s",
                        media["filename"],
                        exc_info=True,
                    )
                delete_poster_file(app, media["filename"])
        return jsonify(ok=True)

    @app.get("/media/pending/<token>")
    @login_required
    def pending_media_file(token):
        media = decode_media_upload_token(app, token, g.user.id)
        if media is None:
            abort(404)

        response = send_from_directory(
            app.config["MEDIA_FOLDER"],
            media["filename"],
            conditional=True,
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/posts/<int:post_id>")
    def post_detail(post_id):
        post = db.session.scalar(
            db.select(Post)
            .options(selectinload(Post.author))
            .where(Post.id == post_id)
        )
        if post is None:
            abort(404)

        comments = db.session.scalars(
            db.select(Comment)
            .options(selectinload(Comment.author))
            .where(Comment.post_id == post.id)
            .order_by(Comment.created_at.asc(), Comment.id.asc())
        ).all()

        return render_template(
            "post_detail.html",
            post=post,
            comments=comments,
            comment_count=len(comments),
        )

    @app.route("/posts/new", methods=["GET", "POST"])
    @login_required
    def new_post():
        form = {"title": "", "content": ""}
        errors = {}
        uploaded_media = []

        if request.method == "POST":
            title = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            form = {"title": title, "content": content}

            if not 2 <= len(title) <= 120:
                errors["title"] = "标题需为 2–120 个字符。"
            if len(content) > 10000:
                errors["content"] = "正文不能超过 10000 个字符。"

            uploaded_media = pending_media_from_tokens(
                app, request.form.getlist("uploaded_media"), g.user.id
            )
            direct_uploads = [
                item
                for item in request.files.getlist("media")
                if item and item.filename
            ]
            max_files = int(app.config["MEDIA_MAX_FILES"])
            if len(uploaded_media) + len(direct_uploads) > max_files:
                errors["media"] = f"每条帖子最多只能有 {max_files} 个附件。"

            saved_media = []
            if not errors:
                try:
                    saved_media = save_media_uploads(direct_uploads, app)
                except MediaUploadError as exc:
                    errors["media"] = str(exc)
                except Exception:
                    app.logger.exception("Unexpected error while saving media")
                    errors["media"] = "文件上传失败，请稍后重试。"

            if not errors:
                media_folder = Path(app.config["MEDIA_FOLDER"])
                total_bytes = media_total_size(
                    media_folder, uploaded_media
                ) + media_total_size(media_folder, saved_media)
                if total_bytes > int(app.config["MEDIA_MAX_TOTAL_BYTES"]):
                    for item in saved_media:
                        (media_folder / item["filename"]).unlink(
                            missing_ok=True
                        )
                    saved_media = []
                    limit_mb = int(app.config["MEDIA_MAX_TOTAL_BYTES"]) // (
                        1024 * 1024
                    )
                    errors["media"] = (
                        f"图片和视频总大小不能超过 {limit_mb} MB。"
                    )

            media_items = [
                {"kind": item["kind"], "filename": item["filename"]}
                for item in uploaded_media
            ] + saved_media

            if not errors:
                post = Post(title=title, content=content, author=g.user)
                for index, item in enumerate(media_items):
                    post.media.append(
                        PostMedia(
                            kind=item["kind"],
                            filename=item["filename"],
                            position=index,
                        )
                    )
                db.session.add(post)
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    for item in media_items:
                        (Path(app.config["MEDIA_FOLDER"]) / item["filename"]).unlink(
                            missing_ok=True
                        )
                    uploaded_media = []
                    app.logger.exception("Could not save post with media")
                    errors["media"] = "发布失败，请稍后重试。"
                else:
                    notify_site_owner(
                        app,
                        f"[耶稣爱你.胜利] 新帖子：{post.title}",
                        "\n".join(
                            [
                                "站点：耶稣爱你.胜利",
                                "事件：新帖子",
                                f"作者：{g.user.display_name}（用户名：{g.user.username}）",
                                f"时间：{local_time_text(post.created_at)}",
                                f"标题：{post.title}",
                                f"链接：{url_for('post_detail', post_id=post.id, _external=True)}",
                                "",
                                "正文：",
                                excerpt_text(post.content) or "（无正文）",
                            ]
                        ),
                    )
                    flash("你的讨论已发布，感谢你的分享。", "success")
                    return redirect(url_for("post_detail", post_id=post.id))

        return render_template(
            "post_new.html",
            form=form,
            errors=errors,
            uploaded_media=uploaded_media,
        )

    @app.route("/posts/<int:post_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_post(post_id):
        post = db.session.get(Post, post_id)
        if post is None:
            abort(404)
        if post.user_id != g.user.id:
            abort(403)

        form = {"title": post.title, "content": post.content}
        errors = {}
        uploaded_media = []

        if request.method == "POST":
            title = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            form = {"title": title, "content": content}

            if not 2 <= len(title) <= 120:
                errors["title"] = "标题需为 2–120 个字符。"
            if len(content) > 10000:
                errors["content"] = "正文不能超过 10000 个字符。"

            uploaded_media = pending_media_from_tokens(
                app, request.form.getlist("uploaded_media"), g.user.id
            )
            uploads = [
                upload
                for upload in request.files.getlist("media")
                if upload and upload.filename
            ]
            max_files = int(app.config["MEDIA_MAX_FILES"])
            if len(post.media) + len(uploaded_media) + len(uploads) > max_files:
                errors["media"] = f"每条帖子最多只能有 {max_files} 个附件。"

            saved_media = []
            if not errors:
                try:
                    saved_media = save_media_uploads(uploads, app)
                except MediaUploadError as exc:
                    errors["media"] = str(exc)
                except Exception:
                    app.logger.exception(
                        "Unexpected error while saving post media"
                    )
                    errors["media"] = "文件上传失败，请稍后重试。"

            if not errors:
                media_folder = Path(app.config["MEDIA_FOLDER"])
                total_bytes = media_total_size(
                    media_folder, uploaded_media
                ) + media_total_size(media_folder, saved_media)
                if total_bytes > int(app.config["MEDIA_MAX_TOTAL_BYTES"]):
                    for item in saved_media:
                        (media_folder / item["filename"]).unlink(
                            missing_ok=True
                        )
                    saved_media = []
                    limit_mb = int(app.config["MEDIA_MAX_TOTAL_BYTES"]) // (
                        1024 * 1024
                    )
                    errors["media"] = (
                        f"图片和视频总大小不能超过 {limit_mb} MB。"
                    )

            media_items = [
                {"kind": item["kind"], "filename": item["filename"]}
                for item in uploaded_media
            ] + saved_media

            if not errors:
                post.title = title
                post.content = content
                next_position = max(
                    (item.position for item in post.media), default=-1
                ) + 1
                for offset, item in enumerate(media_items):
                    post.media.append(
                        PostMedia(
                            kind=item["kind"],
                            filename=item["filename"],
                            position=next_position + offset,
                        )
                    )
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    for item in media_items:
                        (Path(app.config["MEDIA_FOLDER"]) / item["filename"]).unlink(
                            missing_ok=True
                        )
                    uploaded_media = []
                    app.logger.exception("Could not update post with media")
                    errors["media"] = "保存失败，请稍后重试。"
                else:
                    flash("帖子已更新。", "success")
                    return redirect(url_for("post_detail", post_id=post.id))

        return (
            render_template(
                "post_edit.html",
                post=post,
                form=form,
                errors=errors,
                uploaded_media=uploaded_media,
            ),
            400 if errors else 200,
        )

    @app.post("/posts/<int:post_id>/delete")
    def delete_post(post_id):
        post = db.session.get(Post, post_id)
        if post is None:
            abort(404)

        is_admin = bool(session.get("admin_authenticated"))
        if not is_admin and (g.user is None or post.user_id != g.user.id):
            if g.user is None:
                flash("请先登录，再删除帖子。", "info")
                return redirect(url_for("login"))
            abort(403)

        media_filenames = [item.filename for item in post.media]
        db.session.delete(post)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Could not delete post %s", post_id)
            flash("删除失败，请稍后重试。", "error")
        else:
            for filename in media_filenames:
                try:
                    (Path(app.config["MEDIA_FOLDER"]) / filename).unlink(
                        missing_ok=True
                    )
                except OSError:
                    app.logger.warning(
                        "Could not delete media file %s", filename, exc_info=True
                    )
                delete_poster_file(app, filename)
            flash("帖子已删除。", "success")

        target = safe_next_url(request.form.get("next", ""))
        return redirect(target or url_for("index"))

    @app.post("/posts/<int:post_id>/comments")
    @login_required
    def add_comment(post_id):
        post = db.session.get(Post, post_id)
        if post is None:
            abort(404)

        content = request.form.get("content", "").strip()
        if not 1 <= len(content) <= 1000:
            flash("回应内容需为 1–1000 个字符。", "error")
            return redirect(url_for("post_detail", post_id=post.id))

        comment = Comment(content=content, post=post, author=g.user)
        db.session.add(comment)
        db.session.commit()
        notify_site_owner(
            app,
            f"[耶稣爱你.胜利] 新回应：{post.title}",
            "\n".join(
                [
                    "站点：耶稣爱你.胜利",
                    "事件：新回应",
                    f"作者：{g.user.display_name}（用户名：{g.user.username}）",
                    f"时间：{local_time_text(comment.created_at)}",
                    f"帖子：{post.title}",
                    f"链接：{url_for('post_detail', post_id=post.id, _anchor=f'comment-{comment.id}', _external=True)}",
                    "",
                    "回应内容：",
                    excerpt_text(comment.content),
                ]
            ),
        )
        flash("回应已发布。", "success")
        return redirect(
            url_for("post_detail", post_id=post.id, _anchor=f"comment-{comment.id}")
        )

    @app.errorhandler(400)
    def bad_request(error):
        return (
            render_template(
                "error.html",
                code=400,
                title="请求无法完成",
                message=getattr(error, "description", "请刷新页面后重试。"),
            ),
            400,
        )

    @app.errorhandler(403)
    def forbidden(error):
        return (
            render_template(
                "error.html",
                code=403,
                title="无法进行此操作",
                message="你没有权限进行这个操作。",
            ),
            403,
        )

    @app.errorhandler(404)
    def not_found(error):
        return (
            render_template(
                "error.html",
                code=404,
                title="这里还是一片空白",
                message="你访问的页面不存在，或已经被移走了。",
            ),
            404,
        )

    @app.errorhandler(413)
    def too_large(error):
        return (
            render_template(
                "error.html",
                code=413,
                title="内容实在太长了",
                message="提交的内容超过了大小限制，请精简后再试。",
            ),
            413,
        )

    with app.app_context():
        db.create_all()
        inspector = inspect(db.engine)
        table_names = inspector.get_table_names()
        if "sms_send_logs" in table_names:
            columns = {
                column["name"]
                for column in inspector.get_columns("sms_send_logs")
            }
            if "verify_attempts" not in columns:
                db.session.execute(
                    text(
                        "ALTER TABLE sms_send_logs "
                        "ADD COLUMN verify_attempts INTEGER NOT NULL DEFAULT 0"
                    )
                )
                db.session.commit()
        if "users" in table_names:
            columns = {
                column["name"] for column in inspector.get_columns("users")
            }
            if "nickname" not in columns:
                db.session.execute(
                    text("ALTER TABLE users ADD COLUMN nickname VARCHAR(20)")
                )
            if "avatar_filename" not in columns:
                db.session.execute(
                    text(
                        "ALTER TABLE users "
                        "ADD COLUMN avatar_filename VARCHAR(120)"
                    )
                )
            if "bio" not in columns:
                db.session.execute(
                    text("ALTER TABLE users ADD COLUMN bio VARCHAR(80)")
                )
            db.session.commit()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5012")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
