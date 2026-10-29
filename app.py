"""Roam REST API. SQLite is the default demo DB; DATABASE_URL enables MySQL."""
import csv
import hashlib
import hmac
import io
import math
import os
import re
import secrets
import smtplib
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from urllib.parse import quote as urlquote

import requests
from dotenv import load_dotenv
from flask import Flask, abort, g, jsonify, request, send_file, send_from_directory, session
from flask.sessions import SecureCookieSessionInterface
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from PIL import Image, ImageOps, UnidentifiedImageError
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from .db import create_database, default_database_url, local_now
from .models import (User, AuthSession, Customer, VehicleCategory, Branch, Vehicle, Booking, Payment,
                     VehicleReturn, Maintenance, Review, Notification, ContactMessage)
from .seed import seed_data

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
ACTIVE_BOOKING = ('Pending', 'Confirmed', 'Active')
MONEY = Decimal('0.01')
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def money(x):
    return Decimal(str(x)).quantize(MONEY, rounding=ROUND_HALF_UP)


def parse_money(value, label, minimum=Decimal('0'), maximum=Decimal('10000000')):
    try:
        value = money(value)
        if not value.is_finite() or value < minimum or value > maximum:
            raise ValueError()
        return value
    except (InvalidOperation, ValueError, TypeError):
        abort(400, description=f"Enter a valid {label}.")


def parse_date(value, label):
    try:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError()
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            # Accept UTC offsets but normalize to the configured rental timezone.
            from zoneinfo import ZoneInfo
            parsed = parsed.astimezone(ZoneInfo(os.environ.get('RENTAL_TIMEZONE','Asia/Kolkata'))).replace(tzinfo=None)
        return parsed.replace(microsecond=0)
    except (ValueError, TypeError, OverflowError):
        abort(400, description=f"Enter a valid {label}.")


def text(value, label, max_length=180, minimum=1):
    if not isinstance(value, str):
        abort(400, description=f"{label} is required.")
    value = value.strip()
    if len(value) < minimum or len(value) > max_length:
        abort(400, description=f"{label} must be between {minimum} and {max_length} characters.")
    return value


def email_address(value):
    value = text(value, 'Email', 254).lower()
    if not EMAIL_RE.fullmatch(value):
        abort(400, description="Enter a valid email address.")
    return value


def password_value(value):
    if not isinstance(value, str) or len(value) < 8 or len(value) > 128:
        abort(400, description="Password must be between 8 and 128 characters.")
    return value


def json_data():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        abort(400, description="A JSON object is required.")
    return value


def serialize_user(db, u):
    c = db.scalar(select(Customer).where(Customer.user_id == u.id))
    return dict(id=u.id, name=u.name, email=u.email, role=u.role, active=u.active,
                phone=c.phone if c else '', license_number=c.license_number if c else '',
                created_at=u.created_at.isoformat())


def serialize_vehicle(db, v):
    category = db.get(VehicleCategory, v.category_id)
    branch = db.get(Branch, v.branch_id)
    avg, count = db.execute(select(func.avg(Review.rating), func.count(Review.id)).where(Review.vehicle_id == v.id)).one()
    return dict(id=v.id, brand=v.brand, model=v.model, name=f"{v.brand} {v.model}", registration=v.registration,
                category=category.name, category_slug=category.slug, category_id=v.category_id,
                branch_id=v.branch_id, branch=branch.name, city=branch.city, location=f"{branch.name}, {branch.city}",
                year=v.year, fuel_type=v.fuel_type, transmission=v.transmission, seats=v.seats,
                daily_rate=float(v.daily_rate), status=v.status, image_url=v.image_url,
                description=v.description, featured=v.featured,
                rating=round(float(avg), 1) if avg else None, review_count=int(count))


def serialize_payment(p):
    return dict(id=p.id, booking_id=p.booking_id, amount=float(p.amount), method=p.method,
                status=p.status, provider=p.provider, reference=p.reference,
                gateway_order_id=p.gateway_order_id, refund_reference=p.refund_reference,
                paid_at=p.paid_at.isoformat() if p.paid_at else None,
                created_at=p.created_at.isoformat())


def serialize_booking(db, b, include_user=False):
    v = db.get(Vehicle, b.vehicle_id)
    payments = list(db.scalars(select(Payment).where(Payment.booking_id == b.id).order_by(Payment.id)))
    review = db.scalar(select(Review).where(Review.booking_id == b.id))
    can_cancel = b.status in ('Pending','Confirmed') and b.pickup_at - local_now() >= timedelta(hours=24)
    outstanding = sum((p.amount for p in payments if p.status in ('Pending','Failed')), Decimal('0'))
    paid = sum((p.amount for p in payments if p.status == 'Paid'), Decimal('0'))
    data = dict(id=b.id, reference=f"RM-{b.id:05d}", vehicle=serialize_vehicle(db, v),
                pickup_at=b.pickup_at.isoformat(), return_at=b.return_at.isoformat(),
                pickup_location=b.pickup_location, return_location=b.return_location,
                days=b.days, daily_rate=float(b.daily_rate), rental_amount=float(b.rental_amount),
                service_fee=float(b.service_fee), tax=float(b.tax), late_fee=float(b.late_fee),
                extra_charge=float(b.extra_charge), total=float(b.total), status=b.status,
                created_at=b.created_at.isoformat(), hold_expires_at=b.hold_expires_at.isoformat() if b.hold_expires_at else None, can_cancel=can_cancel,
                overdue=b.status == 'Active' and b.return_at < local_now(),
                payments=[serialize_payment(p) for p in payments], paid_amount=float(paid),
                outstanding=float(outstanding), reviewed=bool(review))
    if include_user:
        user = db.get(User, b.user_id)
        data['customer'] = dict(id=user.id, name=user.name, email=user.email)
    return data


def notify(db, user_id, title, message):
    db.add(Notification(user_id=user_id, title=title, message=message, created_at=local_now()))


def availability(db, vehicle, start, end, exclude_booking=None):
    if vehicle.is_deleted or vehicle.status != 'Available':
        return False, f"This vehicle is currently {vehicle.status.lower()}."
    overlap = select(Booking.id).where(Booking.vehicle_id == vehicle.id, Booking.status.in_(ACTIVE_BOOKING),
                                        Booking.pickup_at < end, Booking.return_at > start)
    if exclude_booking:
        overlap = overlap.where(Booking.id != exclude_booking)
    if db.scalar(overlap.limit(1)):
        return False, "This vehicle is already reserved for those dates."
    maintenance = select(Maintenance.id).where(Maintenance.vehicle_id == vehicle.id,
        or_(Maintenance.status == 'In progress', and_(Maintenance.status == 'Scheduled',
            Maintenance.starts_at < end, or_(Maintenance.ends_at.is_(None), Maintenance.ends_at > start))))
    if db.scalar(maintenance.limit(1)):
        return False, "This vehicle has maintenance scheduled during those dates."
    return True, "Available for your dates."


def quote_for(vehicle, start, end):
    seconds = (end - start).total_seconds()
    days = max(1, math.ceil(seconds / 86400))
    if seconds <= 0:
        abort(400, description="Return must be after pickup.")
    if days > 90:
        abort(400, description="Rentals can be up to 90 days. Please contact us for longer trips.")
    rental = money(vehicle.daily_rate * days)
    fee = money(rental * Decimal('0.05'))
    tax = money((rental + fee) * Decimal('0.05'))
    return dict(days=days, daily_rate=vehicle.daily_rate, rental_amount=rental, service_fee=fee, tax=tax, total=rental+fee+tax)


def validate_period(data, future=True):
    start = parse_date(data.get('pickup_at'), 'pickup date and time')
    end = parse_date(data.get('return_at'), 'return date and time')
    if end <= start:
        abort(400, description="Return must be after pickup.")
    if future and start < local_now():
        abort(400, description="Choose a pickup date and time in the future.")
    if start > local_now() + timedelta(days=366):
        abort(400, description="Pickup must be within the next year.")
    return start, end


