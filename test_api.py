"""Integration tests for the database-backed Flask API (no external services)."""
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image
from sqlalchemy import select
from werkzeug.security import check_password_hash

from backend.app import create_app
from backend.db import local_now
from backend.models import Booking, Maintenance, Payment, User


@pytest.fixture
def app(tmp_path):
    app=create_app({'TESTING':True, 'SECRET_KEY':'test-secret-not-for-production',
                    'DATABASE_URL':'sqlite:///'+str(tmp_path/'test.db'),
                    'PAYMENT_MODE':'mock','SHOW_RESET_LINK':True,'SEED_DEMO':True,'UPLOAD_DIR':str(tmp_path/'uploads')})
    yield app
    app.extensions['roam_engine'].dispose()


class Client:
    def __init__(self, app):
        self.c=app.test_client()
        self.csrf=self.c.get('/api/session').get_json()['csrf']
    def request(self, method, path, data=None, **kwargs):
        headers=kwargs.pop('headers',{})
        headers['X-CSRF-Token']=self.csrf
        resp=self.c.open(path,method=method,json=data,headers=headers,**kwargs)
        body=resp.get_json(silent=True)
        if isinstance(body,dict) and body.get('csrf'): self.csrf=body['csrf']
        return resp
    def login(self,email='customer@roam.test',password='Customer123!'):
        resp=self.request('POST','/api/auth/login',{'email':email,'password':password})
        assert resp.status_code==200,resp.get_json()
        return resp.get_json()['user']


def trip(vehicle=4,days_ahead=40,duration=2):
    start=(local_now()+timedelta(days=days_ahead)).replace(hour=10,minute=0,second=0,microsecond=0)
    end=start+timedelta(days=duration)
    return {'vehicle_id':vehicle,'pickup_at':start.isoformat(),'return_at':end.isoformat(),
            'pickup_location':'Park Street, Kolkata','return_location':'Park Street, Kolkata'}


def create(c, form=None):
    response=c.request('POST','/api/bookings',form or trip())
    assert response.status_code==201,response.get_json()
    return response.get_json()['booking']


def test_auth_csrf_ownership_and_password_hash(app):
    c=Client(app)
    assert c.c.post('/api/auth/login',json={'email':'customer@roam.test','password':'Customer123!'}).status_code==403
    registered=c.request('POST','/api/auth/register',{'name':'Test Traveler','email':'test@example.com','password':'Secure123!','phone':'1234567890'})
    assert registered.status_code==201
    assert registered.get_json()['user']['role']=='customer'
    assert c.c.get('/api/admin/summary').status_code==403
    # Seed booking belongs to someone else; do not leak that it exists.
    assert c.c.get('/api/bookings/1').status_code==404
    assert c.c.get('/api/bookings/1/receipt').status_code==404
    with app.extensions['roam_session']() as db:
        u=db.scalar(select(User).where(User.email=='test@example.com'))
        assert u.password_hash!='Secure123!' and check_password_hash(u.password_hash,'Secure123!')
    assert c.request('PUT','/api/profile',{'name':'X','phone':'','license_number':''}).status_code==400


def test_quote_booking_overlap_adjacent_and_server_price(app):
    c=Client(app); c.login(); form=trip()
    quote=c.request('POST','/api/quote',form).get_json()
    assert quote['available'] and quote['days']==2
    # Untrusted client-supplied prices are ignored.
    booked=create(c,{**form,'total':1,'daily_rate':1})
    assert booked['total']==quote['total'] and booked['total']>1
    assert c.request('POST','/api/bookings',form).status_code==409
    adjacent={**form,'pickup_at':form['return_at'],'return_at':(local_now()+timedelta(days=44)).replace(hour=10,minute=0,second=0,microsecond=0).isoformat()}
    assert c.request('POST','/api/bookings',adjacent).status_code==201
    q=c.request('POST','/api/quote',form).get_json()
    assert not q['available']
    assert c.request('POST','/api/quote',{**form,'return_at':form['pickup_at']}).status_code==400


def test_mock_payment_receipt_and_cancel(app):
    c=Client(app);c.login(); b=create(c)
    paid=c.request('POST',f"/api/bookings/{b['id']}/payment",{'method':'Card'})
    assert paid.status_code==200
    b=paid.get_json()['booking']
    assert b['status']=='Confirmed' and b['payments'][0]['status']=='Paid' and b['hold_expires_at'] is None
    receipt=c.c.get(f"/api/bookings/{b['id']}/receipt")
    assert receipt.status_code==200 and receipt.data.startswith(b'%PDF')
    cancelled=c.request('POST',f"/api/bookings/{b['id']}/cancel")
    assert cancelled.status_code==200
    assert cancelled.get_json()['booking']['payments'][0]['status']=='Refunded'
    assert c.request('POST',f"/api/bookings/{b['id']}/payment",{'method':'UPI'}).status_code==400


