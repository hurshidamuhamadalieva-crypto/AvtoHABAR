import asyncio
import logging
from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Boolean,
    DateTime, Float, ForeignKey, JSON, select, update, delete
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship

from config import config

logger = logging.getLogger(__name__)

engine = create_async_engine(config.DATABASE_URL, echo=False, future=True)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(128), nullable=True)
    full_name = Column(String(256), nullable=True)
    is_admin = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    phone_number = Column(String(32), nullable=True)
    session_string = Column(Text, nullable=True)

    subscriptions = relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="user", cascade="all, delete-orphan")
    advertisements = relationship("Advertisement", back_populates="user", cascade="all, delete-orphan")
    groups = relationship("Group", back_populates="user", cascade="all, delete-orphan")
    send_stats = relationship("SendStat", back_populates="user", cascade="all, delete-orphan")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    plan_key = Column(String(32), nullable=False)
    plan_name = Column(String(64), nullable=False)
    price = Column(Integer, nullable=False)
    days = Column(Integer, nullable=False)
    activated_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=False)
    reminder_sent = Column(Boolean, default=False)

    user = relationship("User", back_populates="subscriptions")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    plan_key = Column(String(32), nullable=False)
    amount = Column(Integer, nullable=False)
    method = Column(String(32), nullable=False)  # admin / click / card
    status = Column(String(32), default="pending")  # pending / approved / rejected
    screenshot_file_id = Column(String(256), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    admin_note = Column(Text, nullable=True)

    user = relationship("User", back_populates="payments")


class Advertisement(Base):
    __tablename__ = "advertisements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    user = relationship("User", back_populates="advertisements")


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    group_id = Column(BigInteger, nullable=False)
    group_title = Column(String(256), nullable=True)
    group_username = Column(String(128), nullable=True)
    folder_name = Column(String(128), nullable=True)
    is_active = Column(Boolean, default=True)
    added_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="groups")


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    interval_minutes = Column(Integer, default=10)
    is_sending = Column(Boolean, default=False)
    sending_paused = Column(Boolean, default=False)
    selected_folder = Column(String(128), nullable=True)
    last_sent_at = Column(DateTime, nullable=True)
    messages_sent = Column(Integer, default=0)


class SendStat(Base):
    __tablename__ = "send_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    group_id = Column(BigInteger, nullable=False)
    group_title = Column(String(256), nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow)
    success = Column(Boolean, default=True)
    error_msg = Column(Text, nullable=True)

    user = relationship("User", back_populates="send_stats")


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized successfully.")


# ─── DB Helper Functions ──────────────────────────────────────────────────────

async def get_user(telegram_id: int) -> Optional[User]:
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()


async def get_user_by_db_id(db_id: int) -> Optional[User]:
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.id == db_id))
        return result.scalar_one_or_none()


