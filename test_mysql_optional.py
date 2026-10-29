"""Optional destructive-data smoke test: point TEST_MYSQL_URL at a disposable empty database."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from backend.app import create_app
from backend.db import local_now


@pytest.mark.skipif(not os.environ.get('TEST_MYSQL_URL'), reason='Set TEST_MYSQL_URL to a disposable empty MySQL database')
def test_mysql_atomic_booking():
    app=create_app({'DATABASE_URL':os.environ['TEST_MYSQL_URL'],'TESTING':True,'SECRET_KEY':'mysql-test-only',
                    'PAYMENT_MODE':'mock','SEED_DEMO':True})
    barrier=Barrier(2)
    start=(local_now()+timedelta(days=110)).replace(hour=10,minute=0,second=0,microsecond=0)
    form={'vehicle_id':4,'pickup_at':start.isoformat(),'return_at':(start+timedelta(days=2)).isoformat(),
          'pickup_location':'Park Street, Kolkata','return_location':'Park Street, Kolkata'}
    def attempt(_):
        client=app.test_client();token=client.get('/api/session').json['csrf']
        login=client.post('/api/auth/login',json={'email':'customer@roam.test','password':'Customer123!'},headers={'X-CSRF-Token':token})
        assert login.status_code==200
        barrier.wait()
        response=client.post('/api/bookings',json=form,headers={'X-CSRF-Token':login.json['csrf']})
        return response.status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(attempt,range(2)))==[201,409]
    app.extensions['roam_engine'].dispose()
