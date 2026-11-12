"""Cooperative cancellation for generation requests and polling waits."""
import time


class GenerationStopped(RuntimeError):
    pass


def check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise GenerationStopped("任务已停止，返回结果不会写入。")


def wait_or_cancel(seconds, cancel_event):
    if cancel_event is None:
        time.sleep(seconds)
    elif cancel_event.wait(seconds):
        check_cancel(cancel_event)
