import json
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import cashfree
from .forms import CustomerForm, PortalSettingsForm
from .models import Customer, CustomerLink, PortalSettings


def _render_invalid_link(request, message, status=410):
    return render(
        request,
        "customer_form.html",
        {"link_invalid": True, "error_message": message},
        status=status,
    )


def _mark_customer_paid(customer: Customer, cf_payment_id: str = ""):
    with transaction.atomic():
        customer.payment_status = "paid"
        if cf_payment_id:
            customer.cashfree_payment_id = cf_payment_id
        if not customer.paid_at:
            customer.paid_at = timezone.now()
        customer.save(
            update_fields=[
                "payment_status",
                "cashfree_payment_id",
                "paid_at",
            ]
        )
        if not customer.link.is_used:
            customer.link.is_used = True
            customer.link.save(update_fields=["is_used"])


def _start_cashfree_order(request, customer: Customer):
    order_id = customer.cashfree_order_id or f"DL_{customer.pk}_{secrets.token_hex(4)}"
    return_url = (
        request.build_absolute_uri(reverse("payment_return"))
        + f"?order_id={order_id}"
    )
    notify_url = request.build_absolute_uri(reverse("cashfree_webhook"))

    order_data = cashfree.create_order(
        order_id=order_id,
        amount=customer.final_amount,
        customer_id=f"cust_{customer.pk}",
        customer_name=customer.full_name or customer.application_number,
        customer_phone=customer.phone,
        return_url=return_url,
        notify_url=notify_url,
    )

    customer.cashfree_order_id = order_data.get("order_id", order_id)
    customer.cashfree_payment_session_id = order_data["payment_session_id"]
    customer.save(
        update_fields=["cashfree_order_id", "cashfree_payment_session_id"]
    )
    return customer.cashfree_payment_session_id


@login_required
def dashboard(request):
    settings_obj = PortalSettings.get_solo()
    total_customers = Customer.objects.count()
    pending = Customer.objects.filter(payment_status="pending").count()
    paid_qs = Customer.objects.filter(payment_status__in=["paid", "verified"])
    paid = paid_qs.count()
    total_revenue = paid_qs.aggregate(total=Sum("final_amount"))["total"] or 0

    customers = Customer.objects.select_related("link").order_by("-created_at")[:10]
    latest_link = CustomerLink.objects.order_by("-created_at").first()
    generated_url = request.session.pop("last_generated_url", "")
    if not generated_url and latest_link:
        generated_url = request.build_absolute_uri(
            reverse("customer_form", args=[latest_link.token])
        )

    context = {
        "total_customers": total_customers,
        "submitted_count": total_customers,
        "pending": pending,
        "paid": paid,
        "total_revenue": total_revenue,
        "customers": customers,
        "settings_obj": settings_obj,
        "generated_url": generated_url,
        "cashfree_configured": cashfree.is_configured(),
        "cashfree_env": settings.CASHFREE_ENV,
    }
    return render(request, "dashboard.html", context)


@login_required
def create_link(request):
    settings_obj = PortalSettings.get_solo()

    if request.method == "POST":
        raw_discount = request.POST.get(
            "discount", settings_obj.default_discount_percent
        )
        try:
            discount = int(raw_discount)
            if not 0 <= discount <= 100:
                raise ValueError
        except (ValueError, TypeError):
            messages.error(request, "Discount must be a percentage between 0 and 100.")
            if request.POST.get("source") == "dashboard":
                return redirect("dashboard")
            return redirect("create_link")

        link = CustomerLink.objects.create(
            base_price=settings_obj.default_price,
            discount=discount,
            expires_at=timezone.now() + timedelta(days=2),
        )

        customer_url = request.build_absolute_uri(
            reverse("customer_form", args=[link.token])
        )

        if request.POST.get("source") == "dashboard":
            request.session["last_generated_url"] = customer_url
            messages.success(request, "New customer link generated.")
            return redirect("dashboard")

        _, _, discount_amount, final_amount = link.calculate_amounts()
        return render(
            request,
            "link_created.html",
            {
                "customer_url": customer_url,
                "link": link,
                "discount_amount": discount_amount,
                "final_amount": final_amount,
            },
        )

    return render(
        request,
        "create_link.html",
        {"settings_obj": settings_obj},
    )


@login_required
@require_POST
def quick_settings_update(request):
    settings_obj = PortalSettings.get_solo()
    try:
        default_price = int(request.POST.get("default_price", settings_obj.default_price))
        discount_pct = int(
            request.POST.get(
                "default_discount_percent", settings_obj.default_discount_percent
            )
        )
        if default_price < 1 or not 0 <= discount_pct <= 100:
            raise ValueError
    except (ValueError, TypeError):
        messages.error(
            request,
            "Invalid settings: price must be >= ₹1 and discount must be 0–100%.",
        )
        return redirect("dashboard")

    settings_obj.default_price = default_price
    settings_obj.default_discount_percent = discount_pct
    settings_obj.save()
    messages.success(request, "Quick settings updated.")
    return redirect("dashboard")


