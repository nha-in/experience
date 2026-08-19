from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import F
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _


class EventQuerySet(models.QuerySet["Event"]):
    def published(self) -> EventQuerySet:
        return self.filter(published_at__isnull=False)

    def upcoming(self) -> EventQuerySet:
        """Published events that have not finished, soonest first."""
        now = timezone.now()
        return (
            self.published()
            .filter(Q(ends_at__gt=now) | Q(ends_at__isnull=True, starts_at__gt=now))
            .order_by("starts_at")
        )

    def past(self) -> EventQuerySet:
        now = timezone.now()
        return (
            self.published()
            .filter(Q(ends_at__lte=now) | Q(ends_at__isnull=True, starts_at__lte=now))
            .order_by("-starts_at")
        )


class Event(models.Model):
    """A partner event — office hours, an upgrade webinar, a certification AMA.

    Authored by the OHC team and visible to every vendor once published, so
    there is no per-organisation scoping here on purpose.
    """

    class Kind(models.TextChoices):
        OFFICE_HOURS = "office_hours", _("Office hours")
        WEBINAR = "webinar", _("Webinar")
        AMA = "ama", _("AMA")
        WORKSHOP = "workshop", _("Workshop")

    title = models.CharField(_("Title"), max_length=255)
    slug = models.SlugField(_("Slug"), max_length=255, unique=True)
    kind = models.CharField(
        _("Kind"),
        max_length=20,
        choices=Kind.choices,
        default=Kind.WEBINAR,
    )
    summary = models.CharField(
        _("Summary"),
        max_length=255,
        blank=True,
        help_text=_("One line shown under the title on the dashboard."),
    )
    description = models.TextField(_("Description"), blank=True)
    starts_at = models.DateTimeField(_("Starts at"))
    ends_at = models.DateTimeField(_("Ends at"), null=True, blank=True)
    location = models.CharField(
        _("Location"),
        max_length=255,
        blank=True,
        help_text=_("Leave blank for an online event."),
    )
    join_url = models.URLField(_("Join link"), blank=True)
    published_at = models.DateTimeField(_("Published at"), null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events_created",
        verbose_name=_("Created by"),
        limit_choices_to={"is_ohc_team": True},
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects: ClassVar[EventQuerySet] = EventQuerySet.as_manager()

    class Meta:
        verbose_name = _("Event")
        verbose_name_plural = _("Events")
        ordering = ["starts_at"]
        indexes = [models.Index(fields=["published_at", "starts_at"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(ends_at__gt=F("starts_at")),
                name="event_ends_after_it_starts",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs) -> None:
        if not self.slug:
            self.slug = self._build_unique_slug()
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("events:detail", kwargs={"slug": self.slug})

    def _build_unique_slug(self) -> str:
        base = slugify(self.title)[:200] or "event"
        candidate = base
        suffix = 2
        taken = Event.objects.exclude(pk=self.pk)
        while taken.filter(slug=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    @property
    def is_published(self) -> bool:
        return self.published_at is not None

    @property
    def is_past(self) -> bool:
        end = self.ends_at or self.starts_at
        return end <= timezone.now()

    @property
    def is_online(self) -> bool:
        return not self.location

    def publish(self) -> None:
        if self.published_at is None:
            self.published_at = timezone.now()

    def unpublish(self) -> None:
        self.published_at = None
