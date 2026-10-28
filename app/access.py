from config import config


def is_admin(user):
    return bool(user and getattr(user, "telegram_id", None) in config.ADMIN_IDS)


def has_access(user):
    return bool(user and (is_admin(user) or (user.is_active and not user.is_banned)))
