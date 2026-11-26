import base64
import hashlib
import hmac
import json
import shutil
import tempfile
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .forms import CustomerForm
from .models import Customer, CustomerLink, PortalSettings


def _create_test_image(fmt="PNG", size_bytes=None):
    buffer = BytesIO()
    Image.new("RGB", (20, 20), color="blue").save(buffer, format=fmt)
    content = buffer.getvalue()
    if size_bytes and len(content) < size_bytes:
        content = content + (b"\x00" * (size_bytes - len(content)))
    ext = "jpg" if fmt == "JPEG" else "png"
    ctype = "image/jpeg" if fmt == "JPEG" else "image/png"
    return SimpleUploadedFile(f"photo.{ext}", content, content_type=ctype)


class PortalWorkflowTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.mkdtemp()
        self.user = get_user_model().objects.create_user(
            username="adminuser", password="secretpassword123"
        )

    def tearDown(self):
        shutil.rmtree(self.media_dir, ignore_errors=True)

    def test_customer_form_has_name_first(self):
        form = CustomerForm()
        self.assertEqual(list(form.fields.keys())[0], "full_name")

    def test_photo_validation_rejects_oversized_or_non_jpg_png(self):
        link = CustomerLink.objects.create(
            base_price=300,
            discount=0,
            expires_at=timezone.now() + timedelta(days=2),
        )
        url = reverse("customer_form", args=[link.token])

        oversized = _create_test_image(fmt="PNG", size_bytes=2 * 1024 * 1024 + 10)
        resp = self.client.post(
            url,
            {
                "full_name": "Rohan Sharma",
                "phone": "9876543210",
                "application_number": "DL2025-1048",
                "date_of_birth": "2000-01-01",
                "state": "Punjab",
                "rto_password": "mypassword",
                "profile_picture": oversized,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "2 MB or smaller")

        gif_img = _create_test_image(fmt="GIF")
        resp_gif = self.client.post(
            url,
            {
                "full_name": "Rohan Sharma",
                "phone": "9876543210",
                "application_number": "DL2025-1048",
                "date_of_birth": "2000-01-01",
                "state": "Punjab",
                "rto_password": "mypassword",
                "profile_picture": gif_img,
            },
        )
        self.assertEqual(resp_gif.status_code, 200)
        self.assertContains(resp_gif, "Only JPG and PNG images are allowed.")

    def test_quick_settings_and_percentage_link_creation(self):
        self.client.force_login(self.user)
        resp_settings = self.client.post(
            reverse("quick_settings_update"),
            {"default_price": "400", "default_discount_percent": "25"},
        )
        self.assertRedirects(resp_settings, reverse("dashboard"))

        settings_obj = PortalSettings.get_solo()
        self.assertEqual(settings_obj.default_price, 400)
        self.assertEqual(settings_obj.default_discount_percent, 25)

        resp_link = self.client.post(
            reverse("create_link"), {"discount": "25"}
        )
        self.assertEqual(resp_link.status_code, 200)
        link = CustomerLink.objects.get()
        self.assertEqual(link.base_price, 400)
        self.assertEqual(link.discount, 25)
        self.assertContains(resp_link, "₹300")

    @patch("portal.views.cashfree.create_order")
    @patch("portal.views.cashfree.fetch_order")
    def test_customer_form_cashfree_checkout_and_return_verification(
        self, mock_fetch_order, mock_create_order
    ):
        mock_create_order.return_value = {
            "order_id": "DL_1_test",
            "payment_session_id": "session_test_123",
            "order_status": "ACTIVE",
        }
        mock_fetch_order.return_value = {
            "order_id": "DL_1_test",
            "order_status": "PAID",
        }

        link = CustomerLink.objects.create(
            base_price=300,
            discount=20,  # 20% off => ₹60 off => ₹240 payable
            expires_at=timezone.now() + timedelta(days=2),
        )
        url = reverse("customer_form", args=[link.token])

        with override_settings(MEDIA_ROOT=self.media_dir):
            get_resp = self.client.get(url)
            self.assertEqual(get_resp.status_code, 200)
            self.assertContains(get_resp, "20%")
            self.assertContains(get_resp, "₹240")

            post_resp = self.client.post(
                url,
                {
                    "full_name": "Rohan Sharma",
                    "phone": "9876543210",
                    "application_number": "DL2025-1048",
                    "date_of_birth": "2000-01-01",
                    "state": "Punjab",
                    "rto_password": "secret123",
                    "profile_picture": _create_test_image(fmt="JPEG"),
                },
            )

        self.assertEqual(post_resp.status_code, 200)
        self.assertContains(post_resp, "session_test_123")
        self.assertContains(post_resp, "₹240")

        customer = Customer.objects.get(link=link)
        self.assertEqual(customer.full_name, "Rohan Sharma")
        self.assertEqual(customer.discount, 20)
        self.assertEqual(customer.discount_amount, 60)
        self.assertEqual(customer.final_amount, 240)
        self.assertEqual(customer.payment_status, "pending")
        self.assertFalse(link.is_used)

        return_resp = self.client.get(
            reverse("payment_return"), {"order_id": customer.cashfree_order_id}
        )
        self.assertEqual(return_resp.status_code, 200)
        self.assertContains(return_resp, "Application Submitted &amp; Paid!")

        customer.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual(customer.payment_status, "paid")
        self.assertTrue(link.is_used)

    @override_settings(CASHFREE_SECRET_KEY="test_secret_key")
    def test_cashfree_webhook_signature_and_status_update(self):
        link = CustomerLink.objects.create(
            base_price=300,
            discount=0,
            expires_at=timezone.now() + timedelta(days=2),
        )
        with override_settings(MEDIA_ROOT=self.media_dir):
            customer = Customer.objects.create(
                full_name="Priya Verma",
                phone="9876543211",
                application_number="DL2025-1047",
                date_of_birth="1998-05-15",
                state="Delhi",
                rto_password="pw123",
                profile_picture=_create_test_image(fmt="PNG"),
                base_price=300,
                discount=0,
                discount_amount=0,
                final_amount=300,
                cashfree_order_id="DL_ORDER_999",
                link=link,
            )

        payload_bytes = json.dumps(
            {
                "type": "PAYMENT_SUCCESS_WEBHOOK",
                "data": {
                    "order": {"order_id": "DL_ORDER_999"},
                    "payment": {
                        "cf_payment_id": "CF_PAY_555",
                        "payment_status": "SUCCESS",
                    },
                },
            }
        ).encode("utf-8")
        timestamp = "1700000000"
        sig = base64.b64encode(
            hmac.new(
                b"test_secret_key",
                timestamp.encode("utf-8") + payload_bytes,
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        bad_resp = self.client.post(
            reverse("cashfree_webhook"),
            data=payload_bytes,
            content_type="application/json",
            HTTP_X_WEBHOOK_SIGNATURE="invalid",
            HTTP_X_WEBHOOK_TIMESTAMP=timestamp,
        )
        self.assertEqual(bad_resp.status_code, 400)

        ok_resp = self.client.post(
            reverse("cashfree_webhook"),
            data=payload_bytes,
            content_type="application/json",
            HTTP_X_WEBHOOK_SIGNATURE=sig,
            HTTP_X_WEBHOOK_TIMESTAMP=timestamp,
        )
        self.assertEqual(ok_resp.status_code, 200)

        customer.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual(customer.payment_status, "paid")
        self.assertEqual(customer.cashfree_payment_id, "CF_PAY_555")
        self.assertTrue(link.is_used)

    def test_100_percent_discount_bypasses_checkout_and_admin_views_load(self):
        link = CustomerLink.objects.create(
            base_price=300,
            discount=100,
            expires_at=timezone.now() + timedelta(days=2),
        )
        with override_settings(MEDIA_ROOT=self.media_dir):
            resp = self.client.post(
                reverse("customer_form", args=[link.token]),
                {
                    "full_name": "Amit Kumar",
                    "phone": "9876543212",
                    "application_number": "DL2025-1046",
                    "date_of_birth": "2001-07-20",
                    "state": "Haryana",
                    "rto_password": "pass",
                    "profile_picture": _create_test_image(fmt="PNG"),
                },
            )
        self.assertEqual(resp.status_code, 200)
        customer = Customer.objects.get(link=link)
        self.assertEqual(customer.final_amount, 0)
        self.assertEqual(customer.payment_status, "verified")

        self.client.force_login(self.user)
        for route in [
            reverse("dashboard"),
            reverse("customers_list"),
            reverse("customer_detail", args=[customer.pk]),
            reverse("payments_list"),
            reverse("portal_settings"),
        ]:
            page = self.client.get(route)
            self.assertEqual(page.status_code, 200)