def current_user(db):
    token = session.get('auth_token')
    user_id = session.get('user_id')
    if not isinstance(token, str) or not user_id:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    ticket = db.get(AuthSession, digest)
    user = db.get(User, user_id)
    if not ticket or ticket.user_id != user_id or ticket.expires_at <= local_now() or not user or not user.active:
        session.pop('auth_token', None)
        session.pop('user_id', None)
        return None
    return user


def establish_session(db, user):
    # The browser receives a random, signed, HttpOnly token; only its digest is stored server-side.
    old_token = session.get('auth_token')
    if isinstance(old_token, str):
        db.execute(delete(AuthSession).where(AuthSession.token_hash==hashlib.sha256(old_token.encode()).hexdigest()))
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= local_now()))
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id,
                       expires_at=local_now()+timedelta(hours=12)))
    db.commit()
    session.clear()
    session.permanent = True
    session['user_id'] = user.id
    session['auth_token'] = token
    session['csrf'] = secrets.token_urlsafe(32)


def require(*roles):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = current_user(g.db)
            if not user or not user.active:
                abort(401, description="Please log in to continue.")
            if roles and user.role not in roles:
                abort(403, description="You don't have permission to do that.")
            g.user = user
            return fn(*args, **kwargs)
        return wrapped
    return decorate


def cancel_booking(db, b, status='Cancelled'):
    b.status = status
    b.hold_expires_at = None
    message = 'Your booking has been ' + status.lower() + '.'
    for payment in db.scalars(select(Payment).where(Payment.booking_id == b.id)):
        if payment.status == 'Pending':
            payment.status = 'Failed'
        elif payment.status == 'Paid' and payment.provider == 'mock':
            payment.status = 'Refunded'
            message += ' Your simulated payment was refunded.'
        elif payment.status == 'Paid':
            message += ' A refund requires administrator review.'
    notify(db, b.user_id, f"Booking {status.lower()}", f"{f'RM-{b.id:05d}'}: {message}")
    return message


def activate_due_maintenance(db, commit=True):
    due = list(db.scalars(select(Maintenance).where(Maintenance.status == 'Scheduled', Maintenance.starts_at <= local_now()).with_for_update()))
    changed = False
    for item in due:
        vehicle = db.get(Vehicle, item.vehicle_id)
        if vehicle and vehicle.status == 'Available':
            item.status = 'In progress'; vehicle.status = 'Under Maintenance'; changed = True
    if changed:
        db.flush()
        if commit: db.commit()


class SchemeAwareSessionInterface(SecureCookieSessionInterface):
    """Cookie flags that follow the scheme the browser actually uses.

    Behind a TLS-terminating proxy (hosted previews, PaaS) Flask only sees plain
    HTTP, so ProxyFix is applied and `request.is_secure` reports the public scheme.
    Over HTTPS in non-production the cookie is also marked SameSite=None, because a
    preview that is embedded in a cross-site iframe never sends a Lax cookie.
    State-changing API calls remain protected by the CSRF token.
    """

    def get_cookie_secure(self, app):
        return request.is_secure or app.config['SESSION_COOKIE_SECURE']

    def get_cookie_samesite(self, app):
        configured = app.config['SESSION_COOKIE_SAMESITE']
        if request.is_secure and app.config['APP_ENV'] != 'production':
            return 'None'
        return configured or None


