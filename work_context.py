"""Task-local billing intent; extra work never borrows scheduled allowances."""
from contextlib import contextmanager
from contextvars import ContextVar

work_kind = ContextVar('search_work_kind', default='auxiliary')


@contextmanager
def spending_scope(kind: str):
    token = work_kind.set(kind)
    try:
        yield
    finally:
        work_kind.reset(token)
