from access_log_db.errors import AppError
from access_log_db.messages import MSG


def validate_name(text: str, empty_code: str, length_code: str) -> str:
    value = text.strip()
    if not value:
        raise AppError(empty_code, MSG[empty_code])
    if len(value) > 100:
        raise AppError(length_code, MSG[length_code])
    return value
