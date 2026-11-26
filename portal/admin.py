from django.contrib import admin

from .models import Customer, CustomerLink, PortalSettings


@admin.register(PortalSettings)
class PortalSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "default_price",
        "default_discount_percent",
        "upi_id",
        "updated_at",
    )


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "application_number",
        "phone",
        "state",
        "final_amount",
        "payment_status",
        "created_at",
    )
    search_fields = ("full_name", "application_number", "phone", "cashfree_order_id")
    list_filter = ("state", "payment_status")


@admin.register(CustomerLink)
class CustomerLinkAdmin(admin.ModelAdmin):
    list_display = (
        "token",
        "base_price",
        "discount",
        "expires_at",
        "is_used",
    )
