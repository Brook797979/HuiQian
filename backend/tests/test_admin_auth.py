import os
import shutil
import sys
import tempfile
import unittest


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import attendance
import app as app_module


class AdminAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="huiqian-admin-tests-")
        self.original_db = attendance.DB
        attendance.DB = os.path.join(self.temp_dir, "huiqian.db")
        attendance.init_db()
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def tearDown(self):
        attendance.DB = self.original_db
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def create_admin(self, username="admin", password="AdminPass123!"):
        account, error = attendance.create_admin(username, password)
        self.assertIsNone(error)
        return account

    def login(self, username="admin", password="AdminPass123!"):
        response = self.client.post(
            "/api/admin/auth/login",
            json={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["token"]

    def admin_headers(self, token):
        return {"Authorization": "Bearer " + token}

    def test_admin_login_issues_session_and_me_returns_identity(self):
        self.create_admin()
        token = self.login()

        response = self.client.get("/api/admin/auth/me", headers=self.admin_headers(token))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["admin"]["username"], "admin")

    def test_invalid_admin_password_is_rejected(self):
        self.create_admin()

        response = self.client.post(
            "/api/admin/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["code"], "ADMIN_CREDENTIALS_INVALID")

    def test_unknown_admin_account_has_a_distinct_error_code(self):
        response = self.client.post(
            "/api/admin/auth/login",
            json={"username": "missing-admin", "password": "AdminPass123!"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["code"], "ADMIN_ACCOUNT_NOT_FOUND")

    def test_management_routes_require_an_admin_session(self):
        response = self.client.get("/api/users")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["code"], "ADMIN_AUTH_REQUIRED")

    def test_enabled_admin_can_read_management_routes(self):
        self.create_admin()
        token = self.login()

        response = self.client.get("/api/users", headers=self.admin_headers(token))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

    def test_disabled_admin_session_is_rejected(self):
        self.create_admin()
        account = self.create_admin("backup-admin", "BackupPass123!")
        token = self.login("backup-admin", "BackupPass123!")
        updated, error = attendance.set_admin_enabled(account["id"], False)
        self.assertIsNone(error)
        self.assertEqual(updated["enabled"], 0)

        response = self.client.get("/api/users", headers=self.admin_headers(token))

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["code"], "ADMIN_SESSION_INVALID")

    def test_logout_revokes_current_session(self):
        self.create_admin()
        token = self.login()

        response = self.client.post(
            "/api/admin/auth/logout", headers=self.admin_headers(token)
        )

        self.assertEqual(response.status_code, 200)
        rejected = self.client.get("/api/users", headers=self.admin_headers(token))
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(rejected.get_json()["code"], "ADMIN_SESSION_INVALID")

    def test_password_reset_revokes_existing_sessions(self):
        account = self.create_admin()
        token = self.login()

        updated, error = attendance.reset_admin_password(
            account["id"], "NewAdminPass123!"
        )

        self.assertIsNone(error)
        self.assertEqual(updated["username"], "admin")
        rejected = self.client.get("/api/users", headers=self.admin_headers(token))
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(rejected.get_json()["code"], "ADMIN_SESSION_INVALID")

    def test_student_login_does_not_authorize_management_route(self):
        attendance.add_user("student")

        login = self.client.post(
            "/api/login", json={"name": "student", "password": attendance.DEFAULT_PASSWORD}
        )

        self.assertEqual(login.status_code, 200)
        response = self.client.get("/api/users")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["code"], "ADMIN_AUTH_REQUIRED")

    def test_duplicate_admin_username_is_rejected(self):
        self.create_admin()
        token = self.login()

        response = self.client.post(
            "/api/admin/accounts",
            json={"username": "ADMIN", "password": "AnotherPass123!"},
            headers=self.admin_headers(token),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "ADMIN_ACCOUNT_EXISTS")

    def test_weak_admin_password_is_rejected(self):
        self.create_admin()
        token = self.login()

        response = self.client.post(
            "/api/admin/accounts",
            json={"username": "reviewer", "password": "password"},
            headers=self.admin_headers(token),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "PASSWORD_INVALID")

    def test_first_admin_becomes_super_admin(self):
        self.create_admin()
        token = self.login()

        response = self.client.get("/api/admin/auth/me", headers=self.admin_headers(token))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["admin"]["account_type"], "super_admin")

    def test_super_admin_creates_a_regular_admin(self):
        self.create_admin()
        token = self.login()

        response = self.client.post(
            "/api/admin/accounts",
            json={"username": "reviewer", "password": "ReviewerPass123!"},
            headers=self.admin_headers(token),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["admin"]["account_type"], "admin")

    def test_regular_admin_cannot_manage_other_admin_accounts(self):
        self.create_admin()
        super_token = self.login()
        created = self.client.post(
            "/api/admin/accounts",
            json={"username": "reviewer", "password": "ReviewerPass123!"},
            headers=self.admin_headers(super_token),
        )
        reviewer_id = created.get_json()["admin"]["id"]
        reviewer_token = self.login("reviewer", "ReviewerPass123!")

        accounts = self.client.get("/api/admin/accounts", headers=self.admin_headers(reviewer_token))
        password = self.client.post(
            "/api/admin/accounts/%d/password" % reviewer_id,
            json={"password": "AnotherPass123!"},
            headers=self.admin_headers(reviewer_token),
        )

        self.assertEqual(accounts.status_code, 403)
        self.assertEqual(accounts.get_json()["code"], "ADMIN_PERMISSION_DENIED")
        self.assertEqual(password.status_code, 403)
        self.assertEqual(password.get_json()["code"], "ADMIN_PERMISSION_DENIED")

    def test_regular_admin_can_change_only_own_password(self):
        self.create_admin()
        super_token = self.login()
        self.client.post(
            "/api/admin/accounts",
            json={"username": "reviewer", "password": "ReviewerPass123!"},
            headers=self.admin_headers(super_token),
        )
        reviewer_token = self.login("reviewer", "ReviewerPass123!")

        changed = self.client.post(
            "/api/admin/auth/password",
            json={"current_password": "ReviewerPass123!", "new_password": "ChangedPass123!"},
            headers=self.admin_headers(reviewer_token),
        )

        self.assertEqual(changed.status_code, 200)
        rejected = self.client.get("/api/users", headers=self.admin_headers(reviewer_token))
        self.assertEqual(rejected.status_code, 401)
        replacement_token = self.login("reviewer", "ChangedPass123!")
        self.assertTrue(replacement_token)

    def test_last_enabled_super_admin_cannot_be_disabled(self):
        account = self.create_admin()

        updated, error = attendance.set_admin_enabled(account["id"], False)

        self.assertIsNone(updated)
        self.assertEqual(error, "LAST_SUPER_ADMIN_PROTECTED")

    def test_admin_can_create_and_disable_another_admin(self):
        self.create_admin()
        token = self.login()

        created = self.client.post(
            "/api/admin/accounts",
            json={"username": "reviewer", "password": "ReviewerPass123!"},
            headers=self.admin_headers(token),
        )
        self.assertEqual(created.status_code, 201)
        reviewer_id = created.get_json()["admin"]["id"]

        disabled = self.client.post(
            "/api/admin/accounts/%d/status" % reviewer_id,
            json={"enabled": False},
            headers=self.admin_headers(token),
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(disabled.get_json()["admin"]["enabled"], 0)


if __name__ == "__main__":
    unittest.main()
