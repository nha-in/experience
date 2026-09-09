import json

from allauth.account.models import EmailAddress
from django.contrib.admin.models import ADDITION
from django.contrib.admin.models import CHANGE
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from ohc_experience.support.models import Ticket

from .models import AccessGrant
from .models import ReviewItem
from .registry import registry
from .staff_forms import StaffForm
from .staff_forms import staff_revision
from .workflows import assign_review


def require_superadmin(actor):
    if not (actor.is_authenticated and actor.is_active and actor.is_superuser):
        raise PermissionDenied


def editable_staff(actor, pk):
    require_superadmin(actor)
    user = get_user_model().objects.select_for_update().get(pk=pk)
    if not user.is_ohc_team or user.is_superuser or user.pk == actor.pk:
        raise PermissionDenied
    return user


def check_revision(user, value):
    if not value or not constant_time_compare(staff_revision(user), value):
        msg = "This account changed after you opened it. Reload before saving."
        raise ValidationError(msg)


def revoke_sessions(user):
    # The portal uses Django's database session backend. Delete rather than suspend
    # sessions so restoring an account cannot revive an archived login.
    keys = [
        session.pk
        for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator()
        if session.get_decoded().get("_auth_user_id") == str(user.pk)
    ]
    Session.objects.filter(pk__in=keys).delete()


def staff_log(actor, user, action, *, created=False, detail=None):
    LogEntry.objects.create(
        user=actor,
        content_type=ContentType.objects.get_for_model(user),
        object_id=str(user.pk),
        object_repr=user.display_name,
        action_flag=ADDITION if created else CHANGE,
        change_message=json.dumps(
            [{"changed": {"fields": [action]}}, {"portal": detail or {}}],
        ),
    )


@transaction.atomic
def save_staff(actor, data, *, pk=None):
    require_superadmin(actor)
    user = editable_staff(actor, pk) if pk else None
    form = StaffForm(data, user=user)
    if not form.is_valid():
        return user, form
    if user:
        check_revision(user, form.cleaned_data["revision"])
    before = (
        list(
            user.experience_access.values(
                "program", "area", "category", "can_read", "can_write", "can_approve",
            ),
        )
        if user
        else []
    )
    created = user is None
    user = user or get_user_model()(
        is_ohc_team=True, is_staff=False, is_superuser=False, is_active=True,
    )
    old_email = user.email
    for field in ("name", "email", "phone_number"):
        setattr(user, field, form.cleaned_data[field])
    password = form.cleaned_data["password1"]
    if password:
        user.set_password(password)
    user.save()
    if not created and (password or old_email != user.email):
        revoke_sessions(user)
    # Provisioned staff addresses are asserted by the superadmin, not public signup.
    EmailAddress.objects.filter(user=user).exclude(email=user.email).delete()
    EmailAddress.objects.update_or_create(
        user=user, email=user.email, defaults={"primary": True, "verified": True},
    )
    programs = [program.key for program in registry.programs()]
    user.experience_access.filter(program__in=programs).delete()
    grants = form.grant_values()
    AccessGrant.objects.bulk_create(
        [AccessGrant(user=user, **values) for values in grants],
    )
    staff_log(
        actor,
        user,
        "Staff account created" if created else "Staff account updated",
        created=created,
        detail={
            "permissions_before": before,
            "permissions_after": grants,
            "password_changed": bool(password),
        },
    )
    return user, form


@transaction.atomic
def set_staff_active(actor, pk, *, active, revision):
    user = editable_staff(actor, pk)
    check_revision(user, revision)
    if user.is_active == active:
        return user
    user.is_active = active
    user.save(update_fields=["is_active"])
    if not active:
        revoke_sessions(user)
        for item in ReviewItem.objects.filter(
            assignee=user, status__in=["new", "in_review", "query_raised"],
        ):
            assign_review(item, actor, None)
        Ticket.objects.filter(
            assignee=user, status__in=["open", "awaiting_vendor"],
        ).update(assignee=None)
    staff_log(
        actor, user, "Staff account restored" if active else "Staff account archived",
    )
    return user
