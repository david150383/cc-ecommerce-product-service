from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db import session_scope


def get_session(request: Request) -> Iterator[Session]:
    yield from session_scope(request.app.state.session_factory)


DbSession = Annotated[Session, Depends(get_session)]
