import re

from django import forms
from PIL import Image

from .models import Customer, PortalSettings
from .states import ALL_STATES

STATE_CHOICES = [("", "Select State / Union Territory")] + [
    (state, state) for state in ALL_STATES
]

MAX_PHOTO_BYTES = 2 * 1024 * 1024  # 2 MB
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG"}


class CustomerForm(forms.ModelForm):
    """Customer registration form with Name first and photo/phone validation."""

    state = forms.ChoiceField(
        choices=STATE_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = Customer
        fields = [
            "full_name",
            "phone",
            "application_number",
            "date_of_birth",
            "state",
            "rto_password",
            "profile_picture",
        ]
        labels = {
            "full_name": "Full Name",
            "phone": "Mobile Number",
            "application_number": "Application Number",
            "date_of_birth": "Date of Birth",
            "state": "State",
            "rto_password": "Password (RTO Login)",
            "profile_picture": "Profile Picture",
        }
        widgets = {
            "full_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Enter your full name",
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "10-digit mobile number",
                    "maxlength": "15",
                }
            ),
            "application_number": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. DL2025-1048",
                }
            ),
            "date_of_birth": forms.DateInput(
                attrs={
                    "class": "form-control",
                    "type": "date",
                }
            ),
            "rto_password": forms.PasswordInput(
                render_value=True,
                attrs={
                    "class": "form-control",
                    "placeholder": "Enter RTO login password",
                    "id": "id_rto_password",
                },
            ),
            "profile_picture": forms.ClearableFileInput(
                attrs={
                    "class": "form-control d-none",
                    "accept": "image/jpeg,image/png",
                    "id": "id_profile_picture",
                }
            ),
        }

    def clean_phone(self):
        raw = self.cleaned_data.get("phone", "")
        digits = re.sub(r"\D", "", raw)
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]
        if len(digits) != 10:
            raise forms.ValidationError(
                "Please enter a valid 10-digit mobile number."
            )
        return digits

    def clean_profile_picture(self):
        photo = self.cleaned_data.get("profile_picture")
        if not photo:
            return photo
        if photo.size > MAX_PHOTO_BYTES:
            raise forms.ValidationError(
                "Profile picture must be 2 MB or smaller."
            )
        try:
            photo.seek(0)
            with Image.open(photo) as img:
                img_format = img.format
            photo.seek(0)
        except Exception as exc:
            raise forms.ValidationError(
                "Please upload a valid JPG or PNG image."
            ) from exc

        if img_format not in ALLOWED_IMAGE_FORMATS:
            raise forms.ValidationError(
                "Only JPG and PNG images are allowed."
            )
        return photo


class PortalSettingsForm(forms.ModelForm):
    """Admin form for global price, default percentage discount, UPI ID, and QR."""

    class Meta:
        model = PortalSettings
        fields = [
            "default_price",
            "default_discount_percent",
            "upi_id",
            "payment_qr",
        ]
        widgets = {
            "default_price": forms.NumberInput(
                attrs={"class": "form-control", "min": "1"}
            ),
            "default_discount_percent": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "max": "100"}
            ),
            "upi_id": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. merchant@upi",
                }
            ),
            "payment_qr": forms.ClearableFileInput(
                attrs={"class": "form-control", "accept": "image/*"}
            ),
        }

    def clean_default_price(self):
        price = self.cleaned_data["default_price"]
        if price < 1:
            raise forms.ValidationError("Default price must be at least ₹1.")
        return price

    def clean_default_discount_percent(self):
        discount = self.cleaned_data["default_discount_percent"]
        if not 0 <= discount <= 100:
            raise forms.ValidationError("Discount must be between 0% and 100%.")
        return discount