@login_required
def customers_list(request):
    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()

    customers = Customer.objects.select_related("link").order_by("-created_at")
    if query:
        customers = customers.filter(
            Q(full_name__icontains=query)
            | Q(application_number__icontains=query)
            | Q(phone__icontains=query)
            | Q(state__icontains=query)
        )
    if status_filter in {"pending", "paid", "verified"}:
        customers = customers.filter(payment_status=status_filter)

    return render(
        request,
        "submissions.html",
        {
            "customers": customers,
            "query": query,
            "status_filter": status_filter,
        },
    )


@login_required
def customer_detail(request, pk):
    customer = get_object_or_404(Customer.objects.select_related("link"), pk=pk)

    if request.method == "POST":
        new_status = request.POST.get("payment_status", "")
        if new_status in {"pending", "paid", "verified"}:
            customer.payment_status = new_status
            if new_status in {"paid", "verified"} and not customer.paid_at:
                customer.paid_at = timezone.now()
                customer.link.is_used = True
                customer.link.save(update_fields=["is_used"])
            customer.save()
            messages.success(request, "Customer payment status updated.")
        return redirect("customer_detail", pk=customer.pk)

    return render(request, "customer_detail.html", {"customer": customer})


@login_required
def payments_list(request):
    customers = Customer.objects.order_by("-created_at")
    paid_qs = Customer.objects.filter(payment_status__in=["paid", "verified"])
    total_revenue = paid_qs.aggregate(total=Sum("final_amount"))["total"] or 0
    return render(
        request,
        "payments.html",
        {
            "customers": customers,
            "total_revenue": total_revenue,
            "paid_count": paid_qs.count(),
            "pending_count": Customer.objects.filter(payment_status="pending").count(),
        },
    )


