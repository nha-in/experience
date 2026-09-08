from .registry import get_program


def experience_program(request):
    return {"experience_program": get_program()}
