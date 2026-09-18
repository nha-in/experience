import string

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class LetterNumberAndSpecialCharacterValidator:
    def validate(self, password, user=None):
        has_letter = any(character in string.ascii_letters for character in password)
        has_number = any(character in string.digits for character in password)
        has_special_character = any(
            character in string.punctuation for character in password
        )
        if not (has_letter and has_number and has_special_character):
            raise ValidationError(
                _(
                    "This password must contain at least one letter, one number "
                    "and one special character.",
                ),
                code="password_missing_letter_number_or_special_character",
            )

    def get_help_text(self):
        return _(
            "Your password must contain at least one letter, one number and one "
            "special character.",
        )