def test_expired_holds_release_dates_and_cannot_be_paid(app):
    c=Client(app);c.login(); form=trip(); b=create(c,form)
    with app.extensions['roam_session']() as db:
        db.get(Booking,b['id']).hold_expires_at=local_now()-timedelta(minutes=1);db.commit()
    old=c.c.get(f"/api/bookings/{b['id']}").get_json()
    assert old['status']=='Cancelled'
    assert c.request('POST',f"/api/bookings/{b['id']}/payment",{'method':'UPI'}).status_code==400
    assert c.request('POST','/api/bookings',form).status_code==201


def test_concurrent_bookings_cannot_overlap(app):
    barrier=Barrier(2); form=trip(vehicle=10)
    def attempt(_):
        c=Client(app);c.login();barrier.wait()
        return c.request('POST','/api/bookings',form).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(attempt,range(2)))
    assert sorted(statuses)==[201,409]


def test_return_fees_maintenance_and_staff_permissions(app):
    staff=Client(app);staff.login('staff@roam.test','Staff123!')
    assert staff.c.get('/api/admin/customers').status_code==403
    staff_summary=staff.c.get('/api/admin/summary').get_json()
    assert staff_summary['metrics']['revenue'] is None and staff_summary['revenue_chart']==[]
    active=staff.c.get('/api/admin/returns').get_json();assert len(active)==1 and active[0]['overdue']
    b=active[0]
    returned=staff.request('POST',f"/api/admin/returns/{b['id']}",{'actual_return_at':local_now().isoformat(), 'condition':'Good','additional_charges':500,'notes':'Test inspection'})
    assert returned.status_code==200,returned.get_json()
    r=returned.get_json();assert r['late_fee']>0 and r['booking']['status']=='Completed'
    assert r['booking']['outstanding']==r['late_fee']+500
    vehicle_id=b['vehicle']['id']
    assert staff.c.get(f'/api/vehicles/{vehicle_id}').get_json()['status']=='Available'
    now=local_now()
    service=staff.request('POST','/api/admin/maintenance',{'vehicle_id':vehicle_id,'description':'Safety service','starts_at':now.isoformat(),'ends_at':(now+timedelta(days=2)).isoformat(),'cost':1500})
    assert service.status_code==201,service.get_json()
    q=staff.request('POST','/api/quote',trip(vehicle=vehicle_id)).get_json();assert not q['available']
    done=staff.request('POST',f"/api/admin/maintenance/{service.get_json()['id']}/complete",{'cost':1600})
    assert done.status_code==200
    assert staff.c.get(f'/api/vehicles/{vehicle_id}').get_json()['status']=='Available'


def test_cash_payment_admin_reports_and_upload(app):
    c=Client(app);c.login();b=create(c)
    cash=c.request('POST',f"/api/bookings/{b['id']}/payment",{'method':'Cash'}).get_json()['booking']
    assert cash['status']=='Confirmed' and cash['payments'][0]['status']=='Pending'
    a=Client(app);a.login('admin@roam.test','Admin123!')
    p=cash['payments'][0]
    assert a.request('PATCH',f"/api/admin/payments/{p['id']}",{'status':'Paid'}).status_code==200
    report=a.c.get('/api/admin/reports?period=yearly')
    assert report.status_code==200 and report.get_json()['revenue']>=cash['total']
    assert a.c.get('/api/admin/reports/export?period=yearly').status_code==200
    f=io.BytesIO();Image.new('RGB',(8,8),'blue').save(f,format='PNG');f.seek(0)
    upload=a.c.post('/api/admin/uploads',data={'image':(f,'test.png')},headers={'X-CSRF-Token':a.csrf})
    assert upload.status_code==201 and upload.get_json()['url'].startswith('/uploads/')
    assert a.c.get(upload.get_json()['url']).status_code==200


def test_password_reset_one_time_link(app):
    c=Client(app)
    resp=c.request('POST','/api/auth/forgot',{'email':'customer@roam.test'}).get_json()
    token=parse_qs(urlparse(resp['demo_reset_link']).query)['token'][0]
    assert c.request('POST','/api/auth/reset',{'token':token,'password':'NewPassword123!'}).status_code==200
    assert c.request('POST','/api/auth/reset',{'token':token,'password':'AnotherPassword!'}).status_code==400
    c.login('customer@roam.test','NewPassword123!')


def test_logout_revokes_stolen_session_cookie(app):
    c=Client(app);c.login()
    old_cookie=c.c.get_cookie('session').value
    assert c.request('POST','/api/auth/logout').status_code==200
    stolen=app.test_client();stolen.set_cookie('session',old_cookie)
    assert stolen.get('/api/bookings').status_code==401


def test_cancellation_policy_and_maintenance_conflict(app):
    c=Client(app);c.login();form=trip();b=create(c,form)
    admin=Client(app);admin.login('admin@roam.test','Admin123!')
    maintenance=admin.request('POST','/api/admin/maintenance',{'vehicle_id':form['vehicle_id'],
        'description':'Overlapping service','starts_at':form['pickup_at'],'ends_at':form['return_at'],'cost':100})
    assert maintenance.status_code==409
    with app.extensions['roam_session']() as db:
        booking=db.get(Booking,b['id']);booking.pickup_at=local_now()+timedelta(hours=23);db.commit()
    assert c.request('POST',f"/api/bookings/{b['id']}/cancel").status_code==400


