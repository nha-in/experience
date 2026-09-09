import hashlib
import json

from allauth.account.models import EmailAddress
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from .models import AccessGrant
from .registry import registry


def staff_revision(user):
    data = {
        "catalog": [
            (program.key, [track.code for track in program.tracks])
            for program in registry.programs()
        ],
        "account": [
            user.pk,
            user.name,
            user.email,
            user.phone_number,
            user.password,
            user.is_active,
            user.is_staff,
            user.is_superuser,
            user.is_ohc_team,
        ],
        "grants": list(
            user.experience_access.order_by("program", "area", "category").values_list(
                "program",
                "area",
                "category",
                "can_read",
                "can_write",
                "can_approve",
            ),
        ),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class StaffForm(forms.Form):
    name = forms.CharField(label="Full name", max_length=255)
    email = forms.EmailField(label="Work email", max_length=254)
    phone_number = forms.CharField(label="Phone number", max_length=32, required=False)
    password1 = forms.CharField(
        label="Password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    revision = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user:
            self.initial.update(
                {key: getattr(user, key) for key in ("name", "email", "phone_number")},
            )
            self.initial["revision"] = staff_revision(user)
            self.fields["revision"].required = True
            self.fields["password1"].label = "New password"
        else:
            self.fields["password1"].required = True
            self.fields["password2"].required = True
        current = (
            {
                (grant.program, grant.area, grant.category): grant
                for grant in user.experience_access.all()
            }
            if user
            else {}
        )
        self.permission_groups = []
        self.permission_rows = []
        for program in registry.programs():
            categories = [
                ("*", "All categories", "Including future categories"),
                ("", "General / onboarding", "Organisation and product registration"),
            ]
            categories.extend(
                (track.code, track.code, track.name) for track in program.tracks
            )
            for area, label in AccessGrant.Area.choices:
                rows = []
                for category, category_label, description in categories:
                    key = (program.key, area, category)
                    existing = current.get(key)
                    cells = []
                    for action in ("read", "write", "approve"):
                        field = (
                            f"access_{program.key.encode().hex()}_{area}_"
                            f"{category.encode().hex()}_{action}"
                        )
                        self.fields[field] = forms.BooleanField(
                            required=False,
                            label=(
                                f"{program.short_name}: {category_label} / "
                                f"{label} / {action.title()}"
                            ),
                            initial=bool(
                                existing and getattr(existing, f"can_{action}"),
                            ),
                            widget=forms.CheckboxInput(
                                attrs={
                                    "class": "ui-checkbox",
                                    "data-permission-action": action,
                                },
                            ),
                        )
                        cells.append(self[field])
                    row = {
                        "key": key,
                        "label": category_label,
                        "description": description,
                        "cells": cells,
                    }
                    rows.append(row)
                    self.permission_rows.append(row)
                self.permission_groups.append(
                    {"program": program, "area": area, "label": label, "rows": rows},
                )

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        users = get_user_model().objects.filter(email__iexact=email)
        addresses = EmailAddress.objects.filter(email__iexact=email)
        if self.user:
            users = users.exclude(pk=self.user.pk)
            addresses = addresses.exclude(user=self.user)
        if users.exists() or addresses.exists():
            msg = "This email is already used by another account."
            raise forms.ValidationError(msg)
        return email

    def clean(self):
        data = super().clean()
        if any(
            key.startswith("access_") and key not in self.fields for key in self.data
        ):
            self.add_error(
                None,
                "The permission catalog changed. Reload before saving.",
            )
        password = data.get("password1")
        if password != data.get("password2"):
            self.add_error("password2", "The passwords do not match.")
        if password:
            candidate = get_user_model()(
                email=data.get("email", ""),
                name=data.get("name", ""),
            )
            try:
                validate_password(password, candidate)
            except forms.ValidationError as error:
                self.add_error("password1", error)
        for row in self.permission_rows:
            read, write, approve = row["cells"]
            if (data.get(write.name) or data.get(approve.name)) and not data.get(
                read.name,
            ):
                self.add_error(
                    read.name,
                    "Read is required for write or approve access.",
                )
        return data

    def grant_values(self):
        return [
            {
                "program": row["key"][0],
                "area": row["key"][1],
                "category": row["key"][2],
                **{
                    f"can_{action}": self.cleaned_data[cell.name]
                    for action, cell in zip(
                        ("read", "write", "approve"),
                        row["cells"],
                        strict=True,
                    )
                },
            }
            for row in self.permission_rows
            if self.cleaned_data[row["cells"][0].name]
        ]
