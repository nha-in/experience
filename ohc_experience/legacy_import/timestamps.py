"""Writing legacy dates into fields Django normally stamps itself."""

from contextlib import contextmanager

from django.apps import apps


def _automatic_fields():
    found = {}
    for model in apps.get_models():
        names = [
            field.name
            for field in model._meta.concrete_fields  # noqa: SLF001
            if getattr(field, "auto_now", False)
            or getattr(field, "auto_now_add", False)
        ]
        if names:
            found[model] = names
    return found


class HistoricalTimestamps:
    def __init__(self):
        self.fields = _automatic_fields()

    @contextmanager
    def enabled(self):
        changed = []
        for model, names in self.fields.items():
            for name in names:
                field = model._meta.get_field(name)  # noqa: SLF001
                changed.append((field, field.auto_now, field.auto_now_add))
                field.auto_now = False
                field.auto_now_add = False
        try:
            yield
        finally:
            for field, auto_now, auto_now_add in changed:
                field.auto_now = auto_now
                field.auto_now_add = auto_now_add

    def create(self, model, when, **values):
        for name in self.fields.get(model, ()):
            values.setdefault(name, when)
        return model.objects.create(**values)
