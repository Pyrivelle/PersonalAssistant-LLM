"""Parse explicit memory phrases without initializing a storage backend."""


def memory_command(text: str) -> tuple[str, str] | None:
    value = text.strip().rstrip("。！？!?")
    for prefix in ("请记住", "记住"):
        if value.startswith(prefix) and len(value) > len(prefix):
            return "remember", value[len(prefix):].strip("，, ：:")
    for prefix in ("请忘记我", "忘记我", "请忘记关于我", "忘记关于我"):
        if value.startswith(prefix) and len(value) > len(prefix):
            return "forget", "我" + value[len(prefix):].strip("，, ：:")
    return None
