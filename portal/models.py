import secrets

from django.db import models


def generate_token():
    return secrets.token_urlsafe(32)


class PortalSettings(models.Model):
    """Global pricing, default discount (%), and UPI/QR display settings."""

    default_price = models.PositiveIntegerField(default=300)
    default_discount_percent = models.PositiveIntegerField(default=0)
    upi_id = models.CharField(max_length=120, blank=True, default="")
    payment_qr = models.ImageField(
        upload_to="qr_codes/", blank=True, null=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Portal Settings"
        verbose_name_plural = "Portal Settings"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f"Settings (₹{self.default_price}, {self.default_discount_percent}% off)"


class CustomerLink(models.Model):
    token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_token,
    )
    base_price = models.PositiveIntegerField(default=300)
    # Percentage discount (0 - 100)
    discount = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    def calculate_amounts(self):
        pct = min(max(self.discount, 0), 100)
        discount_amount = (self.base_price * pct) // 100
        final_amount = max(0, self.base_price - discount_amount)
        return self.base_price, pct, discount_amount, final_amount

    def __str__(self):
        return f"Customer Link ({self.discount}% off) - {self.created_at}"


class Customer(models.Model):

    PAYMENT_STATUS = [
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("verified", "Verified"),
    ]

    full_name = models.CharField(max_length=150, default="")
    phone = models.CharField(max_length=15, default="")
    application_number = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    state = models.CharField(max_length=100)
    rto_password = models.CharField(max_length=150, default="")
    profile_picture = models.ImageField(upload_to="customer_photos/")

    base_price = models.PositiveIntegerField(default=300)
    # Percentage discount (0 - 100)
    discount = models.PositiveIntegerField(default=0)
    discount_amount = models.PositiveIntegerField(default=0)
    final_amount = models.PositiveIntegerField(default=300)

    payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS,
        default="pending",
    )

    cashfree_order_id = models.CharField(max_length=100, blank=True, default="")
    cashfree_payment_session_id = models.CharField(
        max_length=255, blank=True, default=""
    )
    cashfree_payment_id = models.CharField(
        max_length=100, blank=True, default=""
    )
    paid_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    link = models.OneToOneField(CustomerLink, on_delete=models.PROTECT)

    def __str__(self):
        return f"{self.full_name} ({self.application_number})"
