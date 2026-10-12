import random
import time
def jittered_sleep(base: float, jitter_ratio: float = 0.3, *, is_cancelled=None) -> float:
    actual = base * (1 + random.uniform(-jitter_ratio, jitter_ratio))
    actual = max(0.1, actual)
    start_time = time.time()
    while time.time() - start_time < actual:
        if is_cancelled and is_cancelled():
            break
        sleep_time = min(0.5, max(0.0, actual - (time.time() - start_time)))
        if sleep_time <= 0:
            break
        time.sleep(sleep_time)
    return actual