def create_app(config=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(
        APP_ENV=os.environ.get('APP_ENV','development'),
        SECRET_KEY=os.environ.get('SECRET_KEY', 'development-only-change-this-secret'),
        DATABASE_URL=os.environ.get('DATABASE_URL', default_database_url()),
        PAYMENT_MODE=os.environ.get('PAYMENT_MODE', 'mock'),
        RAZORPAY_KEY_ID=os.environ.get('RAZORPAY_KEY_ID', ''),
        RAZORPAY_KEY_SECRET=os.environ.get('RAZORPAY_KEY_SECRET', ''),
        SHOW_RESET_LINK=os.environ.get('SHOW_RESET_LINK', '1') == '1',
        PUBLIC_BASE_URL=os.environ.get('PUBLIC_BASE_URL', 'http://localhost:3000').rstrip('/'),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE=os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax'),
        SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', '0') == '1',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12), MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        SEED_DEMO=os.environ.get('SEED_DEMO','1') == '1', UPLOAD_DIR=os.environ.get('UPLOAD_DIR', ''),
        # CSP frame-ancestors source list. Empty = 'self' in production, '*' in development
        # (so local previews and hosted sandboxes can embed the app in an iframe).
        FRAME_ANCESTORS=os.environ.get('FRAME_ANCESTORS', '').strip(),
    )
    if config:
        app.config.update(config)
    app.session_interface = SchemeAwareSessionInterface()
    if os.environ.get('TRUST_PROXY', '1') == '1':
        # Honour X-Forwarded-Proto/Host from the TLS-terminating preview proxy.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    if app.config['APP_ENV'] == 'production':
        if len(app.config['SECRET_KEY']) < 32 or app.config['SECRET_KEY'] in ('development-only-change-this-secret', 'replace-with-a-long-random-secret-before-deploying'):
            raise RuntimeError('Production requires a long, random SECRET_KEY.')
        if not app.config['SESSION_COOKIE_SECURE'] or app.config['SHOW_RESET_LINK'] or app.config['SEED_DEMO']:
            raise RuntimeError('Production requires secure cookies, disabled demo reset links, and disabled demo seeding.')
        if app.config['PAYMENT_MODE'] == 'mock':
            raise RuntimeError('Simulated payment mode is not allowed in production.')
    engine, Session = create_database(app.config['DATABASE_URL'])
    app.extensions['roam_engine'] = engine
    app.extensions['roam_session'] = Session
    if app.config.get('SEED_DEMO'):
        with Session() as db:
            seed_data(db)
    reset_serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='roam-password-reset')
    login_attempts = defaultdict(list)
    upload_dir = Path(app.config['UPLOAD_DIR']) if app.config.get('UPLOAD_DIR') else ROOT / 'backend' / 'instance' / 'uploads'
    upload_dir.mkdir(parents=True, exist_ok=True)

    @app.before_request
    def prepare_request():
        g.db = Session()
        if request.path.startswith('/api/') and request.method in ('POST','PUT','PATCH','DELETE'):
            expected = session.get('csrf', '')
            received = request.headers.get('X-CSRF-Token', '')
            if not expected or not received or not hmac.compare_digest(expected, received):
                abort(403, description='Your session expired. Please refresh and try again.')
            # SQLite ignores FOR UPDATE. An immediate write lock serializes all booking,
            # vehicle and maintenance mutations; MySQL uses row locks in the handlers.
            if app.config['DATABASE_URL'].startswith('sqlite'):
                g.db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        # Expire abandoned 15-minute checkout holds. In a mutating request the
        # changes stay in the same transaction as the operation and its lock.
        if request.path.startswith('/api/') and request.path != '/api/health':
            stale = list(g.db.scalars(select(Booking).where(Booking.status == 'Pending',
                         Booking.hold_expires_at.is_not(None), Booking.hold_expires_at < local_now()).with_for_update()))
            for booking in stale:
                cancel_booking(g.db, booking)
            if stale:
                g.db.flush()
                if request.method == 'GET':
                    g.db.commit()

    @app.teardown_request
    def close_db(exc):
        db = g.pop('db', None)
        if db:
            if exc:
                db.rollback()
            db.close()

    @app.after_request
    def secure_headers(resp):
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['Referrer-Policy'] = 'same-origin'
        ancestors = app.config['FRAME_ANCESTORS'] or ("'self'" if app.config['APP_ENV'] == 'production' else '*')
        if ancestors == "'self'":
            resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
        else:
            # An explicit allow-list replaces X-Frame-Options; browsers honour frame-ancestors.
            resp.headers['Content-Security-Policy'] = f"frame-ancestors {ancestors}"
        if request.path.startswith('/api/'):
            resp.headers['Cache-Control'] = 'no-store'
        return resp

    @app.errorhandler(HTTPException)
    def http_error(err):
        if request.path.startswith('/api/'):
            return jsonify(error=err.description), err.code
        return err

    @app.errorhandler(IntegrityError)
    def integrity_error(err):
        g.db.rollback()
        return jsonify(error='This record conflicts with existing data. Please check your entries.'), 409

    @app.errorhandler(413)
    def too_large(err):
        return jsonify(error='Image is too large. The limit is 5 MB.'), 413

    @app.get('/api/health')
    def health():
        return jsonify(ok=True)

    @app.get('/api/session')
    def get_session():
        if not session.get('csrf'):
            session['csrf'] = secrets.token_urlsafe(32)
        u = current_user(g.db)
        return jsonify(user=serialize_user(g.db, u) if u else None, csrf=session['csrf'], payment_mode=app.config['PAYMENT_MODE'])

    @app.post('/api/auth/register')
    def register():
        data=json_data()
        name=text(data.get('name'),'Name',100,2); email=email_address(data.get('email')); password=password_value(data.get('password'))
        phone=text(data.get('phone',''),'Phone',24,0)
        if g.db.scalar(select(User.id).where(User.email==email)):
            abort(409, description='An account with that email already exists.')
        user=User(name=name,email=email,password_hash=generate_password_hash(password),role='customer',created_at=local_now())
        g.db.add(user); g.db.flush(); g.db.add(Customer(user_id=user.id,phone=phone,license_number=''))
        notify(g.db,user.id,'Welcome to Roam', 'Your account is ready. Your next journey starts here.')
        establish_session(g.db,user)
        return jsonify(user=serialize_user(g.db,user),csrf=session['csrf']),201

    @app.post('/api/auth/login')
    def login():
        data=json_data(); email=email_address(data.get('email')); password=data.get('password','')
        # Simple process-local rate limiting is a demo safeguard; use a shared limiter in production.
        key=(request.remote_addr or 'unknown',email); now=local_now()
        login_attempts[key]=[t for t in login_attempts[key] if t > now-timedelta(minutes=15)]
        if len(login_attempts[key]) >= 10:
            abort(429, description='Too many attempts. Please try again in 15 minutes.')
        user=g.db.scalar(select(User).where(User.email==email))
        if not user or not isinstance(password,str) or len(password)>128 or not check_password_hash(user.password_hash,password) or not user.active:
            login_attempts[key].append(now); abort(401, description='Incorrect email or password, or account is disabled.')
        login_attempts.pop(key,None); establish_session(g.db,user)
        return jsonify(user=serialize_user(g.db,user),csrf=session['csrf'])

    @app.post('/api/auth/logout')
    def logout():
        token=session.get('auth_token')
        if isinstance(token,str):
            ticket=g.db.get(AuthSession,hashlib.sha256(token.encode()).hexdigest())
            if ticket: g.db.delete(ticket); g.db.commit()
        session.clear(); session['csrf']=secrets.token_urlsafe(32)
        return jsonify(ok=True,csrf=session['csrf'])

    @app.post('/api/auth/forgot')
    def forgot():
        data=json_data(); email=email_address(data.get('email'))
        key=('forgot',request.remote_addr or 'unknown'); now=local_now()
        login_attempts[key]=[t for t in login_attempts[key] if t > now-timedelta(minutes=15)]
        if len(login_attempts[key])>=5: abort(429,description='Too many reset requests. Please try again in 15 minutes.')
        login_attempts[key].append(now)
        u=g.db.scalar(select(User).where(User.email==email,User.active.is_(True)))
        response={'message':'If an account exists for that email, a reset link has been sent.'}
        if u:
            fingerprint=hashlib.sha256(u.password_hash.encode()).hexdigest()[:20]
            token=reset_serializer.dumps({'id':u.id,'fingerprint':fingerprint})
            link=f"{app.config['PUBLIC_BASE_URL']}/reset-password?token={urlquote(token)}"
            if app.config['SHOW_RESET_LINK']:
                response['demo_reset_link']=link
            elif os.environ.get('SMTP_HOST') and os.environ.get('SMTP_FROM'):
                msg=EmailMessage(); msg['Subject']='Reset your Roam password'; msg['From']=os.environ['SMTP_FROM']; msg['To']=u.email
                msg.set_content(f'Use this link within one hour to reset your password:\n\n{link}\n\nIf you did not request this, ignore this email.')
                try:
                    with smtplib.SMTP(os.environ['SMTP_HOST'],int(os.environ.get('SMTP_PORT','587')),timeout=10) as smtp:
                        smtp.starttls()
                        if os.environ.get('SMTP_USERNAME'): smtp.login(os.environ['SMTP_USERNAME'],os.environ.get('SMTP_PASSWORD',''))
                        smtp.send_message(msg)
                except (OSError,smtplib.SMTPException):
                    app.logger.exception('Reset email could not be sent')
        return jsonify(response)

    @app.post('/api/auth/reset')
    def reset_password():
        data=json_data(); token=text(data.get('token'),'Reset token',2048); password=password_value(data.get('password'))
        try:
            payload=reset_serializer.loads(token,max_age=3600)
        except (BadSignature, SignatureExpired):
            abort(400,description='This reset link is invalid or has expired.')
        u=g.db.get(User,payload.get('id'))
        if not u or not u.active or not hmac.compare_digest(payload.get('fingerprint',''),hashlib.sha256(u.password_hash.encode()).hexdigest()[:20]):
            abort(400,description='This reset link is invalid or has already been used.')
        u.password_hash=generate_password_hash(password)
        g.db.execute(delete(AuthSession).where(AuthSession.user_id==u.id)); g.db.commit()
        return jsonify(message='Password updated. You can now log in.')

    @app.put('/api/profile')
    @require()
    def update_profile():
        data=json_data(); g.user.name=text(data.get('name'),'Name',100,2)
        c=g.db.scalar(select(Customer).where(Customer.user_id==g.user.id))
        if c:
            c.phone=text(data.get('phone',''),'Phone',24,0); c.license_number=text(data.get('license_number',''),'License number',50,0)
        if data.get('new_password'):
            current_password=data.get('current_password','')
            if not isinstance(current_password,str) or len(current_password)>128 or not check_password_hash(g.user.password_hash,current_password):
                abort(400,description='Current password is incorrect.')
            g.user.password_hash=generate_password_hash(password_value(data['new_password']))
            current_digest=hashlib.sha256(session['auth_token'].encode()).hexdigest()
            g.db.execute(delete(AuthSession).where(AuthSession.user_id==g.user.id,AuthSession.token_hash!=current_digest))
        g.db.commit(); return jsonify(user=serialize_user(g.db,g.user))

    @app.get('/api/categories')
    def categories():
        items=[]
        for c in g.db.scalars(select(VehicleCategory).order_by(VehicleCategory.id)):
            count=g.db.scalar(select(func.count(Vehicle.id)).where(Vehicle.category_id==c.id,Vehicle.is_deleted.is_(False)))
            items.append(dict(id=c.id,name=c.name,slug=c.slug,count=count))
        return jsonify(items)

    @app.get('/api/branches')
    def branches():
        return jsonify([dict(id=b.id,name=b.name,city=b.city,address=b.address) for b in g.db.scalars(select(Branch).order_by(Branch.id))])

    @app.get('/api/vehicles')
    def vehicle_list():
        activate_due_maintenance(g.db)
        q=select(Vehicle).where(Vehicle.is_deleted.is_(False))
        category=request.args.get('category','').strip()
        if category and category!='all':
            q=q.join(VehicleCategory).where(VehicleCategory.slug==category.lower())
        keyword=request.args.get('q','').strip()[:100]
        if keyword:
            term=f"%{keyword}%"
            q=q.where(or_(Vehicle.brand.ilike(term),Vehicle.model.ilike(term), (Vehicle.brand + ' ' + Vehicle.model).ilike(term)))
        branch=request.args.get('branch','').strip()
        if branch:
            try: branch=int(branch)
            except ValueError: abort(400,description='Invalid location.')
            q=q.where(Vehicle.branch_id==branch)
        city=request.args.get('city','').strip()
        if city:
            q=q.join(Branch).where(Branch.city.ilike(f'%{city}%'))
        if request.args.get('min_price'):
            q=q.where(Vehicle.daily_rate>=parse_money(request.args['min_price'],'minimum price'))
        if request.args.get('max_price'):
            q=q.where(Vehicle.daily_rate<=parse_money(request.args['max_price'],'maximum price'))
        if request.args.get('featured')=='true': q=q.where(Vehicle.featured.is_(True))
        sort=request.args.get('sort','featured')
        if sort=='price_asc': q=q.order_by(Vehicle.daily_rate.asc(),Vehicle.id)
        elif sort=='price_desc': q=q.order_by(Vehicle.daily_rate.desc(),Vehicle.id)
        elif sort=='name': q=q.order_by(Vehicle.brand,Vehicle.model)
        else: q=q.order_by(Vehicle.featured.desc(),Vehicle.id)
        vehicles=list(g.db.scalars(q))
        start=request.args.get('pickup_at'); end=request.args.get('return_at')
        if bool(start)!=bool(end): abort(400,description='Select both pickup and return dates.')
        if start:
            start,end=validate_period({'pickup_at':start,'return_at':end})
            vehicles=[v for v in vehicles if availability(g.db,v,start,end)[0]]
        elif request.args.get('available')=='true':
            # Without dates, show vehicles available for immediate pickup.
            now=local_now()
            vehicles=[v for v in vehicles if availability(g.db,v,now,now+timedelta(days=1))[0]]
        return jsonify([serialize_vehicle(g.db,v) for v in vehicles])

    @app.get('/api/vehicles/<int:vehicle_id>')
    def vehicle_details(vehicle_id):
        activate_due_maintenance(g.db)
        v=g.db.get(Vehicle,vehicle_id)
        if not v or v.is_deleted: abort(404,description='Vehicle not found.')
        out=serialize_vehicle(g.db,v)
        reviews=list(g.db.scalars(select(Review).where(Review.vehicle_id==v.id).order_by(Review.created_at.desc())))
        out['reviews']=[dict(id=r.id,rating=r.rating,comment=r.comment,name=g.db.get(User,r.user_id).name,created_at=r.created_at.isoformat()) for r in reviews]
        return jsonify(out)

    @app.get('/api/reviews')
    def reviews():
        records=list(g.db.scalars(select(Review).order_by(Review.created_at.desc()).limit(8)))
        return jsonify([dict(id=r.id,rating=r.rating,comment=r.comment,name=g.db.get(User,r.user_id).name,
                             vehicle=f"{g.db.get(Vehicle,r.vehicle_id).brand} {g.db.get(Vehicle,r.vehicle_id).model}",created_at=r.created_at.isoformat()) for r in records])

    @app.post('/api/contact')
    def contact():
        d=json_data()
        msg=ContactMessage(name=text(d.get('name'),'Name',100,2),email=email_address(d.get('email')),
                           subject=text(d.get('subject'),'Subject',150,2),message=text(d.get('message'),'Message',4000,10),created_at=local_now())
        g.db.add(msg); g.db.commit()
        return jsonify(message='Thanks for reaching out. Your message has been received.'),201

    @app.post('/api/quote')
    def get_quote():
        activate_due_maintenance(g.db)
        d=json_data()
        try: vehicle_id=int(d.get('vehicle_id'))
        except (ValueError,TypeError): abort(400,description='Choose a vehicle.')
        v=g.db.get(Vehicle,vehicle_id)
        if not v or v.is_deleted: abort(404,description='Vehicle not found.')
        start,end=validate_period(d); amounts=quote_for(v,start,end)
        available,reason=availability(g.db,v,start,end)
        return jsonify({**{k:float(x) if isinstance(x,Decimal) else x for k,x in amounts.items()},'available':available,'message':reason})

    @app.post('/api/bookings')
    @require('customer')
    def create_booking():
        d=json_data()
        try: vehicle_id=int(d.get('vehicle_id'))
        except (ValueError,TypeError): abort(400,description='Choose a vehicle.')
        start,end=validate_period(d)
        activate_due_maintenance(g.db, commit=False)
        # A row-level lock prevents concurrent MySQL transactions booking the same vehicle.
        v=g.db.scalar(select(Vehicle).where(Vehicle.id==vehicle_id).with_for_update())
        if not v or v.is_deleted: abort(404,description='Vehicle not found.')
        amounts=quote_for(v,start,end); ok,reason=availability(g.db,v,start,end)
        if not ok: abort(409,description=reason)
        pickup=text(d.get('pickup_location'),'Pickup location',180,2)
        ret=text(d.get('return_location'),'Return location',180,2)
        b=Booking(user_id=g.user.id,vehicle_id=v.id,pickup_at=start,return_at=end,pickup_location=pickup,return_location=ret,
                  status='Pending',hold_expires_at=local_now()+timedelta(minutes=15),created_at=local_now(),**amounts)
        g.db.add(b); g.db.flush()
        g.db.add(Payment(booking_id=b.id,amount=b.total,status='Pending',method='',provider='',created_at=local_now()))
        notify(g.db,g.user.id,'Your booking is reserved',f'Booking RM-{b.id:05d} is awaiting payment.')
        g.db.commit()
        return jsonify(booking=serialize_booking(g.db,b)),201

    @app.get('/api/bookings')
    @require('customer')
    def my_bookings():
        items=list(g.db.scalars(select(Booking).where(Booking.user_id==g.user.id).order_by(Booking.created_at.desc(),Booking.id.desc())))
        return jsonify([serialize_booking(g.db,b) for b in items])

    @app.get('/api/bookings/<int:booking_id>')
    @require()
    def booking_detail(booking_id):
        b=g.db.get(Booking,booking_id)
        if not b or (g.user.role=='customer' and b.user_id!=g.user.id): abort(404,description='Booking not found.')
        return jsonify(serialize_booking(g.db,b,include_user=g.user.role!='customer'))

    @app.post('/api/bookings/<int:booking_id>/cancel')
    @require('customer')
    def cancel(booking_id):
        b=g.db.scalar(select(Booking).where(Booking.id==booking_id).with_for_update())
        if not b or b.user_id!=g.user.id: abort(404,description='Booking not found.')
        if b.status not in ('Pending','Confirmed'): abort(400,description='This booking cannot be cancelled.')
        if b.pickup_at-local_now() < timedelta(hours=24): abort(400,description='Bookings can be cancelled up to 24 hours before pickup. Please contact support.')
        msg=cancel_booking(g.db,b); g.db.commit()
        return jsonify(message=msg,booking=serialize_booking(g.db,b))

    @app.post('/api/bookings/<int:booking_id>/payment')
    @require('customer')
    def pay(booking_id):
        d=json_data(); method=d.get('method')
        if method not in ('UPI','Card','Cash','Net banking'): abort(400,description='Choose a valid payment method.')
        b=g.db.scalar(select(Booking).where(Booking.id==booking_id).with_for_update())
        if not b or b.user_id!=g.user.id: abort(404,description='Booking not found.')
        if b.status in ('Cancelled','Rejected'): abort(400,description='This booking is no longer payable.')
        p=g.db.scalar(select(Payment).where(Payment.booking_id==b.id,Payment.status.in_(('Pending','Failed'))).order_by(Payment.id).with_for_update())
        if not p: abort(400,description='There is no outstanding payment for this booking.')
        p.method=method; p.status='Pending'
        if method=='Cash':
            p.provider='cash'; p.reference=f'CASH-{p.id:05d}'
            if b.status=='Pending': b.status='Confirmed'; b.hold_expires_at=None
            notify(g.db,b.user_id,'Booking confirmed',f'RM-{b.id:05d} is confirmed. Your cash payment is pending.')
            g.db.commit(); return jsonify(message='Cash payment is pending. Please pay the team in person.',booking=serialize_booking(g.db,b))
        mode=app.config['PAYMENT_MODE']
        if mode=='mock':
            p.provider='mock'; p.status='Paid'; p.paid_at=local_now(); p.reference='TEST-'+secrets.token_hex(5).upper()
            if b.status=='Pending': b.status='Confirmed'; b.hold_expires_at=None
            notify(g.db,b.user_id,'Booking confirmed',f'RM-{b.id:05d} is confirmed. Your test payment was recorded.')
            g.db.commit(); return jsonify(message='Test payment successful. No real money was charged.',booking=serialize_booking(g.db,b))
        if mode!='razorpay_test': abort(503,description='Payments are not configured. Please contact support.')
        key=app.config['RAZORPAY_KEY_ID']; secret=app.config['RAZORPAY_KEY_SECRET']
        if not key.startswith('rzp_test_') or not secret:
            abort(503,description='A Razorpay test key and secret must be configured. Live keys are not accepted.')
        # Reuse the same order until it is paid, preventing accidental duplicate orders.
        if not p.gateway_order_id:
            try:
                response=requests.post('https://api.razorpay.com/v1/orders',auth=(key,secret),json={'amount':int(p.amount*100),'currency':'INR','receipt':f'roam-{b.id}-{p.id}'},timeout=10)
                if not response.ok: raise requests.RequestException('Gateway rejected the order')
                p.gateway_order_id=response.json()['id']
            except (requests.RequestException,ValueError,KeyError):
                abort(502,description='Could not create a test payment order. Please try again.')
        p.provider='razorpay_test'; g.db.commit()
        return jsonify(checkout={'key':key,'amount':int(p.amount*100),'currency':'INR','order_id':p.gateway_order_id,
                                 'name':'Roam Rentals','description':f'Booking RM-{b.id:05d}','payment_id':p.id,
                                 'prefill':{'name':g.user.name,'email':g.user.email}})

    @app.post('/api/payments/<int:payment_id>/verify')
    @require('customer')
    def verify_payment(payment_id):
        d=json_data()
        p=g.db.scalar(select(Payment).where(Payment.id==payment_id).with_for_update())
        b=g.db.get(Booking,p.booking_id) if p else None
        if not p or not b or b.user_id!=g.user.id: abort(404,description='Payment not found.')
        if p.status=='Paid': return jsonify(message='Payment already confirmed.',booking=serialize_booking(g.db,b))
        order=d.get('razorpay_order_id',''); payment_id_gateway=d.get('razorpay_payment_id',''); signature=d.get('razorpay_signature','')
        if p.provider!='razorpay_test' or not p.gateway_order_id or order!=p.gateway_order_id or not isinstance(payment_id_gateway,str) or not re.fullmatch(r'pay_[A-Za-z0-9]{1,90}',payment_id_gateway) or not isinstance(signature,str):
            abort(400,description='Payment verification failed.')
        expected=hmac.new(app.config['RAZORPAY_KEY_SECRET'].encode(),f'{order}|{payment_id_gateway}'.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,signature): abort(400,description='Payment verification failed.')
        if b.status in ('Cancelled','Rejected'): abort(409,description='This booking was cancelled; contact support for a refund.')
        # A valid checkout signature alone does not prove capture. Fetch the payment
        # using server credentials and verify its order, amount, currency and status.
        key=app.config['RAZORPAY_KEY_ID']; secret=app.config['RAZORPAY_KEY_SECRET']
        if not key.startswith('rzp_test_') or not secret: abort(503,description='Razorpay test credentials are not configured.')
        try:
            response=requests.get(f'https://api.razorpay.com/v1/payments/{payment_id_gateway}',auth=(key,secret),timeout=10)
            if not response.ok: raise requests.RequestException('Payment lookup failed')
            gateway=response.json()
            if gateway.get('id')!=payment_id_gateway or gateway.get('order_id')!=order or gateway.get('amount')!=int(p.amount*100) or gateway.get('currency')!='INR':
                abort(400,description='Gateway payment details do not match this booking.')
            if gateway.get('status')!='captured':
                abort(409,description='The test payment is not captured yet. Please contact support to reconcile it.')
        except (requests.RequestException,ValueError,KeyError):
            abort(502,description='Could not verify the payment with the test gateway.')
        p.status='Paid'; p.gateway_payment_id=payment_id_gateway; p.reference=payment_id_gateway; p.paid_at=local_now()
        if b.status=='Pending': b.status='Confirmed'; b.hold_expires_at=None
        notify(g.db,b.user_id,'Booking confirmed',f'Your test payment for RM-{b.id:05d} was verified.')
        g.db.commit(); return jsonify(message='Test payment verified.',booking=serialize_booking(g.db,b))

    @app.get('/api/bookings/<int:booking_id>/receipt')
    @require()
    def receipt(booking_id):
        b=g.db.get(Booking,booking_id)
        if not b or (g.user.role=='customer' and b.user_id!=g.user.id): abort(404,description='Booking not found.')
        v=g.db.get(Vehicle,b.vehicle_id); user=g.db.get(User,b.user_id)
        payments=list(g.db.scalars(select(Payment).where(Payment.booking_id==b.id)))
        buffer=io.BytesIO(); pdf=canvas.Canvas(buffer,pagesize=A4)
        pdf.setFillColorRGB(.09,.20,.38); pdf.setFont('Helvetica-Bold',24); pdf.drawString(48,790,'roam.'); pdf.setFont('Helvetica',10); pdf.drawString(48,770,'RENTAL RECEIPT / BOOKING SUMMARY')
        pdf.setFillColorRGB(.12,.15,.21); pdf.setFont('Helvetica-Bold',16); pdf.drawString(48,728,f'Booking RM-{b.id:05d}')
        pdf.setFont('Helvetica',11); y=700
        rows=[('Customer',user.name),('Vehicle',f'{v.brand} {v.model}'),('Pickup',b.pickup_at.strftime('%d %b %Y, %I:%M %p')),('Return',b.return_at.strftime('%d %b %Y, %I:%M %p')),('Status',b.status),('Rental charge',f'INR {b.rental_amount:,.2f}'),('Service fee',f'INR {b.service_fee:,.2f}'),('GST',f'INR {b.tax:,.2f}'),('Late / additional charges',f'INR {b.late_fee+b.extra_charge:,.2f}'),('Total',f'INR {b.total:,.2f}')]
        for label,value in rows:
            pdf.drawString(48,y,label); pdf.drawRightString(545,y,value); y-=25
        y-=15; pdf.setFont('Helvetica-Bold',12); pdf.drawString(48,y,'Payment history'); y-=23; pdf.setFont('Helvetica',10)
        for p in payments:
            pdf.drawString(48,y,f'{p.method or "Not selected"}  /  {p.provider or "Pending"}  /  {p.status}'); pdf.drawRightString(545,y,f'INR {p.amount:,.2f}'); y-=20
        pdf.setFont('Helvetica',9); pdf.drawString(48,60,'Demo receipt. Test payments do not represent real charges.'); pdf.showPage(); pdf.save(); buffer.seek(0)
        return send_file(buffer,mimetype='application/pdf',as_attachment=True,download_name=f'roam-receipt-{b.id:05d}.pdf')

    @app.post('/api/bookings/<int:booking_id>/review')
    @require('customer')
    def add_review(booking_id):
        b=g.db.get(Booking,booking_id)
        if not b or b.user_id!=g.user.id: abort(404,description='Booking not found.')
        if b.status!='Completed': abort(400,description='Reviews are available after your trip is completed.')
        if g.db.scalar(select(Review.id).where(Review.booking_id==b.id)): abort(409,description='You have already reviewed this trip.')
        d=json_data()
        try: rating=int(d.get('rating'))
        except (ValueError,TypeError): abort(400,description='Select a rating from 1 to 5.')
        if rating<1 or rating>5: abort(400,description='Select a rating from 1 to 5.')
        r=Review(booking_id=b.id,user_id=g.user.id,vehicle_id=b.vehicle_id,rating=rating,comment=text(d.get('comment'),'Review',600,5),created_at=local_now())
        g.db.add(r); g.db.commit(); return jsonify(message='Thank you for sharing your experience.'),201

    @app.get('/api/notifications')
    @require()
    def notifications():
        items=[dict(id=n.id,title=n.title,message=n.message,is_read=n.is_read,created_at=n.created_at.isoformat(),dynamic=False)
               for n in g.db.scalars(select(Notification).where(Notification.user_id==g.user.id).order_by(Notification.created_at.desc()).limit(40))]
        if g.user.role=='customer':
            for b in g.db.scalars(select(Booking).where(Booking.user_id==g.user.id,Booking.status=='Active')):
                delta=b.return_at-local_now()
                if delta <= timedelta(hours=24):
                    items.insert(0,dict(id=f'return-{b.id}',title='Return overdue' if delta.total_seconds()<0 else 'Return coming up',
                        message=f'Booking RM-{b.id:05d} is due back {b.return_at.strftime("%d %b at %I:%M %p")}.',is_read=False,created_at=local_now().isoformat(),dynamic=True))
        else:
            overdue=g.db.scalar(select(func.count(Booking.id)).where(Booking.status=='Active',Booking.return_at<local_now()))
            if overdue: items.insert(0,dict(id='overdue',title='Overdue returns',message=f'{overdue} rental(s) need your attention.',is_read=False,created_at=local_now().isoformat(),dynamic=True))
        return jsonify(items)

    @app.patch('/api/notifications/<int:notification_id>')
    @require()
    def mark_notification(notification_id):
        n=g.db.get(Notification,notification_id)
        if not n or n.user_id!=g.user.id: abort(404,description='Notification not found.')
        n.is_read=True; g.db.commit(); return jsonify(ok=True)

    @app.get('/api/admin/summary')
    @require('admin','staff')
    def admin_summary():
        activate_due_maintenance(g.db)
        now=local_now()
        total_vehicles=g.db.scalar(select(func.count(Vehicle.id)).where(Vehicle.is_deleted.is_(False)))
        customers=g.db.scalar(select(func.count(User.id)).where(User.role=='customer')) if g.user.role=='admin' else None
        active=g.db.scalar(select(func.count(Booking.id)).where(Booking.status=='Active'))
        overdue=g.db.scalar(select(func.count(Booking.id)).where(Booking.status=='Active',Booking.return_at<now))
        revenue=g.db.scalar(select(func.coalesce(func.sum(Payment.amount),0)).where(Payment.status=='Paid')) if g.user.role=='admin' else None
        all_payments=list(g.db.scalars(select(Payment).where(Payment.status=='Paid',Payment.paid_at.is_not(None)))) if g.user.role=='admin' else []
        chart=[]
        for i in (range(5,-1,-1) if g.user.role=='admin' else []):
            month_index=now.year*12+now.month-1-i; year=month_index//12; month=month_index%12+1
            value=sum((p.amount for p in all_payments if p.paid_at.year==year and p.paid_at.month==month),Decimal('0'))
            chart.append(dict(label=datetime(year,month,1).strftime('%b'),value=float(value)))
        status_counts={status:g.db.scalar(select(func.count(Booking.id)).where(Booking.status==status)) for status in ('Pending','Confirmed','Active','Completed','Cancelled','Rejected')}
        recent=list(g.db.scalars(select(Booking).order_by(Booking.created_at.desc(),Booking.id.desc()).limit(6)))
        return jsonify(metrics=dict(vehicles=total_vehicles,customers=customers,active_rentals=active,revenue=float(revenue) if revenue is not None else None,overdue=overdue),
                       revenue_chart=chart,booking_counts=status_counts,recent=[serialize_booking(g.db,b,True) for b in recent])

    def apply_vehicle_data(v,d,create=False):
        v.brand=text(d.get('brand'),'Brand',80,1); v.model=text(d.get('model'),'Model',100,1)
        v.registration=text(d.get('registration'),'Registration number',32,3).upper()
        try:
            v.category_id=int(d.get('category_id')); v.branch_id=int(d.get('branch_id')); v.year=int(d.get('year')); v.seats=int(d.get('seats'))
        except (TypeError,ValueError): abort(400,description='Choose a category and location and enter a valid year and seating capacity.')
        if not g.db.get(VehicleCategory,v.category_id) or not g.db.get(Branch,v.branch_id): abort(400,description='Invalid category or location.')
        if not (1990<=v.year<=local_now().year+1) or not (1<=v.seats<=60): abort(400,description='Enter a valid vehicle year and seating capacity.')
        v.daily_rate=parse_money(d.get('daily_rate'),'daily rate',Decimal('0.01'))
        v.fuel_type=d.get('fuel_type'); v.transmission=d.get('transmission')
        if v.fuel_type not in ('Petrol','Diesel','Electric','CNG','Hybrid'): abort(400,description='Select a valid fuel type.')
        if v.transmission not in ('Manual','Automatic'): abort(400,description='Select a valid transmission.')
        image=d.get('image_url','/images/car-suv.jpg')
        if not isinstance(image,str) or len(image)>255 or not (image.startswith('/images/') or image.startswith('/uploads/')) or '..' in image:
            abort(400,description='Choose a valid uploaded or demo image.')
        v.image_url=image
        v.description=text(d.get('description',''),'Description',2000,0)
        v.featured=d.get('featured') is True
        if create: v.status='Available'; v.created_at=local_now(); v.is_deleted=False

    @app.get('/api/admin/vehicles')
    @require('admin','staff')
    def admin_vehicles():
        activate_due_maintenance(g.db)
        return jsonify([serialize_vehicle(g.db,v) for v in g.db.scalars(select(Vehicle).where(Vehicle.is_deleted.is_(False)).order_by(Vehicle.id))])

    @app.post('/api/admin/vehicles')
    @require('admin')
    def admin_add_vehicle():
        v=Vehicle(); apply_vehicle_data(v,json_data(),True); g.db.add(v); g.db.commit()
        return jsonify(vehicle=serialize_vehicle(g.db,v),message='Vehicle added to your fleet.'),201

    @app.put('/api/admin/vehicles/<int:vehicle_id>')
    @require('admin')
    def admin_edit_vehicle(vehicle_id):
        v=g.db.scalar(select(Vehicle).where(Vehicle.id==vehicle_id).with_for_update())
        if not v or v.is_deleted: abort(404,description='Vehicle not found.')
        apply_vehicle_data(v,json_data()); g.db.commit()
        return jsonify(vehicle=serialize_vehicle(g.db,v),message='Vehicle updated.')

    @app.delete('/api/admin/vehicles/<int:vehicle_id>')
    @require('admin')
    def admin_delete_vehicle(vehicle_id):
        v=g.db.scalar(select(Vehicle).where(Vehicle.id==vehicle_id).with_for_update())
        if not v or v.is_deleted: abort(404,description='Vehicle not found.')
        busy=g.db.scalar(select(Booking.id).where(Booking.vehicle_id==v.id,Booking.status.in_(ACTIVE_BOOKING)).limit(1))
        service=g.db.scalar(select(Maintenance.id).where(Maintenance.vehicle_id==v.id,Maintenance.status.in_(('Scheduled','In progress'))).limit(1))
        if busy or service: abort(409,description='Resolve active bookings and maintenance before removing this vehicle.')
        v.is_deleted=True; g.db.commit(); return jsonify(message='Vehicle removed from the fleet. Historical records were retained.')

    @app.post('/api/admin/uploads')
    @require('admin')
    def upload_image():
        file=request.files.get('image')
        if not file or not file.filename: abort(400,description='Select a JPG, PNG, or WebP image.')
        try:
            Image.MAX_IMAGE_PIXELS=24_000_000
            image=Image.open(file.stream); image.verify(); file.stream.seek(0); image=Image.open(file.stream)
            if image.format not in ('JPEG','PNG','WEBP'): abort(400,description='Only JPG, PNG, and WebP images are supported.')
            image=ImageOps.exif_transpose(image).convert('RGB'); image.thumbnail((2000,2000))
            name=secrets.token_hex(16)+'.jpg'; image.save(upload_dir/name,'JPEG',quality=87,optimize=True)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            abort(400,description='That file is not a valid image.')
        return jsonify(url='/uploads/'+name),201

    @app.get('/uploads/<path:filename>')
    def uploaded_image(filename):
        return send_from_directory(upload_dir,filename)

    @app.get('/api/admin/customers')
    @require('admin')
    def admin_customers():
        users=list(g.db.scalars(select(User).where(User.role=='customer').order_by(User.created_at.desc())))
        result=[]
        for u in users:
            item=serialize_user(g.db,u); item['bookings']=g.db.scalar(select(func.count(Booking.id)).where(Booking.user_id==u.id)); result.append(item)
        return jsonify(result)

    @app.patch('/api/admin/customers/<int:user_id>')
    @require('admin')
    def admin_customer_status(user_id):
        u=g.db.get(User,user_id)
        if not u or u.role!='customer': abort(404,description='Customer not found.')
        active=json_data().get('active')
        if not isinstance(active,bool): abort(400,description='A valid account status is required.')
        u.active=active; g.db.commit(); return jsonify(message='Customer account updated.',user=serialize_user(g.db,u))

    @app.get('/api/admin/bookings')
    @require('admin','staff')
    def admin_bookings():
        status=request.args.get('status','')
        q=select(Booking).order_by(Booking.created_at.desc(),Booking.id.desc())
        if status: q=q.where(Booking.status==status)
        return jsonify([serialize_booking(g.db,b,True) for b in g.db.scalars(q)])

    @app.post('/api/admin/bookings/<int:booking_id>/status')
    @require('admin','staff')
    def admin_booking_status(booking_id):
        b=g.db.scalar(select(Booking).where(Booking.id==booking_id).with_for_update())
        if not b: abort(404,description='Booking not found.')
        target=json_data().get('status')
        allowed={'Pending':('Confirmed','Rejected','Cancelled'),'Confirmed':('Active','Cancelled'),'Active':(),'Completed':(),'Cancelled':(),'Rejected':()}
        if target not in allowed.get(b.status,()): abort(400,description='That booking status change is not allowed.')
        if target in ('Cancelled','Rejected'):
            msg=cancel_booking(g.db,b,target)
        elif target=='Active':
            activate_due_maintenance(g.db, commit=False)
            v=g.db.scalar(select(Vehicle).where(Vehicle.id==b.vehicle_id).with_for_update())
            if v.status!='Available': abort(409,description='The vehicle is not available for pickup.')
            if b.pickup_at>local_now()+timedelta(hours=1): abort(400,description='A rental can be started within one hour of its scheduled pickup.')
            if b.return_at<=local_now(): abort(400,description='This rental cannot be started after its scheduled return time.')
            maintenance=g.db.scalar(select(Maintenance.id).where(Maintenance.vehicle_id==v.id,Maintenance.status=='In progress').limit(1))
            if maintenance: abort(409,description='Complete vehicle maintenance before starting this rental.')
            v.status='Rented'; b.status='Active'; msg='Rental started.'
            notify(g.db,b.user_id,'Your rental has started',f'Enjoy your journey! RM-{b.id:05d} is now active.')
        else:
            b.status='Confirmed'; b.hold_expires_at=None; msg='Booking approved.'; notify(g.db,b.user_id,'Booking approved',f'Booking RM-{b.id:05d} is confirmed.')
        g.db.commit(); return jsonify(message=msg,booking=serialize_booking(g.db,b,True))

    @app.get('/api/admin/payments')
    @require('admin')
    def admin_payments():
        result=[]
        for p in g.db.scalars(select(Payment).order_by(Payment.created_at.desc(),Payment.id.desc())):
            b=g.db.get(Booking,p.booking_id); u=g.db.get(User,b.user_id); v=g.db.get(Vehicle,b.vehicle_id)
            item=serialize_payment(p); item.update(booking_reference=f'RM-{b.id:05d}',customer=u.name,vehicle=f'{v.brand} {v.model}')
            result.append(item)
        return jsonify(result)

    @app.patch('/api/admin/payments/<int:payment_id>')
    @require('admin')
    def admin_payment_status(payment_id):
        p=g.db.scalar(select(Payment).where(Payment.id==payment_id).with_for_update())
        if not p: abort(404,description='Payment not found.')
        status=json_data().get('status'); b=g.db.get(Booking,p.booking_id)
        if status not in ('Paid','Failed','Refunded'): abort(400,description='Invalid payment status.')
        if status=='Paid':
            if p.status not in ('Pending','Failed') or p.provider=='razorpay_test': abort(400,description='Gateway payments must be verified by the gateway; already paid payments cannot be paid again.')
            if b.status in ('Cancelled','Rejected'): abort(400,description='Cannot collect payment for a cancelled booking.')
            p.status='Paid'; p.paid_at=local_now(); p.provider=p.provider or 'cash'; p.method=p.method or 'Cash'; p.reference=p.reference or f'MANUAL-{p.id:05d}'
            if b.status=='Pending': b.status='Confirmed'; b.hold_expires_at=None
            notify(g.db,b.user_id,'Payment recorded',f'Payment for RM-{b.id:05d} was marked paid.')
        elif status=='Failed':
            if p.status!='Pending': abort(400,description='Only pending payments can be marked failed.')
            p.status='Failed'
        else:
            if p.status!='Paid': abort(400,description='Only paid payments can be refunded.')
            if p.provider=='razorpay_test':
                key=app.config['RAZORPAY_KEY_ID']; secret=app.config['RAZORPAY_KEY_SECRET']
                if not key.startswith('rzp_test_') or not secret or not p.gateway_payment_id: abort(503,description='Razorpay test credentials are not configured.')
                try:
                    if p.refund_reference:
                        response=requests.get(f'https://api.razorpay.com/v1/refunds/{p.refund_reference}',auth=(key,secret),timeout=10)
                    else:
                        response=requests.post(f'https://api.razorpay.com/v1/payments/{p.gateway_payment_id}/refund',auth=(key,secret),json={'amount':int(p.amount*100)},timeout=10)
                    if not response.ok: raise requests.RequestException('Gateway rejected refund')
                    body=response.json(); p.refund_reference=body['id']
                    if body.get('status')!='processed':
                        g.db.commit(); return jsonify(message='Refund initiated at the gateway. It is still processing; use Refund again to check its status.',payment=serialize_payment(p))
                except (requests.RequestException,ValueError,KeyError): abort(502,description='The test gateway could not process or check this refund.')
            p.status='Refunded'; notify(g.db,b.user_id,'Payment refunded',f'The payment for RM-{b.id:05d} was marked refunded.')
        g.db.commit(); return jsonify(message='Payment updated.',payment=serialize_payment(p))

    @app.get('/api/admin/returns')
    @require('admin','staff')
    def admin_returns():
        q=select(Booking).where(Booking.status=='Active').order_by(Booking.return_at)
        return jsonify([serialize_booking(g.db,b,True) for b in g.db.scalars(q)])

    @app.post('/api/admin/returns/<int:booking_id>')
    @require('admin','staff')
    def record_return(booking_id):
        b=g.db.scalar(select(Booking).where(Booking.id==booking_id).with_for_update())
        if not b or b.status!='Active': abort(400,description='Only an active rental can be returned.')
        d=json_data(); actual=parse_date(d.get('actual_return_at'),'actual return time')
        if actual < b.pickup_at or actual > local_now()+timedelta(minutes=2): abort(400,description='Actual return must be after pickup and not in the future.')
        condition=text(d.get('condition'),'Vehicle condition',100,2); notes=text(d.get('notes',''),'Notes',1000,0)
        extra=parse_money(d.get('additional_charges',0),'additional charges')
        late_days=max(0,math.ceil((actual-b.return_at).total_seconds()/86400))
        late=money(b.daily_rate*late_days)
        r=VehicleReturn(booking_id=b.id,actual_return_at=actual,vehicle_condition=condition,late_fee=late,additional_charges=extra,notes=notes)
        g.db.add(r); b.status='Completed'; b.late_fee=late; b.extra_charge=extra; b.total=money(b.total+late+extra)
        if late+extra>0: g.db.add(Payment(booking_id=b.id,amount=late+extra,status='Pending',method='',provider='',created_at=local_now()))
        v=g.db.scalar(select(Vehicle).where(Vehicle.id==b.vehicle_id).with_for_update())
        servicing=g.db.scalar(select(Maintenance.id).where(Maintenance.vehicle_id==v.id,Maintenance.status=='In progress').limit(1))
        v.status='Under Maintenance' if servicing else 'Available'
        notify(g.db,b.user_id,'Your return is complete',f'RM-{b.id:05d} has been returned. Additional charges: INR {late+extra:.2f}.')
        g.db.commit(); return jsonify(message='Return recorded. The fleet status has been updated.',late_days=late_days,late_fee=float(late),booking=serialize_booking(g.db,b,True))

    @app.get('/api/admin/maintenance')
    @require('admin','staff')
    def admin_maintenance():
        activate_due_maintenance(g.db)
        result=[]
        for m in g.db.scalars(select(Maintenance).order_by(Maintenance.starts_at.desc())):
            v=g.db.get(Vehicle,m.vehicle_id)
            result.append(dict(id=m.id,vehicle_id=v.id,vehicle=f'{v.brand} {v.model}',registration=v.registration,
                               description=m.description,starts_at=m.starts_at.isoformat(),ends_at=m.ends_at.isoformat() if m.ends_at else None,
                               cost=float(m.cost),status=m.status,completed_at=m.completed_at.isoformat() if m.completed_at else None))
        return jsonify(result)

    @app.post('/api/admin/maintenance')
    @require('admin','staff')
    def create_maintenance():
        d=json_data()
        try: vehicle_id=int(d.get('vehicle_id'))
        except (TypeError,ValueError): abort(400,description='Select a vehicle.')
        v=g.db.scalar(select(Vehicle).where(Vehicle.id==vehicle_id).with_for_update())
        if not v or v.is_deleted: abort(404,description='Vehicle not found.')
        start=parse_date(d.get('starts_at'),'maintenance start'); end=parse_date(d.get('ends_at'),'maintenance end') if d.get('ends_at') else None
        if end and end<=start: abort(400,description='Maintenance end must be after the start.')
        if v.status=='Rented': abort(409,description='Return this vehicle before scheduling maintenance.')
        far=end or datetime(9999,12,31,23,59,59)
        busy=g.db.scalar(select(Booking.id).where(Booking.vehicle_id==v.id,Booking.status.in_(ACTIVE_BOOKING),Booking.pickup_at<far,Booking.return_at>start).limit(1))
        other=g.db.scalar(select(Maintenance.id).where(Maintenance.vehicle_id==v.id,Maintenance.status.in_(('Scheduled','In progress')),Maintenance.starts_at<far,or_(Maintenance.ends_at.is_(None),Maintenance.ends_at>start)).limit(1))
        if busy: abort(409,description='Maintenance overlaps an existing booking.')
        if other: abort(409,description='This vehicle already has overlapping maintenance.')
        status='In progress' if start<=local_now() else 'Scheduled'
        if status=='In progress': v.status='Under Maintenance'
        m=Maintenance(vehicle_id=v.id,description=text(d.get('description'),'Description',255,3),starts_at=start,ends_at=end,cost=parse_money(d.get('cost',0),'maintenance cost'),status=status)
        g.db.add(m); g.db.commit(); return jsonify(message='Maintenance scheduled.',id=m.id),201

    @app.post('/api/admin/maintenance/<int:maintenance_id>/complete')
    @require('admin','staff')
    def complete_maintenance(maintenance_id):
        m=g.db.scalar(select(Maintenance).where(Maintenance.id==maintenance_id).with_for_update())
        if not m or m.status=='Completed': abort(400,description='Maintenance record is not active.')
        d=json_data(); m.cost=parse_money(d.get('cost',m.cost),'maintenance cost'); m.status='Completed'; m.completed_at=local_now()
        v=g.db.scalar(select(Vehicle).where(Vehicle.id==m.vehicle_id).with_for_update())
        other=g.db.scalar(select(Maintenance.id).where(Maintenance.vehicle_id==v.id,Maintenance.id!=m.id,Maintenance.status=='In progress').limit(1))
        active=g.db.scalar(select(Booking.id).where(Booking.vehicle_id==v.id,Booking.status=='Active').limit(1))
        if not other and not active: v.status='Available'
        g.db.commit(); return jsonify(message='Maintenance completed. Vehicle availability has been updated.')

    def report_data(period):
        now=local_now()
        midnight=now.replace(hour=0,minute=0,second=0,microsecond=0)
        if period=='daily': start=midnight
        elif period=='weekly': start=midnight-timedelta(days=midnight.weekday())
        elif period=='monthly': start=midnight.replace(day=1)
        elif period=='yearly': start=midnight.replace(month=1,day=1)
        else: abort(400,description='Choose daily, weekly, monthly, or yearly.')
        bookings=list(g.db.scalars(select(Booking).where(Booking.created_at>=start,Booking.created_at<=now)))
        paid=list(g.db.scalars(select(Payment).where(Payment.status=='Paid',Payment.paid_at>=start,Payment.paid_at<=now)))
        maintained=list(g.db.scalars(select(Maintenance).where(Maintenance.starts_at>=start,Maintenance.starts_at<=now)))
        payment_all=list(g.db.scalars(select(Payment).where(Payment.created_at>=start,Payment.created_at<=now)))
        counts={}
        for b in bookings:
            if b.status not in ('Active','Completed'): continue
            v=g.db.get(Vehicle,b.vehicle_id); name=f'{v.brand} {v.model}'; counts[name]=counts.get(name,0)+1
        totals={s:sum(1 for p in payment_all if p.status==s) for s in ('Pending','Paid','Failed','Refunded')}
        all_vehicles=list(g.db.scalars(select(Vehicle).where(Vehicle.is_deleted.is_(False))))
        return dict(period=period,start=start.isoformat(),end=now.isoformat(),
                    revenue=float(sum((p.amount for p in paid),Decimal('0'))),bookings=len(bookings),
                    maintenance_cost=float(sum((m.cost for m in maintained),Decimal('0'))),
                    overdue=g.db.scalar(select(func.count(Booking.id)).where(Booking.status=='Active',Booking.return_at<now)),
                    vehicle_counts={s:sum(1 for v in all_vehicles if v.status==s) for s in ('Available','Rented','Under Maintenance')},
                    payment_counts=totals,most_rented=[dict(name=n,count=c) for n,c in sorted(counts.items(),key=lambda x:(-x[1],x[0]))[:5]])

    @app.get('/api/admin/reports')
    @require('admin')
    def admin_reports():
        activate_due_maintenance(g.db)
        return jsonify(report_data(request.args.get('period','monthly')))

    @app.get('/api/admin/reports/export')
    @require('admin')
    def export_report():
        report=report_data(request.args.get('period','monthly'))
        out=io.StringIO(newline=''); writer=csv.writer(out)
        writer.writerow(['Roam report',report['period']]); writer.writerow(['Period start',report['start']]); writer.writerow(['Period end',report['end']]); writer.writerow([])
        writer.writerow(['Metric','Value']); writer.writerow(['Revenue (INR)',report['revenue']]); writer.writerow(['Bookings created',report['bookings']]); writer.writerow(['Maintenance expenses (INR)',report['maintenance_cost']]); writer.writerow(['Currently overdue',report['overdue']]); writer.writerow([])
        writer.writerow(['Vehicle status','Count'])
        for k,v in report['vehicle_counts'].items(): writer.writerow([k,v])
        writer.writerow([]); writer.writerow(['Payment status','Count'])
        for k,v in report['payment_counts'].items(): writer.writerow([k,v])
        writer.writerow([]); writer.writerow(['Most rented vehicle','Bookings'])
        for item in report['most_rented']:
            name=item['name']; name="'"+name if name[:1] in ('=','+','-','@') else name
            writer.writerow([name,item['count']])
        buf=io.BytesIO(out.getvalue().encode('utf-8-sig')); buf.seek(0)
        return send_file(buf,mimetype='text/csv',as_attachment=True,download_name=f'roam-{report["period"]}-report.csv')

    @app.get('/api/admin/messages')
    @require('admin')
    def admin_messages():
        return jsonify([dict(id=m.id,name=m.name,email=m.email,subject=m.subject,message=m.message,created_at=m.created_at.isoformat()) for m in g.db.scalars(select(ContactMessage).order_by(ContactMessage.created_at.desc()))])

    # Optional production serving after `npm run build` in frontend. Development uses Vite's proxy.
    @app.get('/')
    @app.get('/<path:path>')
    def frontend(path=''):
        if path.startswith(('api/', 'uploads/')): abort(404,description='Resource not found.')
        folder=ROOT/'frontend'/'dist'
        if not folder.exists():
            return 'Roam API is running. Start the frontend with npm run dev.',200
        file=(folder/path).resolve()
        if path and folder.resolve() in file.parents and file.is_file(): return send_from_directory(folder,path)
        return send_from_directory(folder,'index.html')

    return app


if __name__ == '__main__':
    create_app().run(host='0.0.0.0',port=int(os.environ.get('PORT','5000')),debug=False)