async def create_user(telegram_id: int, username: str = None, full_name: str = None) -> User:
    async with async_session_maker() as session:
        user = User(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name,
            is_admin=telegram_id in config.ADMIN_IDS,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def get_or_create_user(telegram_id: int, username: str = None, full_name: str = None) -> User:
    user = await get_user(telegram_id)
    if not user:
        user = await create_user(telegram_id, username, full_name)
    return user


async def update_user(telegram_id: int, **kwargs):
    async with async_session_maker() as session:
        await session.execute(update(User).where(User.telegram_id == telegram_id).values(**kwargs))
        await session.commit()


async def get_all_users() -> List[User]:
    async with async_session_maker() as session:
        result = await session.execute(select(User).order_by(User.created_at.desc()))
        return result.scalars().all()


async def get_active_subscription(user_db_id: int) -> Optional[Subscription]:
    async with async_session_maker() as session:
        result = await session.execute(
            select(Subscription)
            .where(Subscription.user_id == user_db_id, Subscription.is_active == True)
            .order_by(Subscription.expires_at.desc())
        )
        return result.scalar_one_or_none()


async def create_payment(user_db_id: int, plan_key: str, amount: int, method: str) -> Payment:
    async with async_session_maker() as session:
        payment = Payment(user_id=user_db_id, plan_key=plan_key, amount=amount, method=method)
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        return payment


async def get_payment(payment_id: int) -> Optional[Payment]:
    async with async_session_maker() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        return result.scalar_one_or_none()


async def update_payment(payment_id: int, **kwargs):
    async with async_session_maker() as session:
        await session.execute(update(Payment).where(Payment.id == payment_id).values(**kwargs))
        await session.commit()


async def get_user_payments(user_db_id: int) -> List[Payment]:
    async with async_session_maker() as session:
        result = await session.execute(
            select(Payment).where(Payment.user_id == user_db_id).order_by(Payment.created_at.desc())
        )
        return result.scalars().all()


async def activate_subscription(user_db_id: int, plan_key: str, payment_id: int):
    from datetime import timedelta
    plan = config.PLANS[plan_key]
    now = datetime.utcnow()
    expires = now + timedelta(days=plan["days"])
    async with async_session_maker() as session:
        # Deactivate old subs
        await session.execute(
            update(Subscription).where(Subscription.user_id == user_db_id).values(is_active=False)
        )
        sub = Subscription(
            user_id=user_db_id,
            plan_key=plan_key,
            plan_name=plan["name"],
            price=plan["price"],
            days=plan["days"],
            activated_at=now,
            expires_at=expires,
            is_active=True,
        )
        session.add(sub)
        # Mark user active
        await session.execute(update(User).where(User.id == user_db_id).values(is_active=True))
        await session.commit()


async def get_active_advertisement(user_db_id: int) -> Optional[Advertisement]:
    async with async_session_maker() as session:
        result = await session.execute(
            select(Advertisement)
            .where(Advertisement.user_id == user_db_id, Advertisement.is_active == True)
            .order_by(Advertisement.created_at.desc())
        )
        return result.scalar_one_or_none()


async def save_advertisement(user_db_id: int, text: str):
    async with async_session_maker() as session:
        # Deactivate old ads
        await session.execute(
            update(Advertisement).where(Advertisement.user_id == user_db_id).values(is_active=False)
        )
        ad = Advertisement(user_id=user_db_id, text=text, is_active=True)
        session.add(ad)
        await session.commit()


async def clear_advertisements(user_db_id: int):
    async with async_session_maker() as session:
        await session.execute(delete(Advertisement).where(Advertisement.user_id == user_db_id))
        await session.commit()


async def get_user_groups(user_db_id: int) -> List[Group]:
    async with async_session_maker() as session:
        result = await session.execute(
            select(Group).where(Group.user_id == user_db_id, Group.is_active == True)
        )
        return result.scalars().all()


async def save_groups(user_db_id: int, groups: list, folder_name: str):
    async with async_session_maker() as session:
        await session.execute(delete(Group).where(Group.user_id == user_db_id))
        for g in groups:
            grp = Group(
                user_id=user_db_id,
                group_id=g["id"],
                group_title=g.get("title", ""),
                group_username=g.get("username", ""),
                folder_name=folder_name,
                is_active=True,
            )
            session.add(grp)
        await session.commit()


async def get_or_create_settings(user_db_id: int) -> UserSettings:
    async with async_session_maker() as session:
        result = await session.execute(select(UserSettings).where(UserSettings.user_id == user_db_id))
        settings = result.scalar_one_or_none()
        if not settings:
            settings = UserSettings(user_id=user_db_id)
            session.add(settings)
            await session.commit()
            await session.refresh(settings)
        return settings


async def update_settings(user_db_id: int, **kwargs):
    async with async_session_maker() as session:
        result = await session.execute(select(UserSettings).where(UserSettings.user_id == user_db_id))
        settings = result.scalar_one_or_none()
        if not settings:
            settings = UserSettings(user_id=user_db_id, **kwargs)
            session.add(settings)
        else:
            for k, v in kwargs.items():
                setattr(settings, k, v)
        await session.commit()


async def add_send_stat(user_db_id: int, group_id: int, group_title: str, success: bool, error_msg: str = None):
    async with async_session_maker() as session:
        stat = SendStat(
            user_id=user_db_id, group_id=group_id,
            group_title=group_title, success=success, error_msg=error_msg
        )
        session.add(stat)
        if success:
            await session.execute(
                update(UserSettings)
                .where(UserSettings.user_id == user_db_id)
                .values(messages_sent=UserSettings.messages_sent + 1, last_sent_at=datetime.utcnow())
            )
        await session.commit()


async def get_expiring_subscriptions():
    """Get subscriptions expiring tomorrow (for reminder)."""
    from datetime import timedelta
    now = datetime.utcnow()
    tomorrow_start = now.replace(hour=0, minute=0, second=0) + timedelta(days=1)
    tomorrow_end = tomorrow_start + timedelta(days=1)
    async with async_session_maker() as session:
        result = await session.execute(
            select(Subscription)
            .where(
                Subscription.is_active == True,
                Subscription.expires_at >= tomorrow_start,
                Subscription.expires_at < tomorrow_end,
                Subscription.reminder_sent == False,
            )
        )
        return result.scalars().all()


async def mark_reminder_sent(sub_id: int):
    async with async_session_maker() as session:
        await session.execute(update(Subscription).where(Subscription.id == sub_id).values(reminder_sent=True))
        await session.commit()


async def get_expired_subscriptions():
    now = datetime.utcnow()
    async with async_session_maker() as session:
        result = await session.execute(
            select(Subscription)
            .where(Subscription.is_active == True, Subscription.expires_at < now)
        )
        return result.scalars().all()


async def deactivate_subscription(sub_id: int, user_db_id: int):
    async with async_session_maker() as session:
        await session.execute(update(Subscription).where(Subscription.id == sub_id).values(is_active=False))
        await session.execute(update(User).where(User.id == user_db_id).values(is_active=False))
        await session.commit()


async def get_total_stats():
    async with async_session_maker() as session:
        from sqlalchemy import func
        users_count = (await session.execute(select(func.count(User.id)))).scalar()
        active_count = (await session.execute(select(func.count(User.id)).where(User.is_active == True))).scalar()
        total_payments = (await session.execute(
            select(func.sum(Payment.amount)).where(Payment.status == "approved")
        )).scalar() or 0
        messages_sent = (await session.execute(select(func.sum(UserSettings.messages_sent)))).scalar() or 0
        return {
            "total_users": users_count,
            "active_users": active_count,
            "total_revenue": total_payments,
            "messages_sent": messages_sent,
        }
