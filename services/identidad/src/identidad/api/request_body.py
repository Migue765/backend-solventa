from typing import Any

from flask import request
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

FORM_MIMETYPE = "application/x-www-form-urlencoded"


def read_body(allow_form: bool = False) -> dict[str, Any]:
    if request.is_json:
        data: Any = request.get_json(silent=False)
    elif allow_form and request.mimetype == FORM_MIMETYPE:
        data = request.form.to_dict()
    else:
        raise UnsupportedMediaType()

    if not isinstance(data, dict):
        raise BadRequest()
    return data