@login_required
def portal_settings_view(request):
    settings_obj = PortalSettings.get_solo()
    if request.method == "POST":
        form = PortalSettingsForm(request.POST, request.FILES, instance=settings_obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Portal settings saved.")
            return redirect("portal_settings")
    else:
        form = PortalSettingsForm(instance=settings_obj)

    return render(
        request,
        "settings.html",
        {
            "form": form,
            "settings_obj": settings_obj,
            "cashfree_configured": cashfree.is_configured(),
            "cashfree_env": settings.CASHFREE_ENV,
        },
    )


def customer_form(request, token):
    """Render and process customer registration + Cashfree embedded checkout."""
    link = get_object_or_404(CustomerLink, token=token)
    settings_obj = PortalSettings.get_solo()
    existing_customer = Customer.objects.filter(link=link).first()

    if existing_customer:
        if existing_customer.payment_status in {"paid", "verified"}:
            return render(
                request,
                "customer_form.html",
                {"submitted": True, "customer": existing_customer},
            )
        if link.expires_at <= timezone.now():
            return _render_invalid_link(request, "This link has expired.")

        payment_error = ""
        session_id = existing_customer.cashfree_payment_session_id
        if not session_id:
            try:
                session_id = _start_cashfree_order(request, existing_customer)
            except cashfree.CashfreeError as exc:
                payment_error = str(exc)

        return render(
            request,
            "customer_form.html",
            {
                "checkout_ready": True,
                "customer": existing_customer,
                "payment_session_id": session_id,
                "cashfree_env": settings.CASHFREE_ENV,
                "payment_error": payment_error,
                "settings_obj": settings_obj,
            },
        )

    if link.is_used:
        return _render_invalid_link(request, "This link has already been used.")

    if link.expires_at <= timezone.now():
        return _render_invalid_link(request, "This link has expired.")

    base_price, discount_pct, discount_amount, final_amount = link.calculate_amounts()

    if request.method == "POST":
        form = CustomerForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                locked_link = CustomerLink.objects.select_for_update().get(pk=link.pk)
                if locked_link.is_used or locked_link.expires_at <= timezone.now():
                    return _render_invalid_link(
                        request,
                        "This link is no longer valid or has already been used.",
                    )

                customer = form.save(commit=False)
                customer.base_price = base_price
                customer.discount = discount_pct
                customer.discount_amount = discount_amount
                customer.final_amount = final_amount
                customer.link = locked_link

                if final_amount == 0:
                    customer.payment_status = "verified"
                    customer.paid_at = timezone.now()
                    customer.save()
                    locked_link.is_used = True
                    locked_link.save(update_fields=["is_used"])
                    return render(
                        request,
                        "customer_form.html",
                        {"submitted": True, "customer": customer},
                    )

                customer.payment_status = "pending"
                customer.save()

            payment_error = ""
            session_id = ""
            try:
                session_id = _start_cashfree_order(request, customer)
            except cashfree.CashfreeError as exc:
                payment_error = str(exc)

            return render(
                request,
                "customer_form.html",
                {
                    "checkout_ready": True,
                    "customer": customer,
                    "payment_session_id": session_id,
                    "cashfree_env": settings.CASHFREE_ENV,
                    "payment_error": payment_error,
                    "settings_obj": settings_obj,
                },
            )
    else:
        form = CustomerForm()

    return render(
        request,
        "customer_form.html",
        {
            "form": form,
            "link": link,
            "base_price": base_price,
            "discount": discount_pct,
            "discount_amount": discount_amount,
            "final_amount": final_amount,
            "settings_obj": settings_obj,
        },
    )


def payment_return(request):
    """Handle redirect back from Cashfree checkout and verify order status server-side."""
    order_id = request.GET.get("order_id", "").strip()
    if not order_id:
        return HttpResponseBadRequest("Missing order_id.")

    customer = get_object_or_404(
        Customer.objects.select_related("link"), cashfree_order_id=order_id
    )
    settings_obj = PortalSettings.get_solo()

    if customer.payment_status in {"paid", "verified"}:
        return render(
            request,
            "customer_form.html",
            {"submitted": True, "customer": customer},
        )

    payment_error = ""
    try:
        order_data = cashfree.fetch_order(order_id)
        if order_data.get("order_status") == "PAID":
            _mark_customer_paid(customer)
            return render(
                request,
                "customer_form.html",
                {"submitted": True, "customer": customer},
            )
        payment_error = (
            f"Payment status is '{order_data.get('order_status', 'ACTIVE')}'. "
            "Please complete your payment below."
        )
    except cashfree.CashfreeError as exc:
        payment_error = str(exc)

    return render(
        request,
        "customer_form.html",
        {
            "checkout_ready": True,
            "customer": customer,
            "payment_session_id": customer.cashfree_payment_session_id,
            "cashfree_env": settings.CASHFREE_ENV,
            "payment_error": payment_error,
            "settings_obj": settings_obj,
        },
    )


@csrf_exempt
@require_POST
def cashfree_webhook(request):
    """Verify and process asynchronous Cashfree payment webhooks."""
    signature = request.headers.get("x-webhook-signature")
    timestamp = request.headers.get("x-webhook-timestamp")
    raw_body = request.body

    if not cashfree.verify_webhook_signature(signature, timestamp, raw_body):
        return HttpResponse("Invalid signature", status=400)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    data = payload.get("data", {})
    order_id = data.get("order", {}).get("order_id")
    payment_info = data.get("payment", {})
    payment_status = payment_info.get("payment_status")
    cf_payment_id = str(payment_info.get("cf_payment_id", ""))
    event_type = payload.get("type", "")

    if order_id and (
        payment_status == "SUCCESS" or event_type == "PAYMENT_SUCCESS_WEBHOOK"
    ):
        customer = Customer.objects.select_related("link").filter(
            cashfree_order_id=order_id
        ).first()
        if customer:
            _mark_customer_paid(customer, cf_payment_id=cf_payment_id)

    return JsonResponse({"status": "ok"})



def payment_return(request):
    """Handle redirect back from Cashfree checkout and verify order status server-side."""
    order_id = request.GET.get("order_id", "").strip()
    if not order_id:
        return HttpResponseBadRequest("Missing order_id.")

    customer = get_object_or_404(
        Customer.objects.select_related("link"), cashfree_order_id=order_id
    )
    settings_obj = PortalSettings.get_solo()

    if customer.payment_status in {"paid", "verified"}:
        return render(
            request,
            "customer_form.html",
            {"submitted": True, "customer": customer},
        )

    payment_error = ""
    try:
        order_data = cashfree.fetch_order(order_id)
        if order_data.get("order_status") == "PAID":
            _mark_customer_paid(customer)
            return render(
                request,
                "customer_form.html",
                {"submitted": True, "customer": customer},
            )
        payment_error = (
            f"Payment status is '{order_data.get('order_status', 'ACTIVE')}'. "
            "Please complete your payment below."
        )
    except cashfree.CashfreeError as exc:
        payment_error = str(exc)

    return render(
        request,
        "customer_form.html",
        {
            "checkout_ready": True,
            "customer": customer,
            "payment_session_id": customer.cashfree_payment_session_id,
            "cashfree_env": settings.CASHFREE_ENV,
            "payment_error": payment_error,
            "settings_obj": settings_obj,
        },
    )


@csrf_exempt
@require_POST
def cashfree_webhook(request):
    """Verify and process asynchronous Cashfree payment webhooks."""
    signature = request.headers.get("x-webhook-signature")
    timestamp = request.headers.get("x-webhook-timestamp")
    raw_body = request.body

    if not cashfree.verify_webhook_signature(signature, timestamp, raw_body):
        return HttpResponse("Invalid signature", status=400)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    data = payload.get("data", {})
    order_id = data.get("order", {}).get("order_id")
    payment_info = data.get("payment", {})
    payment_status = payment_info.get("payment_status")
    cf_payment_id = str(payment_info.get("cf_payment_id", ""))
    event_type = payload.get("type", "")

    if order_id and (
        payment_status == "SUCCESS" or event_type == "PAYMENT_SUCCESS_WEBHOOK"
    ):
        customer = Customer.objects.select_related("link").filter(
            cashfree_order_id=order_id
        ).first()
        if customer:
            _mark_customer_paid(customer, cf_payment_id=cf_payment_id)

    return JsonResponse({"status": "ok"})