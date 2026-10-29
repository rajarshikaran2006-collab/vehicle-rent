"""Idempotently install a small, internally consistent demo dataset."""
import os
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash
from sqlalchemy import select
from .db import create_database, default_database_url, local_now
from .models import Base, User, Customer, VehicleCategory, Branch, Vehicle, Booking, Payment, Review, Maintenance, Notification, VehicleReturn


def amounts(rate, days):
    q = lambda x: x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    rental = Decimal(rate) * days
    service = q(rental * Decimal('0.05'))
    tax = q((rental + service) * Decimal('0.05'))
    return rental, service, tax, rental + service + tax


def seed_data(db):
    if db.scalar(select(User.id).limit(1)):
        return False
    now = local_now()
    at9 = now.replace(hour=9, minute=0, second=0, microsecond=0)
    admin = User(name="Roam Admin", email="admin@roam.test", password_hash=generate_password_hash("Admin123!"), role="admin", created_at=now)
    staff = User(name="Roam Staff", email="staff@roam.test", password_hash=generate_password_hash("Staff123!"), role="staff", created_at=now)
    a = User(name="Aarav Sharma", email="customer@roam.test", password_hash=generate_password_hash("Customer123!"), role="customer", created_at=now - timedelta(days=120))
    b = User(name="Priya Sen", email="priya@roam.test", password_hash=generate_password_hash("Demo12345!"), role="customer", created_at=now - timedelta(days=100))
    c = User(name="Rohan Mehta", email="rohan@roam.test", password_hash=generate_password_hash("Demo12345!"), role="customer", created_at=now - timedelta(days=80))
    d = User(name="Ananya Roy", email="ananya@roam.test", password_hash=generate_password_hash("Demo12345!"), role="customer", created_at=now - timedelta(days=70))
    db.add_all([admin,staff,a,b,c,d]); db.flush()
    db.add_all([Customer(user_id=x.id, phone=p, license_number="") for x,p in [(a,"+91 98765 43210"),(b,"+91 98765 43211"),(c,"+91 98765 43212"),(d,"+91 98765 43213")]])
    cats = {name: VehicleCategory(name=name, slug=slug) for name,slug in [("SUV","suv"),("Sedan","sedan"),("Hatchback","hatchback"),("Electric","electric"),("Bike","bike"),("Luxury","luxury")]}
    branches = [Branch(name="Park Street", city="Kolkata", address="18 Park Street, Kolkata, West Bengal"), Branch(name="Salt Lake", city="Kolkata", address="Sector V, Salt Lake, Kolkata, West Bengal"), Branch(name="Indiranagar", city="Bengaluru", address="100 Feet Road, Indiranagar, Bengaluru"), Branch(name="Connaught Place", city="New Delhi", address="Connaught Place, New Delhi")]
    db.add_all(list(cats.values()) + branches); db.flush()
    def vehicle(cat, branch, brand, model, reg, year, fuel, transmission, seats, rate, image, featured=False, status="Available", desc=""):
        v = Vehicle(category_id=cats[cat].id, branch_id=branches[branch].id, brand=brand, model=model, registration=reg, year=year, fuel_type=fuel, transmission=transmission, seats=seats, daily_rate=Decimal(rate), image_url="/images/"+image+".jpg", featured=featured, status=status, description=desc or "A comfortable, well-maintained drive for city escapes and the open road.", created_at=now)
        db.add(v); db.flush(); return v
    v1=vehicle("SUV",0,"Hyundai","Creta SX","WB-01-AB-1201",2024,"Petrol","Automatic",5,2499,"car-suv",True,desc="Spacious, confident and made for the long way around. The perfect all-rounder for city trips and weekend escapes.")
    v2=vehicle("Sedan",0,"Honda","City ZX","WB-01-AB-1202",2023,"Petrol","Automatic",5,2199,"car-sedan",True,desc="A refined drive with room to stretch out. Enjoy effortless comfort on every journey.")
    v3=vehicle("Electric",1,"Tata","Nexon EV","WB-02-CD-1203",2024,"Electric","Automatic",5,2799,"car-electric",True,desc="Go a little further with a quieter, cleaner drive. Modern comfort meets electric ease.")
    v4=vehicle("Hatchback",0,"Maruti Suzuki","Baleno Alpha","WB-01-AB-1204",2023,"Petrol","Manual",5,1699,"car-hatchback",True,desc="Easy to park and even easier to love. Your nimble companion for exploring the city.")
    v5=vehicle("Luxury",2,"Mercedes-Benz","C-Class","KA-03-EF-1205",2024,"Petrol","Automatic",5,6499,"car-luxury",True,desc="Elevate the everyday with a beautifully refined premium sedan.")
    v6=vehicle("Bike",1,"Royal Enfield","Classic 350","WB-02-CD-1206",2023,"Petrol","Manual",2,899,"bike",True,desc="Two wheels, endless possibilities. A timeless ride for every kind of adventure.")
    v7=vehicle("SUV",2,"Kia","Seltos HTX","KA-03-EF-1207",2023,"Diesel","Automatic",5,2399,"car-suv",False,"Rented")
    v8=vehicle("Sedan",3,"Hyundai","Verna SX","DL-01-GH-1208",2024,"Petrol","Manual",5,1899,"car-sedan")
    v9=vehicle("Luxury",0,"BMW","3 Series","WB-01-AB-1209",2023,"Petrol","Automatic",5,5999,"car-luxury",False,"Under Maintenance")
    v10=vehicle("Electric",3,"MG","ZS EV","DL-01-GH-1210",2024,"Electric","Automatic",5,2999,"car-electric")
    def booking(user, v, start, end, status, payment_status="Paid", method="UPI"):
        days=(end-start).days
        rental, service, tax, total=amounts(v.daily_rate,days)
        bk=Booking(user_id=user.id, vehicle_id=v.id, pickup_at=start, return_at=end, pickup_location=branches[[x.id for x in branches].index(v.branch_id)].name+", "+branches[[x.id for x in branches].index(v.branch_id)].city, return_location=branches[[x.id for x in branches].index(v.branch_id)].name+", "+branches[[x.id for x in branches].index(v.branch_id)].city, days=days, daily_rate=v.daily_rate, rental_amount=rental, service_fee=service, tax=tax, total=total, status=status, created_at=min(start-timedelta(days=7),now-timedelta(hours=1)))
        db.add(bk); db.flush()
        db.add(Payment(booking_id=bk.id,amount=total, method=method,status=payment_status, provider="mock",reference=f"DEMO-{bk.id:04d}",paid_at=min(start-timedelta(days=6),now-timedelta(minutes=30)) if payment_status=="Paid" else None,created_at=min(start-timedelta(days=7),now-timedelta(hours=1))))
        if status=="Completed":
            db.add(VehicleReturn(booking_id=bk.id,actual_return_at=end,vehicle_condition="Good",late_fee=0,additional_charges=0,notes="Returned on time."))
        return bk
    past_a=booking(a,v2,at9-timedelta(days=34),at9-timedelta(days=31),"Completed")
    upcoming=booking(a,v1,at9+timedelta(days=8),at9+timedelta(days=11),"Confirmed")
    past_b=booking(b,v1,at9-timedelta(days=65),at9-timedelta(days=61),"Completed","Paid","Card")
    past_c=booking(c,v3,at9-timedelta(days=95),at9-timedelta(days=92),"Completed","Paid","Net banking")
    past_d=booking(d,v4,at9-timedelta(days=20),at9-timedelta(days=18),"Completed")
    active=booking(b,v7,at9-timedelta(days=5),at9-timedelta(days=1),"Active")
    pending=booking(c,v8,at9+timedelta(days=5),at9+timedelta(days=7),"Pending","Pending","Cash")
    db.add_all([
      Review(booking_id=past_a.id,user_id=a.id,vehicle_id=v2.id,rating=5,comment="The whole experience was effortless. The car was spotless and pickup was a breeze.",created_at=at9-timedelta(days=30)),
      Review(booking_id=past_b.id,user_id=b.id,vehicle_id=v1.id,rating=5,comment="A lovely car and the easiest booking process. We had the best weekend away!",created_at=at9-timedelta(days=60)),
      Review(booking_id=past_c.id,user_id=c.id,vehicle_id=v3.id,rating=5,comment="Great service from start to finish. I loved the freedom of exploring at my own pace.",created_at=at9-timedelta(days=91)),
      Review(booking_id=past_d.id,user_id=d.id,vehicle_id=v4.id,rating=4,comment="Super convenient and transparent pricing. Will definitely book again.",created_at=at9-timedelta(days=17)),
      Maintenance(vehicle_id=v9.id,description="Scheduled service and safety inspection",starts_at=at9-timedelta(days=1),ends_at=at9+timedelta(days=2),cost=Decimal('4500'),status="In progress"),
      Notification(user_id=a.id,title="Your trip is confirmed",message=f"Your Hyundai Creta is reserved for {upcoming.pickup_at.strftime('%d %b')}. Happy travels!",created_at=now-timedelta(days=1)),
    ])
    db.commit()
    return True


if __name__ == "__main__":
    import sys
    load_dotenv()
    url=os.environ.get("DATABASE_URL",default_database_url())
    engine, Session=create_database(url)
    if "--reset" in sys.argv:
        print("Resetting ALL tables and demo records.")
        Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    with Session() as db:
        print("Demo records created." if seed_data(db) else "Database already contains users; seed skipped.")
