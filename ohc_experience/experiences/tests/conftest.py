"""The engine's tests run against the sample experience, registered here.

Nothing outside the tests registers it, so the shipped registry stays empty
until a real app registers a definition of its own.
"""

from ohc_experience.experiences.tests import sample

sample.register()
