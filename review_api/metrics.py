from collections import Counter

from fastapi import APIRouter

router = APIRouter(tags=["metrics"])

_counters: Counter[str] = Counter()


def inc(name: str, n: int = 1) -> None:
    _counters[name] += n


@router.get("/metrics")
def metrics() -> dict[str, int]:
    return dict(_counters)
