"""Short-lived routing lease for the conversation used by a live voice socket."""
import uuid

from redis import RedisError

from app.services import desktop_preview

TTL_SECONDS = 75


def _key(user_id: uuid.UUID) -> str:
    return f"chat:voice-target:{user_id}"


def claim(user_id: uuid.UUID, socket_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    try:
        if (client := desktop_preview.redis_client()) is not None:
            client.set(_key(user_id), f"{socket_id}:{conversation_id}", ex=TTL_SECONDS)
    except RedisError:
        return


def refresh(user_id: uuid.UUID, socket_id: uuid.UUID, active: bool) -> None:
    # An older socket can neither renew nor clear a newer socket's lease.
    try:
        if (client := desktop_preview.redis_client()) is not None:
            client.eval("""
                local target = redis.call('GET', KEYS[1])
                if target and string.sub(target, 1, string.len(ARGV[1])) == ARGV[1] then
                    if ARGV[2] == '1' then
                        return redis.call('EXPIRE', KEYS[1], ARGV[3])
                    end
                    return redis.call('DEL', KEYS[1])
                end
                return 0
            """, 1, _key(user_id), f"{socket_id}:", int(active), TTL_SECONDS)
    except RedisError:
        return


def current(user_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID] | None:
    try:
        client = desktop_preview.redis_client()
        target = client.get(_key(user_id)) if client is not None else None
        if target:
            socket_id, conversation_id = target.decode().split(":")
            return uuid.UUID(socket_id), uuid.UUID(conversation_id)
    except (RedisError, ValueError):
        return None
    return None
