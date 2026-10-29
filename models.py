from datetime import datetime
from decimal import Decimal
from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="customer")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (CheckConstraint("role IN ('customer', 'staff', 'admin')", name="ck_user_role"),)


class AuthSession(Base):
    """Revocable opaque session tokens, stored only as SHA-256 digests."""
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    phone: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    license_number: Mapped[str] = mapped_column(String(50), default="", nullable=False)


class VehicleCategory(Base):
    __tablename__ = "vehicle_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)


class Branch(Base):
    __tablename__ = "branches"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    address: Mapped[str] = mapped_column(String(255), nullable=False)


class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("vehicle_categories.id"), nullable=False, index=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), nullable=False, index=True)
    brand: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    registration: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    fuel_type: Mapped[str] = mapped_column(String(24), nullable=False)
    transmission: Mapped[str] = mapped_column(String(24), nullable=False)
    seats: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_rate: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="Available", index=True)
    image_url: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        CheckConstraint("daily_rate > 0", name="ck_vehicle_rate"),
        CheckConstraint("seats > 0 AND seats <= 60", name="ck_vehicle_seats"),
        CheckConstraint("status IN ('Available', 'Rented', 'Under Maintenance')", name="ck_vehicle_status"),
    )


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False, index=True)
    pickup_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    return_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    pickup_location: Mapped[str] = mapped_column(String(180), nullable=False)
    return_location: Mapped[str] = mapped_column(String(180), nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_rate: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    rental_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    service_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    tax: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    late_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    extra_charge: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="Pending", nullable=False, index=True)
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        CheckConstraint("return_at > pickup_at", name="ck_booking_dates"),
        CheckConstraint("days > 0 AND total >= 0", name="ck_booking_amount"),
        CheckConstraint("status IN ('Pending', 'Confirmed', 'Active', 'Completed', 'Cancelled', 'Rejected')", name="ck_booking_status"),
        Index("ix_booking_vehicle_period", "vehicle_id", "pickup_at", "return_at", "status"),
    )


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(30), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="Pending", nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(30), default="", nullable=False)
    reference: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    gateway_order_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    refund_reference: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_payment_amount"),
        CheckConstraint("status IN ('Pending', 'Paid', 'Failed', 'Refunded')", name="ck_payment_status"),
    )


class VehicleReturn(Base):
    __tablename__ = "vehicle_returns"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), nullable=False, unique=True)
    actual_return_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    vehicle_condition: Mapped[str] = mapped_column(String(100), nullable=False)
    late_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    additional_charges: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Maintenance(Base):
    __tablename__ = "maintenance"
    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cost: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="Scheduled", nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (
        CheckConstraint("cost >= 0", name="ck_maintenance_cost"),
        CheckConstraint("status IN ('Scheduled', 'In progress', 'Completed')", name="ck_maintenance_status"),
        CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ck_maintenance_dates"),
    )


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False, index=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(String(600), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (CheckConstraint("rating BETWEEN 1 AND 5", name="ck_review_rating"),)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ContactMessage(Base):
    __tablename__ = "contact_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    subject: Mapped[str] = mapped_column(String(150), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


# Transactions and row locks are required for overlap prevention in MySQL.
for _table in Base.metadata.tables.values():
    _table.dialect_options["mysql"]["engine"] = "InnoDB"
    _table.dialect_options["mysql"]["charset"] = "utf8mb4"
