import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.auth import AuthService, SESSION_COOKIE


class GithubOAuthAuthTests(unittest.TestCase):
    def test_callback_success_creates_session_cookie(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(
                "os.environ",
                {
                    "QTB_AUTH_MODE": "github",
                    "QTB_GITHUB_CLIENT_ID": "cid",
                    "QTB_GITHUB_CLIENT_SECRET": "secret",
                    "QTB_GITHUB_ALLOWED_ORGS": "varsity-tech-product",
                    "QTB_ADMIN_GITHUB_LOGINS": "alice",
                },
                clear=False,
            ):
                auth = AuthService(root)
                state = auth.store.create_state("/#/results")

                async def callback(request):
                    return await auth.callback(request)

                app = Starlette(routes=[Route("/auth/callback", callback)])
                with patch.object(auth, "_exchange_code", return_value="tok"), patch.object(
                    auth,
                    "_fetch_profile",
                    return_value={
                        "login": "alice",
                        "email": "alice@example.com",
                        "name": "Alice",
                        "avatar_url": "https://example/avatar.png",
                    },
                ), patch.object(auth, "_is_allowed", return_value=True):
                    client = TestClient(app)
                    response = client.get(
                        f"/auth/callback?code=abc&state={state}",
                        follow_redirects=False,
                    )

                self.assertEqual(response.status_code, 302)
                self.assertIn(SESSION_COOKIE, response.headers.get("set-cookie", ""))
                cookie_value = response.cookies.get(SESSION_COOKIE)
                user = auth.store.get_session(cookie_value)
                self.assertIsNotNone(user)
                self.assertEqual(user.github_login, "alice")
                self.assertEqual(user.role, "admin")

    def test_invalid_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"QTB_AUTH_MODE": "github"}, clear=False):
                auth = AuthService(Path(tmp))

                async def callback(request):
                    return await auth.callback(request)

                client = TestClient(Starlette(routes=[Route("/auth/callback", callback)]))
                response = client.get("/auth/callback?code=abc&state=bad")
                self.assertEqual(response.status_code, 400)

    def test_org_allowlist_accepts_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ",
                {"QTB_GITHUB_ALLOWED_ORGS": "varsity-tech-product"},
                clear=True,
            ):
                auth = AuthService(Path(tmp))
                with patch.object(
                    auth,
                    "_github_get",
                    return_value=[{"login": "varsity-tech-product"}],
                ):
                    self.assertTrue(auth._is_allowed({"login": "alice"}, "tok"))

    def test_empty_allowlist_accepts_any_github_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {}, clear=True):
                auth = AuthService(Path(tmp))
                with patch.object(auth, "_github_get") as github_get:
                    self.assertTrue(auth._is_allowed({"login": "external"}, "tok"))
                    github_get.assert_not_called()

    def test_allow_all_accepts_account_with_org_allowlist_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ",
                {
                    "QTB_GITHUB_ALLOW_ALL": "true",
                    "QTB_GITHUB_ALLOWED_ORGS": "varsity-tech-product",
                },
                clear=True,
            ):
                auth = AuthService(Path(tmp))
                with patch.object(auth, "_github_get") as github_get:
                    self.assertTrue(auth._is_allowed({"login": "external"}, "tok"))
                    github_get.assert_not_called()

    def test_access_policy_reports_public_when_allow_all_overrides_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ",
                {
                    "QTB_AUTH_MODE": "github",
                    "QTB_GITHUB_ALLOW_ALL": "true",
                    "QTB_GITHUB_ALLOWED_ORGS": "varsity-tech-product",
                },
                clear=True,
            ):
                auth = AuthService(Path(tmp))
                self.assertEqual(auth.github_access_policy(), "public")

    def test_access_policy_reports_invite_only_when_allowlist_is_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ",
                {
                    "QTB_AUTH_MODE": "github",
                    "QTB_GITHUB_ALLOWED_ORGS": "varsity-tech-product",
                },
                clear=True,
            ):
                auth = AuthService(Path(tmp))
                self.assertEqual(auth.github_access_policy(), "invite_only")

    def test_org_allowlist_rejects_outsider(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ",
                {"QTB_GITHUB_ALLOWED_ORGS": "varsity-tech-product"},
                clear=True,
            ):
                auth = AuthService(Path(tmp))
                with patch.object(auth, "_github_get", return_value=[{"login": "other"}]):
                    self.assertFalse(auth._is_allowed({"login": "alice"}, "tok"))

    def test_admin_role_can_match_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ",
                {"QTB_ADMIN_EMAILS": "alice@example.com"},
                clear=False,
            ):
                auth = AuthService(Path(tmp))
                user = auth._build_user(
                    {
                        "login": "alice",
                        "email": "alice@example.com",
                        "name": "Alice",
                    }
                )
                self.assertEqual(user.role, "admin")


if __name__ == "__main__":
    unittest.main()
