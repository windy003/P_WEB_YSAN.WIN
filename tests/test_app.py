import re
import smtplib
import sys
import tempfile
import unittest
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import (  # noqa: E402
    Comment,
    Message,
    Post,
    PostMedia,
    User,
    Visitor,
    create_app,
    db,
    lookup_ip_location,
    mp4_duration_seconds,
    utcnow,
)


class CommunityTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.media_dir = Path(self.temp_dir.name) / "media"
        self.avatar_dir = Path(self.temp_dir.name) / "avatars"
        self.poster_dir = Path(self.temp_dir.name) / "posters"
        database_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret-key",
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
                "MEDIA_FOLDER": str(self.media_dir),
                "AVATAR_FOLDER": str(self.avatar_dir),
                "POSTER_FOLDER": str(self.poster_dir),
                "MEDIA_CHUNK_FOLDER": str(Path(self.temp_dir.name) / "chunks"),
                "MEDIA_CHUNK_SIZE": 32,
                "MEDIA_CHUNK_THRESHOLD": 1,
                "MAIL_USERNAME": "",
                "MAIL_AUTH_CODE": "",
                "MAIL_TO": "",
                "ADMIN_USERNAME": "",
                "ADMIN_PASSWORD": "",
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp_dir.cleanup()

    def csrf_token(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(
            r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)
        )
        self.assertIsNotNone(match, f"No CSRF token found on {path}")
        return match.group(1)

    def register(
        self,
        username="恩雨",
        phone="13800138000",
        password="grace123",
        follow_redirects=True,
    ):
        token = self.csrf_token("/register")
        return self.client.post(
            "/register",
            data={
                "csrf_token": token,
                "username": username,
                "password": password,
                "confirm_password": password,
            },
            follow_redirects=follow_redirects,
        )

    def logout(self):
        token = self.csrf_token("/")
        return self.client.post(
            "/logout", data={"csrf_token": token}, follow_redirects=True
        )

    def request_sms_code(self, phone="13800138000"):
        token = self.csrf_token("/login")
        with patch(
            "app._aliyun_rpc", return_value={"Code": "OK", "Model": {}}
        ) as rpc_mock:
            response = self.client.post(
                "/login/sms-code",
                data={"csrf_token": token, "phone": phone},
                headers={"Accept": "application/json"},
            )
        return response, rpc_mock

    def login_with_sms(self, phone="13800138000", verify_result="PASS"):
        token = self.csrf_token("/login")
        with patch(
            "app._aliyun_rpc",
            return_value={
                "Code": "OK",
                "Model": {"VerifyResult": verify_result},
            },
        ) as rpc_mock:
            response = self.client.post(
                "/login",
                data={
                    "csrf_token": token,
                    "method": "phone",
                    "phone": phone,
                    "code": "123456",
                    "username": "",
                    "password": "",
                },
                follow_redirects=True,
            )
        return response, rpc_mock

    def login_with_password(self, username, password="grace123"):
        token = self.csrf_token("/login")
        return self.client.post(
            "/login",
            data={
                "csrf_token": token,
                "method": "username",
                "phone": "",
                "code": "",
                "username": username,
                "password": password,
            },
            follow_redirects=True,
        )

    def test_home_page_and_banner(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("耶稣爱你.胜利", html)
        self.assertIn("本网站的域名的意思是", html)
        self.assertIn("/static/banner.png", html)
        self.assertIn("在主爱中相遇", html)
        self.assertIn("有事请联系站长:周秋良,微信:trust030703", html)
        self.assertNotIn("最新讨论", html)
        self.assertNotIn("今日经文", html)
        self.assertNotIn("社区公约", html)

        banner = self.client.get("/static/banner.png")
        self.assertEqual(banner.status_code, 200)
        self.assertEqual(banner.content_type, "image/png")
        self.assertGreater(len(banner.data), 1000)
        banner.close()

    def test_guest_must_login_to_start_discussion(self):
        response = self.client.get("/posts/new")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

        response = self.client.get("/posts/new", follow_redirects=True)
        self.assertIn("登录社区", response.get_data(as_text=True))

    def test_csrf_token_is_required(self):
        response = self.client.post(
            "/login",
            data={"method": "phone", "phone": "13800138000", "password": "grace123"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("表单已过期", response.get_data(as_text=True))

    def test_register_without_phone_and_login_with_password(self):
        response = self.register()
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("欢迎加入耶稣爱你.胜利", html)
        self.assertIn("恩雨", html)

        with self.app.app_context():
            user = db.session.scalar(
                db.select(User).where(User.username == "恩雨")
            )
            self.assertIsNotNone(user)
            self.assertTrue(user.phone.startswith("local-"))

        self.logout()

        response = self.login_with_password("恩雨")
        self.assertEqual(response.status_code, 200)
        self.assertIn("欢迎回来", response.get_data(as_text=True))
        self.assertIn("发帖", response.get_data(as_text=True))

    def test_sms_code_request_is_rate_limited(self):
        self.register()
        self.logout()

        response, rpc_mock = self.request_sms_code()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(rpc_mock.call_count, 1)

        response, rpc_mock = self.request_sms_code()
        self.assertEqual(response.status_code, 429)
        self.assertFalse(response.get_json()["ok"])
        self.assertEqual(rpc_mock.call_count, 0)

    def test_sms_login_auto_creates_account_for_new_phone(self):
        phone = "13500135000"
        response, rpc_mock = self.request_sms_code(phone)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(rpc_mock.call_count, 1)

        response, _ = self.login_with_sms(phone)
        self.assertEqual(response.status_code, 200)
        self.assertIn("自动创建账号并登录", response.get_data(as_text=True))

        with self.app.app_context():
            user = db.session.scalar(db.select(User).where(User.phone == phone))
            self.assertIsNotNone(user)
            self.assertTrue(user.username.startswith("新朋友"))
            self.assertFalse(user.check_password("not-the-generated-password"))

    def test_login_with_username_is_case_insensitive(self):
        self.register(username="Grace_7", phone="13900139000")
        self.logout()

        response = self.login_with_password("grace_7")
        self.assertEqual(response.status_code, 200)
        self.assertIn("欢迎回来", response.get_data(as_text=True))

    def test_wrong_sms_code_does_not_log_in(self):
        self.register()
        self.logout()
        send_response, _ = self.request_sms_code()
        self.assertEqual(send_response.status_code, 200)

        response, rpc_mock = self.login_with_sms(verify_result="UNKNOWN")
        self.assertIn("验证码不正确", response.get_data(as_text=True))
        self.assertEqual(rpc_mock.call_args.args[1], "CheckSmsVerifyCode")
        protected = self.client.get("/posts/new")
        self.assertEqual(protected.status_code, 302)

    def test_duplicate_username_is_rejected(self):
        self.register(username="Morning")
        self.logout()

        response = self.register(username="morning")
        self.assertIn("用户名已经被使用", response.get_data(as_text=True))

    def test_logged_in_user_can_create_post_and_comment(self):
        self.register()
        token = self.csrf_token("/posts/new")
        response = self.client.post(
            "/posts/new",
            data={
                "csrf_token": token,
                "title": "今天的感恩",
                "content": "感谢今天收到的每一份善意，也想听听大家的故事。",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("今天的感恩", html)
        self.assertIn("感谢今天收到的每一份善意", html)

        home = self.client.get("/")
        self.assertIn("今天的感恩", home.get_data(as_text=True))

        token = self.csrf_token("/posts/1")
        response = self.client.post(
            "/posts/1/comments",
            data={"csrf_token": token, "content": "愿平安与你同在，谢谢你的分享。"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("愿平安与你同在", html)
        self.assertIn("恩雨", html)

    def test_post_with_only_title_is_allowed(self):
        self.register()

        editor = self.client.get("/posts/new").get_data(as_text=True)
        self.assertEqual(editor.count("可选"), 2)

        response = self.create_post(title="只发一个标题", content="")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("只发一个标题", html)
        self.assertNotIn("正文需为", html)

        with self.app.app_context():
            post = db.session.scalar(db.select(Post))
            self.assertEqual(post.title, "只发一个标题")
            self.assertEqual(post.content, "")

        home = self.client.get("/")
        self.assertIn("只发一个标题", home.get_data(as_text=True))

    def test_author_can_edit_own_post(self):
        self.register()
        self.create_post(title="原标题", content="原来的正文内容。")

        detail = self.client.get("/posts/1")
        self.assertIn("编辑帖子", detail.get_data(as_text=True))

        form_page = self.client.get("/posts/1/edit")
        html = form_page.get_data(as_text=True)
        self.assertEqual(form_page.status_code, 200)
        self.assertIn('value="原标题"', html)
        self.assertIn("原来的正文内容。", html)

        token = self.csrf_token("/posts/1/edit")
        response = self.client.post(
            "/posts/1/edit",
            data={
                "csrf_token": token,
                "title": "修改后的标题",
                "content": "修改后的正文。",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("帖子已更新", html)
        self.assertIn("修改后的标题", html)
        self.assertIn("修改后的正文。", html)

        with self.app.app_context():
            post = db.session.get(Post, 1)
            self.assertEqual(post.title, "修改后的标题")
            self.assertEqual(post.content, "修改后的正文。")

        token = self.csrf_token("/posts/1/edit")
        invalid = self.client.post(
            "/posts/1/edit",
            data={"csrf_token": token, "title": "x", "content": ""},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("标题需为 2–120 个字符", invalid.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(db.session.get(Post, 1).title, "修改后的标题")

    def test_author_can_add_media_when_editing_post(self):
        self.register()
        self.create_post(title="带图的帖子", content="正文内容。")
        image_data = b"\xff\xd8\xff\xe0" + b"0" * 100

        token = self.csrf_token("/posts/1/edit")
        response = self.client.post(
            "/posts/1/edit",
            data={
                "csrf_token": token,
                "title": "带图的帖子",
                "content": "正文内容。",
                "media": [(BytesIO(image_data), "new.jpg")],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("帖子已更新", html)
        self.assertIn("/media/", html)

        with self.app.app_context():
            media = db.session.scalars(db.select(PostMedia)).all()
            self.assertEqual(len(media), 1)
            self.assertEqual(media[0].kind, "image")
            self.assertTrue((self.media_dir / media[0].filename).exists())

        self.app.config["MEDIA_MAX_FILES"] = 1
        token = self.csrf_token("/posts/1/edit")
        response = self.client.post(
            "/posts/1/edit",
            data={
                "csrf_token": token,
                "title": "带图的帖子",
                "content": "正文内容。",
                "media": [(BytesIO(image_data), "second.jpg")],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("最多只能有 1 个附件", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(
                len(db.session.scalars(db.select(PostMedia)).all()), 1
            )
        self.assertEqual(len(list(self.media_dir.glob("*"))), 1)

    def test_only_author_can_edit_post(self):
        self.register()
        self.create_post(title="作者的帖子", content="原文内容。")
        self.logout()

        guest = self.client.get("/posts/1/edit")
        self.assertEqual(guest.status_code, 302)
        self.assertIn("/login", guest.headers["Location"])

        self.register(username="其他人", phone="13900139000")
        detail = self.client.get("/posts/1").get_data(as_text=True)
        self.assertNotIn("编辑帖子", detail)
        self.assertEqual(self.client.get("/posts/1/edit").status_code, 403)

        token = self.csrf_token("/")
        response = self.client.post(
            "/posts/1/edit",
            data={
                "csrf_token": token,
                "title": "偷改的标题",
                "content": "偷改的正文。",
            },
        )
        self.assertEqual(response.status_code, 403)

        with self.app.app_context():
            post = db.session.get(Post, 1)
            self.assertEqual(post.title, "作者的帖子")
            self.assertEqual(post.content, "原文内容。")

    def test_logged_in_user_can_upload_images_and_video(self):
        self.register()
        token = self.csrf_token("/posts/new")
        image_data = b"\xff\xd8\xff\xe0" + b"0" * 100
        video_data = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100

        response = self.client.post(
            "/posts/new",
            data={
                "csrf_token": token,
                "title": "带图片和视频的分享",
                "content": "这是包含图片和视频的正文内容。",
                "media": [
                    (BytesIO(image_data), "photo.jpg"),
                    (BytesIO(video_data), "clip.mp4"),
                ],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("带图片和视频的分享", html)
        self.assertIn("<img", html)
        self.assertIn("<video", html)
        self.assertIn("/media/", html)

        with self.app.app_context():
            media = db.session.scalars(
                db.select(PostMedia).order_by(PostMedia.position)
            ).all()
            self.assertEqual([item.kind for item in media], ["image", "video"])
            for item in media:
                self.assertTrue((self.media_dir / item.filename).exists())

            image_filename = media[0].filename
            video_filename = media[1].filename

        image_response = self.client.get(f"/media/{image_filename}")
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response.content_type, "image/jpeg")
        self.assertEqual(image_response.headers["X-Content-Type-Options"], "nosniff")
        image_response.close()

        video_response = self.client.get(f"/media/{video_filename}")
        self.assertEqual(video_response.status_code, 200)
        self.assertEqual(video_response.content_type, "video/mp4")
        video_response.close()

        home = self.client.get("/")
        home_html = home.get_data(as_text=True)
        self.assertIn(f"/media/{image_filename}", home_html)
        self.assertNotIn(f"/media/{video_filename}", home_html)
        self.assertIn("home-post-media-item", home_html)
        self.assertIn("video-poster-play", home_html)
        self.assertNotIn("data-video-poster", home_html)

        missing_response = self.client.get("/media/not-a-real-file.jpg")
        self.assertEqual(missing_response.status_code, 404)

    def test_video_poster_is_saved_and_served_on_home(self):
        self.register()
        token = self.csrf_token("/posts/new")
        video_data = b"\x00\x00\x00\x18ftypmp42" + b"0" * 100

        upload = self.client.post(
            "/media/upload",
            data={
                "csrf_token": token,
                "media": (BytesIO(video_data), "clip.mp4"),
            },
            content_type="multipart/form-data",
        )
        payload = upload.get_json()
        self.assertTrue(payload["ok"])
        media_token = payload["token"]

        poster_data = b"\xff\xd8\xff\xe0" + b"0" * 100
        poster = self.client.post(
            "/media/poster",
            data={
                "csrf_token": token,
                "token": media_token,
                "poster": (BytesIO(poster_data), "poster.jpg"),
                "duration": "245",
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(poster.status_code, 200)
        self.assertTrue(poster.get_json()["ok"])

        create = self.client.post(
            "/posts/new",
            data={
                "csrf_token": token,
                "title": "带封面的视频",
                "content": "首页应该使用封面图，而不是加载视频。",
                "uploaded_media": media_token,
            },
            follow_redirects=True,
        )
        self.assertEqual(create.status_code, 200)
        self.assertIn('data-duration="04:05"', create.get_data(as_text=True))

        with self.app.app_context():
            media = db.session.scalar(
                db.select(PostMedia).where(PostMedia.kind == "video")
            )
            media_filename = media.filename
            post_id = media.post_id

        poster_name = Path(media_filename).stem + ".jpg"
        self.assertTrue((self.poster_dir / poster_name).is_file())

        home_html = self.client.get("/").get_data(as_text=True)
        self.assertIn(f"/posters/{poster_name}", home_html)
        self.assertNotIn(f"/media/{media_filename}", home_html)
        self.assertIn("04:05", home_html)

        poster_response = self.client.get(f"/posters/{poster_name}")
        self.assertEqual(poster_response.status_code, 200)
        self.assertEqual(poster_response.data, poster_data)
        poster_response.close()

        token = self.csrf_token("/")
        self.client.post(
            f"/posts/{post_id}/delete",
            data={"csrf_token": token},
            follow_redirects=True,
        )
        self.assertFalse((self.poster_dir / poster_name).exists())

    def test_mp4_duration_parser_reads_mvhd(self):
        mvhd_payload = (
            b"\x00\x00\x00\x00"
            + (0).to_bytes(4, "big")
            + (0).to_bytes(4, "big")
            + (1000).to_bytes(4, "big")
            + (245000).to_bytes(4, "big")
            + b"\x00" * 80
        )
        mvhd = (8 + len(mvhd_payload)).to_bytes(4, "big") + b"mvhd" + mvhd_payload
        moov = (8 + len(mvhd)).to_bytes(4, "big") + b"moov" + mvhd
        ftyp = (24).to_bytes(4, "big") + b"ftyp" + b"mp42" + b"\x00" * 12

        path = Path(self.temp_dir.name) / "sample.mp4"
        path.write_bytes(ftyp + moov)
        self.assertAlmostEqual(mp4_duration_seconds(path), 245.0, places=1)

    def test_media_size_limits_allow_300_mb_videos(self):
        self.assertEqual(
            self.app.config["MEDIA_MAX_VIDEO_BYTES"], 300 * 1024 * 1024
        )
        self.assertGreaterEqual(
            self.app.config["MEDIA_MAX_TOTAL_BYTES"],
            self.app.config["MEDIA_MAX_VIDEO_BYTES"],
        )
        self.assertGreater(
            self.app.config["MAX_CONTENT_LENGTH"],
            self.app.config["MEDIA_MAX_TOTAL_BYTES"],
        )

    def test_unsupported_media_type_is_rejected(self):
        self.register()
        token = self.csrf_token("/posts/new")
        response = self.client.post(
            "/posts/new",
            data={
                "csrf_token": token,
                "title": "不支持的文件",
                "content": "这个帖子不应该被创建，因为附件格式不受支持。",
                "media": [(BytesIO(b"<svg></svg>"), "drawing.svg")],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("文件格式不受支持", response.get_data(as_text=True))

        with self.app.app_context():
            self.assertEqual(len(db.session.scalars(db.select(PostMedia)).all()), 0)
        self.assertEqual(list(self.media_dir.glob("*")), [])

    def test_chunked_media_upload_is_merged_and_attached(self):
        self.register()
        image_data = b"\x89PNG\r\n\x1a\n" + b"0" * 120
        token = self.csrf_token("/posts/new")

        init = self.client.post(
            "/media/upload/init",
            data={
                "csrf_token": token,
                "filename": "large.png",
                "size": str(len(image_data)),
            },
        )
        self.assertEqual(init.status_code, 200)
        init_payload = init.get_json()
        self.assertTrue(init_payload["ok"])
        upload_token = init_payload["upload_token"]
        chunk_size = init_payload["chunk_size"]
        self.assertGreater(init_payload["chunk_count"], 1)

        for index in range(init_payload["chunk_count"]):
            start = index * chunk_size
            blob = image_data[start : start + chunk_size]
            chunk = self.client.post(
                "/media/upload/chunk",
                data={
                    "csrf_token": token,
                    "token": upload_token,
                    "index": str(index),
                    "chunk": (BytesIO(blob), f"chunk-{index}.part"),
                },
                content_type="multipart/form-data",
            )
            self.assertEqual(chunk.status_code, 200)
            self.assertTrue(chunk.get_json()["ok"])

        complete = self.client.post(
            "/media/upload/complete",
            data={"csrf_token": token, "token": upload_token},
        )
        self.assertEqual(complete.status_code, 200)
        complete_payload = complete.get_json()
        self.assertTrue(complete_payload["ok"])
        media_token = complete_payload["token"]

        preview = self.client.get(complete_payload["url"])
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.data, image_data)
        preview.close()

        self.assertEqual(
            list(Path(self.app.config["MEDIA_CHUNK_FOLDER"]).glob("*")), []
        )

        response = self.client.post(
            "/posts/new",
            data={
                "csrf_token": token,
                "title": "分片上传的图片",
                "content": "合并后的文件应与原文件一致。",
                "uploaded_media": media_token,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("分片上传的图片", response.get_data(as_text=True))

        with self.app.app_context():
            media = db.session.scalars(db.select(PostMedia)).all()
            self.assertEqual(len(media), 1)
            merged = (self.media_dir / media[0].filename).read_bytes()
            self.assertEqual(merged, image_data)

    def test_chunked_upload_can_be_cancelled(self):
        self.register()
        token = self.csrf_token("/posts/new")
        image_data = b"\x89PNG\r\n\x1a\n" + b"0" * 100

        init = self.client.post(
            "/media/upload/init",
            data={
                "csrf_token": token,
                "filename": "cancel.png",
                "size": str(len(image_data)),
            },
        )
        upload_token = init.get_json()["upload_token"]

        cancel = self.client.post(
            "/media/upload/cancel",
            data={"csrf_token": token, "token": upload_token},
        )
        self.assertEqual(cancel.status_code, 200)
        self.assertTrue(cancel.get_json()["ok"])
        self.assertEqual(
            list(Path(self.app.config["MEDIA_CHUNK_FOLDER"]).glob("*")), []
        )

    def test_post_owner_can_delete_post_and_all_media(self):
        self.register()
        token = self.csrf_token("/posts/new")
        image_data = b"\xff\xd8\xff\xe0" + b"0" * 100
        video_data = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100

        response = self.client.post(
            "/posts/new",
            data={
                "csrf_token": token,
                "title": "待删除的帖子",
                "content": "正文、图片和视频都应该被删除。",
                "media": [
                    (BytesIO(image_data), "photo.jpg"),
                    (BytesIO(video_data), "clip.mp4"),
                ],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            post = db.session.scalar(
                db.select(Post).where(Post.title == "待删除的帖子")
            )
            post_id = post.id
            filenames = [item.filename for item in post.media]
            self.assertEqual(len(filenames), 2)

        token = self.csrf_token(f"/posts/{post_id}")
        self.client.post(
            f"/posts/{post_id}/comments",
            data={"csrf_token": token, "content": "一条回应"},
            follow_redirects=True,
        )

        home_html = self.client.get("/").get_data(as_text=True)
        self.assertIn(f"/posts/{post_id}/delete", home_html)

        token = self.csrf_token("/")
        response = self.client.post(
            f"/posts/{post_id}/delete",
            data={"csrf_token": token, "next": "/"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("帖子已删除", response.get_data(as_text=True))

        with self.app.app_context():
            self.assertIsNone(db.session.get(Post, post_id))
            self.assertEqual(db.session.scalars(db.select(Comment)).all(), [])
            self.assertEqual(db.session.scalars(db.select(PostMedia)).all(), [])

        for filename in filenames:
            self.assertFalse((self.media_dir / filename).exists())

        detail = self.client.get(f"/posts/{post_id}")
        self.assertEqual(detail.status_code, 404)

    def test_non_owner_cannot_delete_post(self):
        self.register()
        token = self.csrf_token("/posts/new")
        self.client.post(
            "/posts/new",
            data={
                "csrf_token": token,
                "title": "别人的帖子",
                "content": "普通用户不能删除这篇帖子。",
            },
            follow_redirects=True,
        )
        with self.app.app_context():
            post_id = db.session.scalar(
                db.select(Post.id).where(Post.title == "别人的帖子")
            )

        self.logout()
        self.register(username="另一个人", password="grace456")

        home_html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn(f"/posts/{post_id}/delete", home_html)

        token = self.csrf_token("/")
        response = self.client.post(
            f"/posts/{post_id}/delete",
            data={"csrf_token": token},
        )
        self.assertEqual(response.status_code, 403)

        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Post, post_id))

    def test_admin_can_delete_any_post(self):
        self.register()
        token = self.csrf_token("/posts/new")
        self.client.post(
            "/posts/new",
            data={
                "csrf_token": token,
                "title": "管理员要删除的帖子",
                "content": "管理员可以删除任何帖子。",
            },
            follow_redirects=True,
        )
        with self.app.app_context():
            post_id = db.session.scalar(
                db.select(Post.id).where(Post.title == "管理员要删除的帖子")
            )

        self.logout()
        self.app.config["ADMIN_USERNAME"] = "admin"
        self.app.config["ADMIN_PASSWORD"] = "admin-pass"

        token = self.csrf_token("/admin")
        response = self.client.post(
            "/admin",
            data={
                "csrf_token": token,
                "username": "admin",
                "password": "admin-pass",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("管理员登录成功", response.get_data(as_text=True))

        home_html = self.client.get("/").get_data(as_text=True)
        self.assertIn(f"/posts/{post_id}/delete", home_html)

        token = self.csrf_token("/")
        response = self.client.post(
            f"/posts/{post_id}/delete",
            data={"csrf_token": token},
            follow_redirects=True,
        )
        self.assertIn("帖子已删除", response.get_data(as_text=True))

        with self.app.app_context():
            self.assertIsNone(db.session.get(Post, post_id))

    def test_authenticated_user_is_redirected_away_from_login(self):
        self.register()
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_signup_prompt_only_shows_for_username_login(self):
        phone_page = self.client.get("/login").get_data(as_text=True)
        self.assertIn('data-auth-switch-for="username" hidden', phone_page)

        token = self.csrf_token("/login")
        response = self.client.post(
            "/login",
            data={
                "csrf_token": token,
                "method": "username",
                "username": "nobody",
                "password": "wrong-password",
                "phone": "",
                "code": "",
            },
        )
        username_page = response.get_data(as_text=True)
        self.assertNotIn('data-auth-switch-for="username" hidden', username_page)
        self.assertIn("还没有账号", username_page)

    def test_profile_page_requires_login(self):
        response = self.client.get("/profile")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def configure_admin(self, username="admin", password="admin-pass"):
        self.app.config.update(
            {"ADMIN_USERNAME": username, "ADMIN_PASSWORD": password}
        )

    def test_admin_login_and_dashboard(self):
        self.configure_admin()
        self.register()
        self.create_post(title="管理员可见的帖子", content="正文内容。")

        login_page = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("管理员登录", login_page)
        self.assertNotIn("最新帖子", login_page)

        token = self.csrf_token("/admin")
        response = self.client.post(
            "/admin",
            data={
                "csrf_token": token,
                "username": "admin",
                "password": "wrong-password",
            },
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn(
            "管理员用户名或密码不正确", response.get_data(as_text=True)
        )
        self.assertNotIn("最新帖子", response.get_data(as_text=True))

        token = self.csrf_token("/admin")
        response = self.client.post(
            "/admin",
            data={
                "csrf_token": token,
                "username": "admin",
                "password": "admin-pass",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin")

        dashboard = self.client.get("/admin")
        html = dashboard.get_data(as_text=True)
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("最新帖子", html)
        self.assertIn("注册用户", html)
        self.assertIn("管理员可见的帖子", html)
        self.assertIn("当前管理员：admin", html)

        token = self.csrf_token("/admin")
        response = self.client.post(
            "/admin/logout",
            data={"csrf_token": token},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("已退出管理后台", response.get_data(as_text=True))
        self.assertIn("管理员登录", response.get_data(as_text=True))

    def test_visitor_ips_dashboard_list_and_detail(self):
        self.configure_admin()
        with self.app.app_context():
            db.session.query(Visitor).delete()
            db.session.commit()

        self.client.get("/", environ_overrides={"REMOTE_ADDR": "8.8.8.8"})
        self.client.get("/", environ_overrides={"REMOTE_ADDR": "8.8.8.8"})
        self.client.get("/", environ_overrides={"REMOTE_ADDR": "9.9.9.9"})

        with self.app.app_context():
            old_visitor = Visitor(
                ip_address="1.1.1.1",
                user_agent="old-agent",
                visit_count=1,
                first_seen=utcnow() - timedelta(hours=30),
                last_seen=utcnow() - timedelta(hours=25),
            )
            db.session.add(old_visitor)
            db.session.commit()

        token = self.csrf_token("/admin")
        self.client.post(
            "/admin",
            data={
                "csrf_token": token,
                "username": "admin",
                "password": "admin-pass",
            },
        )

        dashboard = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("24 小时独立 IP", dashboard)
        self.assertIn("最近 24 小时独立 IP：<strong>2</strong> 个", dashboard)
        self.assertIn("累计独立 IP：<strong>3</strong> 个", dashboard)
        self.assertIn("查看所有独立 IP", dashboard)

        visitors_page = self.client.get("/admin/visitors")
        visitors_html = visitors_page.get_data(as_text=True)
        self.assertEqual(visitors_page.status_code, 200)
        self.assertIn("8.8.8.8", visitors_html)
        self.assertIn("9.9.9.9", visitors_html)
        self.assertIn("1.1.1.1", visitors_html)

        with self.app.app_context():
            visitor = db.session.scalar(
                db.select(Visitor).where(Visitor.ip_address == "8.8.8.8")
            )
            visitor_id = visitor.id
            self.assertEqual(visitor.visit_count, 2)

        with patch("app.lookup_ip_location", return_value="测试归属地") as lookup:
            detail = self.client.get(f"/admin/visitors/{visitor_id}")
        detail_html = detail.get_data(as_text=True)
        self.assertEqual(detail.status_code, 200)
        self.assertIn("8.8.8.8", detail_html)
        self.assertIn("测试归属地", detail_html)
        self.assertIn("访问次数", detail_html)
        lookup.assert_called_once_with("8.8.8.8")

        self.assertEqual(lookup_ip_location("127.0.0.1"), "本地或内网地址")
        self.assertEqual(lookup_ip_location("192.168.1.8"), "本地或内网地址")

    def test_visitor_ip_uses_cloudflare_header_behind_proxy(self):
        with self.app.app_context():
            db.session.query(Visitor).delete()
            db.session.commit()

        # Local proxy with Cloudflare in front: REMOTE_ADDR is the proxy itself.
        self.client.get(
            "/",
            environ_overrides={
                "REMOTE_ADDR": "127.0.0.1",
                "HTTP_CF_CONNECTING_IP": "8.8.4.4",
                "HTTP_X_FORWARDED_FOR": "203.0.113.9, 10.0.0.2",
            },
        )
        with self.app.app_context():
            visitor = db.session.scalar(
                db.select(Visitor).where(Visitor.ip_address == "8.8.4.4")
            )
            self.assertIsNotNone(visitor)

        # A direct public connection must not be able to spoof the header.
        self.client.get(
            "/",
            environ_overrides={
                "REMOTE_ADDR": "93.184.216.34",
                "HTTP_CF_CONNECTING_IP": "8.8.8.8",
            },
        )
        with self.app.app_context():
            self.assertIsNotNone(
                db.session.scalar(
                    db.select(Visitor).where(
                        Visitor.ip_address == "93.184.216.34"
                    )
                )
            )
            self.assertIsNone(
                db.session.scalar(
                    db.select(Visitor).where(Visitor.ip_address == "8.8.8.8")
                )
            )

    def test_visitor_records_logged_in_user(self):
        self.configure_admin()
        self.register(username="恩雨", phone="13800138000")

        with self.app.app_context():
            user = db.session.scalar(
                db.select(User).where(User.username == "恩雨")
            )
            visitor = db.session.scalar(
                db.select(Visitor).where(Visitor.ip_address == "127.0.0.1")
            )
            self.assertIsNotNone(user)
            self.assertIsNotNone(visitor)
            self.assertEqual(visitor.user_id, user.id)

        token = self.csrf_token("/admin")
        self.client.post(
            "/admin",
            data={
                "csrf_token": token,
                "username": "admin",
                "password": "admin-pass",
            },
        )

        visitors_html = self.client.get("/admin/visitors").get_data(as_text=True)
        self.assertIn("登录用户", visitors_html)
        self.assertIn("@恩雨", visitors_html)

        with self.app.app_context():
            visitor_id = db.session.scalar(
                db.select(Visitor.id).where(Visitor.ip_address == "127.0.0.1")
            )
        detail_html = self.client.get(
            f"/admin/visitors/{visitor_id}"
        ).get_data(as_text=True)
        self.assertIn("登录用户", detail_html)
        self.assertIn("@恩雨", detail_html)

    def test_visitor_pages_require_admin_login(self):
        response = self.client.get("/admin/visitors")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin")

        response = self.client.get("/admin/visitors/1")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin")

    def test_admin_can_login_from_main_site_and_use_normal_features(self):
        self.configure_admin()

        token = self.csrf_token("/login")
        response = self.client.post(
            "/login",
            data={
                "csrf_token": token,
                "method": "username",
                "username": "admin",
                "password": "admin-pass",
                "phone": "",
                "code": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("管理员登录成功", html)
        self.assertIn("进入后台", html)

        response = self.create_post(title="管理员发布的帖子", content="管理员也可以正常发帖。")
        self.assertEqual(response.status_code, 200)
        self.assertIn("管理员发布的帖子", response.get_data(as_text=True))

        token = self.csrf_token("/posts/1")
        response = self.client.post(
            "/posts/1/comments",
            data={
                "csrf_token": token,
                "content": "管理员也可以发表回应。",
            },
            follow_redirects=True,
        )
        self.assertIn("管理员也可以发表回应", response.get_data(as_text=True))

        admin_page = self.client.get("/admin")
        self.assertEqual(admin_page.status_code, 200)
        self.assertIn("管理后台", admin_page.get_data(as_text=True))

        with self.app.app_context():
            admin_user = db.session.scalar(
                db.select(User).where(User.username_key == "admin")
            )
            self.assertIsNotNone(admin_user)
            post = db.session.get(Post, 1)
            self.assertEqual(post.user_id, admin_user.id)

    def test_regular_user_does_not_see_admin_entry(self):
        self.register()
        home = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("进入后台", home)

    def test_admin_backend_login_grants_normal_user_session(self):
        self.configure_admin()
        token = self.csrf_token("/admin")
        response = self.client.post(
            "/admin",
            data={
                "csrf_token": token,
                "username": "admin",
                "password": "admin-pass",
            },
        )
        self.assertEqual(response.status_code, 302)

        home = self.client.get("/").get_data(as_text=True)
        self.assertIn("进入后台", home)
        self.assertIn("admin", home)

        response = self.create_post(title="后台登录后发的帖子", content="正文内容。")
        self.assertEqual(response.status_code, 200)
        self.assertIn("后台登录后发的帖子", response.get_data(as_text=True))

    def test_admin_login_requires_configuration(self):
        token = self.csrf_token("/admin")
        response = self.client.post(
            "/admin",
            data={
                "csrf_token": token,
                "username": "admin",
                "password": "anything",
            },
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn(
            "管理员账号尚未配置", response.get_data(as_text=True)
        )

    def test_user_can_update_and_clear_nickname(self):
        self.register()

        response = self.client.get("/profile")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("个人资料", html)
        self.assertIn("/profile/avatar", html)

        token = self.csrf_token("/profile")
        response = self.client.post(
            "/profile/nickname",
            data={"csrf_token": token, "nickname": "晨光"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("昵称已更新", html)
        self.assertIn("晨光", html)

        with self.app.app_context():
            user = db.session.scalar(
                db.select(User).where(User.username_key == "恩雨")
            )
            self.assertEqual(user.nickname, "晨光")
            self.assertEqual(user.display_name, "晨光")

        home = self.client.get("/")
        self.assertIn("晨光", home.get_data(as_text=True))
        self.assertIn("/profile", home.get_data(as_text=True))

        self.logout()
        response = self.login_with_password("恩雨")
        self.assertIn("欢迎回来", response.get_data(as_text=True))

        token = self.csrf_token("/profile")
        response = self.client.post(
            "/profile/nickname",
            data={"csrf_token": token, "nickname": ""},
            follow_redirects=True,
        )
        self.assertIn("已恢复使用用户名", response.get_data(as_text=True))
        with self.app.app_context():
            user = db.session.scalar(
                db.select(User).where(User.username_key == "恩雨")
            )
            self.assertIsNone(user.nickname)
            self.assertEqual(user.display_name, "恩雨")

    def test_nickname_validation(self):
        self.register()

        token = self.csrf_token("/profile")
        response = self.client.post(
            "/profile/nickname",
            data={"csrf_token": token, "nickname": "a"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("昵称需为 2–20 个字符", response.get_data(as_text=True))

        token = self.csrf_token("/profile")
        response = self.client.post(
            "/profile/nickname",
            data={"csrf_token": token, "nickname": "a b"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "昵称只能包含中文、英文、数字和下划线",
            response.get_data(as_text=True),
        )

        with self.app.app_context():
            other = User(
                username="Grace", username_key="grace", phone="13900139000"
            )
            other.set_password("grace123")
            db.session.add(other)
            db.session.commit()

        token = self.csrf_token("/profile")
        response = self.client.post(
            "/profile/nickname",
            data={"csrf_token": token, "nickname": "grace"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("这个昵称已经被使用了", response.get_data(as_text=True))

    def upload_avatar(self, data, filename="avatar.jpg"):
        token = self.csrf_token("/profile")
        return self.client.post(
            "/profile/avatar",
            data={"csrf_token": token, "avatar": (BytesIO(data), filename)},
            content_type="multipart/form-data",
            follow_redirects=True,
        )

    def current_user(self):
        return db.session.scalar(
            db.select(User).where(User.username_key == "恩雨")
        )

    def test_user_can_upload_replace_and_remove_avatar(self):
        self.register()

        response = self.upload_avatar(b"\xff\xd8\xff\xe0" + b"0" * 100)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("头像已更新", html)
        self.assertIn("avatar--image", html)
        self.assertIn("恢复默认头像", html)

        with self.app.app_context():
            first_name = self.current_user().avatar_filename
            self.assertIsNotNone(first_name)
        self.assertTrue((self.avatar_dir / first_name).exists())

        image_response = self.client.get(f"/avatars/{first_name}")
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response.content_type, "image/jpeg")
        self.assertEqual(
            image_response.headers["X-Content-Type-Options"], "nosniff"
        )
        image_response.close()

        response = self.upload_avatar(
            b"\x89PNG\r\n\x1a\n" + b"0" * 100, filename="avatar.png"
        )
        self.assertIn("头像已更新", response.get_data(as_text=True))

        with self.app.app_context():
            second_name = self.current_user().avatar_filename
        self.assertNotEqual(first_name, second_name)
        self.assertFalse((self.avatar_dir / first_name).exists())
        self.assertTrue((self.avatar_dir / second_name).exists())

        token = self.csrf_token("/profile")
        response = self.client.post(
            "/profile/avatar/remove",
            data={"csrf_token": token},
            follow_redirects=True,
        )
        self.assertIn("已恢复默认头像", response.get_data(as_text=True))

        with self.app.app_context():
            self.assertIsNone(self.current_user().avatar_filename)
        self.assertFalse((self.avatar_dir / second_name).exists())
        self.assertEqual(
            self.client.get(f"/avatars/{second_name}").status_code, 404
        )

    def configure_mail(self):
        self.app.config.update(
            {
                "MAIL_USERNAME": "owner@qq.com",
                "MAIL_AUTH_CODE": "auth-code",
                "MAIL_TO": "owner@qq.com",
                "MAIL_HOST": "smtp.qq.com",
                "MAIL_PORT": "465",
            }
        )

    def create_post(self, title="今天的感恩", content="感谢今天收到的每一份善意。"):
        token = self.csrf_token("/posts/new")
        return self.client.post(
            "/posts/new",
            data={"csrf_token": token, "title": title, "content": content},
            follow_redirects=True,
        )

    def test_post_and_comment_send_email_notifications(self):
        self.configure_mail()
        self.register()

        with patch("app.smtplib.SMTP_SSL") as smtp_ssl:
            server = smtp_ssl.return_value.__enter__.return_value
            response = self.create_post()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(server.send_message.call_count, 1)

            post_message = server.send_message.call_args.args[0]
            self.assertIn("新帖子", post_message["Subject"])
            self.assertIn("今天的感恩", post_message["Subject"])
            post_body = post_message.get_content()
            self.assertIn("恩雨", post_body)
            self.assertIn("/posts/1", post_body)

            token = self.csrf_token("/posts/1")
            response = self.client.post(
                "/posts/1/comments",
                data={
                    "csrf_token": token,
                    "content": "愿平安与你同在，谢谢你的分享。",
                },
                follow_redirects=True,
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(server.send_message.call_count, 2)

            comment_message = server.send_message.call_args.args[0]
            self.assertIn("新回应", comment_message["Subject"])
            comment_body = comment_message.get_content()
            self.assertIn("愿平安与你同在", comment_body)
            self.assertIn("comment-1", comment_body)

        smtp_ssl.assert_called_with("smtp.qq.com", 465, timeout=15)
        server.login.assert_called_with("owner@qq.com", "auth-code")

    def test_no_email_is_sent_when_mail_is_not_configured(self):
        self.register()
        with patch("app.smtplib.SMTP_SSL") as smtp_ssl:
            self.create_post()
        smtp_ssl.assert_not_called()

    def test_mail_failure_does_not_break_posting(self):
        self.configure_mail()
        self.register()

        with patch("app.smtplib.SMTP_SSL") as smtp_ssl:
            server = smtp_ssl.return_value.__enter__.return_value
            server.login.side_effect = smtplib.SMTPAuthenticationError(
                535, b"authentication failed"
            )
            response = self.create_post(title="仍可发布", content="邮件失败不应影响发帖。")

        self.assertEqual(response.status_code, 200)
        self.assertIn("仍可发布", response.get_data(as_text=True))
        self.assertTrue(server.login.called)

    def test_avatar_rejects_unsupported_file(self):
        self.register()

        response = self.upload_avatar(b"<svg></svg>", filename="avatar.svg")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "头像仅支持 JPG、PNG、GIF 和 WebP 格式",
            response.get_data(as_text=True),
        )

        with self.app.app_context():
            self.assertIsNone(self.current_user().avatar_filename)
        self.assertEqual(list(self.avatar_dir.glob("*")), [])

    def user_id_for(self, username):
        with self.app.app_context():
            return db.session.scalar(
                db.select(User.id).where(User.username == username)
            )

    def register_second_user(self):
        self.logout()
        return self.register(username="晨光", phone="13900139000")

    def test_user_profile_has_private_message_button(self):
        self.register(username="恩雨", phone="13800138000")
        first_id = self.user_id_for("恩雨")
        self.register_second_user()
        second_id = self.user_id_for("晨光")

        html = self.client.get(f"/users/{first_id}").get_data(as_text=True)
        self.assertIn(f"/messages/{first_id}", html)
        self.assertIn("私信", html)

        html = self.client.get(f"/users/{second_id}").get_data(as_text=True)
        self.assertIn("我的私信", html)
        self.assertNotIn(f"/messages/{second_id}", html)

    def test_guest_is_redirected_from_private_messages_to_login(self):
        response = self.client.get("/messages")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

        response = self.client.get("/messages/1")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_users_can_exchange_private_messages(self):
        self.register(username="恩雨", phone="13800138000")
        first_id = self.user_id_for("恩雨")
        self.register_second_user()
        second_id = self.user_id_for("晨光")

        token = self.csrf_token(f"/messages/{first_id}")
        response = self.client.post(
            f"/messages/{first_id}",
            data={
                "csrf_token": token,
                "body": "主内平安，很高兴认识你。",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("主内平安，很高兴认识你。", html)
        self.assertIn("聊天记录保留最近 7 天", html)

        with self.app.app_context():
            message = db.session.scalar(db.select(Message))
            self.assertEqual(message.sender_id, second_id)
            self.assertEqual(message.recipient_id, first_id)
            self.assertIsNone(message.read_at)

        self.logout()
        response = self.login_with_password("恩雨")
        inbox_html = self.client.get("/messages").get_data(as_text=True)
        self.assertIn("晨光", inbox_html)
        self.assertIn("主内平安，很高兴认识你。", inbox_html)

        response = self.client.get(f"/messages/{second_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("主内平安，很高兴认识你。", response.get_data(as_text=True))
        with self.app.app_context():
            message = db.session.scalar(db.select(Message))
            self.assertIsNotNone(message.read_at)

        token = self.csrf_token(f"/messages/{second_id}")
        self.client.post(
            f"/messages/{second_id}",
            data={"csrf_token": token, "body": "平安，愿主赐福与你。"},
            follow_redirects=True,
        )
        with self.app.app_context():
            bodies = db.session.scalars(
                db.select(Message.body).order_by(Message.id)
            ).all()
            self.assertEqual(
                bodies,
                ["主内平安，很高兴认识你。", "平安，愿主赐福与你。"],
            )

    def test_private_messages_are_not_visible_to_other_users(self):
        self.register(username="恩雨", phone="13800138000")
        first_id = self.user_id_for("恩雨")
        self.register_second_user()
        second_id = self.user_id_for("晨光")

        token = self.csrf_token(f"/messages/{first_id}")
        self.client.post(
            f"/messages/{first_id}",
            data={"csrf_token": token, "body": "只写给恩雨的私信。"},
            follow_redirects=True,
        )

        self.logout()
        self.register(username="小羊", phone="13700137000")
        third_id = self.user_id_for("小羊")

        response = self.client.get(f"/messages/{first_id}")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("只写给恩雨的私信。", response.get_data(as_text=True))
        self.assertNotIn(
            "只写给恩雨的私信。",
            self.client.get("/messages").get_data(as_text=True),
        )
        # 与第二位用户是同一段对话，但第三方看到的是自己的空会话。
        response = self.client.get(f"/messages/{second_id}")
        self.assertNotIn("只写给恩雨的私信。", response.get_data(as_text=True))
        self.assertNotEqual(third_id, first_id)

    def test_private_message_validation_and_self_message(self):
        self.register(username="恩雨", phone="13800138000")
        first_id = self.user_id_for("恩雨")
        self.register_second_user()
        second_id = self.user_id_for("晨光")

        token = self.csrf_token(f"/messages/{first_id}")
        response = self.client.post(
            f"/messages/{first_id}",
            data={"csrf_token": token, "body": "   "},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("私信内容需为 1–1000 个字符", response.get_data(as_text=True))

        token = self.csrf_token(f"/messages/{first_id}")
        response = self.client.post(
            f"/messages/{first_id}",
            data={"csrf_token": token, "body": "长" * 1001},
        )
        self.assertEqual(response.status_code, 400)

        token = self.csrf_token("/messages")
        response = self.client.post(
            f"/messages/{second_id}",
            data={"csrf_token": token, "body": "写给自己"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/messages", response.headers["Location"])

        with self.app.app_context():
            self.assertEqual(
                db.session.scalar(db.select(db.func.count(Message.id))), 0
            )

    def test_expired_private_messages_are_purged(self):
        self.register(username="恩雨", phone="13800138000")
        first_id = self.user_id_for("恩雨")
        self.register_second_user()
        second_id = self.user_id_for("晨光")

        with self.app.app_context():
            now = utcnow()
            db.session.add_all(
                [
                    Message(
                        sender_id=second_id,
                        recipient_id=first_id,
                        body="八天前的旧私信",
                        created_at=now - timedelta(days=8),
                    ),
                    Message(
                        sender_id=second_id,
                        recipient_id=first_id,
                        body="六天前的私信",
                        created_at=now - timedelta(days=6),
                    ),
                ]
            )
            db.session.commit()

        html = self.client.get("/messages").get_data(as_text=True)
        self.assertNotIn("八天前的旧私信", html)
        self.assertIn("六天前的私信", html)

        with self.app.app_context():
            bodies = db.session.scalars(db.select(Message.body)).all()
            self.assertEqual(bodies, ["六天前的私信"])


if __name__ == "__main__":
    unittest.main()
