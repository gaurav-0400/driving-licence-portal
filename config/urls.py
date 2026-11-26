from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from portal import views

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="login.html"),
        name="login",
    ),
    path(
        "logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),
    path("", views.dashboard, name="dashboard"),
    path("create-link/", views.create_link, name="create_link"),
    path("customers/", views.customers_list, name="customers_list"),
    path("customers/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("payments/", views.payments_list, name="payments_list"),
    path("settings/", views.portal_settings_view, name="portal_settings"),
    path(
        "settings/quick-update/",
        views.quick_settings_update,
        name="quick_settings_update",
    ),
    path("apply/<str:token>/", views.customer_form, name="customer_form"),
    path("payment/return/", views.payment_return, name="payment_return"),
    path("webhooks/cashfree/", views.cashfree_webhook, name="cashfree_webhook"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
