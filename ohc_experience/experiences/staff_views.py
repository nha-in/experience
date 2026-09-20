from django.contrib import messages
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from .staff_forms import StaffForm
from .staff_forms import staff_revision
from .staff_services import require_superadmin
from .staff_services import save_staff
from .staff_services import set_staff_active


@login_required
@never_cache
def staff_list(request):
    require_superadmin(request.user)
    staff = get_user_model().objects.filter(Q(is_nha_team=True) | Q(is_superuser=True))
    search = request.GET.get("q", "").strip()
    if search:
        staff = staff.filter(Q(name__icontains=search) | Q(email__icontains=search))
    status = request.GET.get("status", "active")
    if status not in {"active", "archived", "all"}:
        status = "active"
    counts = {
        "active": staff.filter(is_active=True).count(),
        "archived": staff.filter(is_active=False).count(),
        "all": staff.count(),
    }
    if status != "all":
        staff = staff.filter(is_active=status == "active")
    page = Paginator(
        staff.prefetch_related("experience_access").order_by("name", "email", "pk"),
        20,
    ).get_page(request.GET.get("page"))
    for member in page:
        member.staff_initials = "".join(
            part[0] for part in member.display_name.split()[:2]
        ).upper()
        member.portal_grants = [
            grant for grant in member.experience_access.all() if grant.can_read
        ]
    return render(
        request,
        "experiences/staff_list.html",
        {
            "nav": "staff",
            "page_title": "Staff",
            "page": page,
            "search": search,
            "status": status,
            "counts": counts,
        },
    )


def _account_history(pk, limit=10):
    """Admin log entries in the shape the activity feed reads."""
    if not pk:
        return []
    entries = LogEntry.objects.filter(
        content_type=ContentType.objects.get_for_model(get_user_model()),
        object_id=str(pk),
    ).select_related("user")[:limit]
    return [
        {
            "action": entry.get_change_message(),
            "actor": entry.user,
            "created_at": entry.action_time,
        }
        for entry in entries
    ]


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def staff_edit(request, pk=None):
    require_superadmin(request.user)
    user = (
        get_object_or_404(get_user_model(), pk=pk, is_nha_team=True, is_superuser=False)
        if pk
        else None
    )
    form = StaffForm(request.POST or None, user=user)
    if request.method == "POST":
        try:
            user, form = save_staff(request.user, request.POST, pk=pk)
            if form.is_valid():
                messages.success(
                    request,
                    "Staff account created."
                    if pk is None
                    else "Staff account updated.",
                )
                return redirect("experiences:staff-edit", pk=user.pk)
        except ValidationError as error:
            form.is_valid()
            form.add_error(None, error)
        except IntegrityError:
            form.is_valid()
            form.add_error(
                None,
                "The account could not be saved. "
                "Check the email and reload before trying again.",
            )
    history = _account_history(pk)
    return render(
        request,
        "experiences/staff_edit.html",
        {
            "nav": "staff",
            "page_title": user.display_name if user else "New staff member",
            "staff_member": user,
            "form": form,
            "history": history,
            "account_revision": staff_revision(user) if user else "",
            "pending_reviews": user.assigned_review_items.filter(
                status__in=["new", "in_review", "query_raised"],
            ).count()
            if user
            else 0,
        },
    )


@login_required
@never_cache
@require_POST
def staff_archive(request, pk):
    require_superadmin(request.user)
    get_object_or_404(get_user_model(), pk=pk, is_nha_team=True, is_superuser=False)
    intent = request.POST.get("intent")
    if intent not in {"archive", "restore"}:
        messages.error(request, "Choose archive or restore.")
    else:
        try:
            set_staff_active(
                request.user,
                pk,
                active=intent == "restore",
                revision=request.POST.get("revision", ""),
            )
            messages.success(
                request,
                "Staff account archived."
                if intent == "archive"
                else "Staff account restored.",
            )
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
    return redirect("experiences:staff-edit", pk=pk)