def test_razorpay_test_order_signature_and_refund(app,monkeypatch):
    import hashlib, hmac
    app.config.update(PAYMENT_MODE='razorpay_test', RAZORPAY_KEY_ID='rzp_test_demo', RAZORPAY_KEY_SECRET='testsecret')
    class Response:
        ok=True
        def __init__(self,data): self.data=data
        def json(self): return self.data
    def fake_post(url,**kwargs):
        if url.endswith('/orders'): return Response({'id':'order_test_123'})
        if url.endswith('/refund'): return Response({'id':'rfnd_test_123','status':'pending'})
        raise AssertionError(url)
    capture_status=['authorized']
    def fake_get(url,**kwargs):
        if url.endswith('/payments/pay_test123'):
            return Response({'id':'pay_test123','order_id':'order_test_123','amount':expected_amount,'currency':'INR','status':capture_status[0]})
        assert url.endswith('/rfnd_test_123')
        return Response({'id':'rfnd_test_123','status':'processed'})
    monkeypatch.setattr('backend.app.requests.post',fake_post)
    monkeypatch.setattr('backend.app.requests.get',fake_get)
    c=Client(app);c.login();b=create(c)
    order=c.request('POST',f"/api/bookings/{b['id']}/payment",{'method':'UPI'})
    assert order.status_code==200 and order.get_json()['checkout']['order_id']=='order_test_123'
    p=order.get_json()['checkout']['payment_id']
    expected_amount=order.get_json()['checkout']['amount']
    bad=c.request('POST',f'/api/payments/{p}/verify',{'razorpay_order_id':'order_test_123','razorpay_payment_id':'pay_test123','razorpay_signature':'fake'})
    assert bad.status_code==400
    sig=hmac.new(b'testsecret',b'order_test_123|pay_test123',hashlib.sha256).hexdigest()
    not_captured=c.request('POST',f'/api/payments/{p}/verify',{'razorpay_order_id':'order_test_123','razorpay_payment_id':'pay_test123','razorpay_signature':sig})
    assert not_captured.status_code==409
    capture_status[0]='captured'
    good=c.request('POST',f'/api/payments/{p}/verify',{'razorpay_order_id':'order_test_123','razorpay_payment_id':'pay_test123','razorpay_signature':sig})
    assert good.status_code==200 and good.get_json()['booking']['payments'][0]['status']=='Paid'
    a=Client(app);a.login('admin@roam.test','Admin123!')
    pending=a.request('PATCH',f'/api/admin/payments/{p}',{'status':'Refunded'})
    assert pending.status_code==200 and pending.get_json()['payment']['status']=='Paid'
    completed=a.request('PATCH',f'/api/admin/payments/{p}',{'status':'Refunded'})
    assert completed.status_code==200 and completed.get_json()['payment']['status']=='Refunded'


def test_live_payment_keys_are_refused(app):
    app.config.update(PAYMENT_MODE='razorpay_test', RAZORPAY_KEY_ID='rzp_live_forbidden', RAZORPAY_KEY_SECRET='secret')
    c=Client(app);c.login();b=create(c)
    r=c.request('POST',f"/api/bookings/{b['id']}/payment",{'method':'Card'})
    assert r.status_code==503 and 'Live keys are not accepted' in r.get_json()['error']


def test_production_configuration_rejects_demo_settings(tmp_path):
    with pytest.raises(RuntimeError, match='SECRET_KEY'):
        create_app({'APP_ENV':'production', 'SECRET_KEY':'short', 'DATABASE_URL':'sqlite:///'+str(tmp_path/'prod.db')})
    with pytest.raises(RuntimeError, match='secure cookies'):
        create_app({'APP_ENV':'production', 'SECRET_KEY':'x'*48, 'DATABASE_URL':'sqlite:///'+str(tmp_path/'prod.db')})


def test_due_maintenance_blocks_future_booking_until_completed(app):
    admin=Client(app); admin.login('admin@roam.test','Admin123!')
    now=local_now()
    service=admin.request('POST','/api/admin/maintenance',{'vehicle_id':10,'description':'Future service',
        'starts_at':(now+timedelta(days=2)).isoformat(),'ends_at':(now+timedelta(days=3)).isoformat(),'cost':0})
    assert service.status_code==201
    with app.extensions['roam_session']() as db:
        m=db.get(Maintenance,service.get_json()['id'])
        m.starts_at=now-timedelta(days=2); m.ends_at=now-timedelta(days=1); db.commit()
    traveler=Client(app); traveler.login()
    quote=traveler.request('POST','/api/quote',trip(vehicle=10)).get_json()
    assert not quote['available']
    assert traveler.c.get('/api/vehicles/10').get_json()['status']=='Under Maintenance'
    assert admin.request('POST',f"/api/admin/maintenance/{m.id}/complete",{'cost':0}).status_code==200
    assert traveler.request('POST','/api/quote',trip(vehicle=10)).get_json()['available']
