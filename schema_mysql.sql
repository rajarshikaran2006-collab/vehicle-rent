-- Roam Vehicle Rental: MySQL 8.0.16+ relational schema.
-- Run after creating/selecting a database, or allow the Flask app to create the same tables.
-- All tables use InnoDB so transaction row locks enforce booking availability.
SET NAMES utf8mb4;

CREATE TABLE branches (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(100) NOT NULL, 
	city VARCHAR(80) NOT NULL, 
	address VARCHAR(255) NOT NULL, 
	PRIMARY KEY (id)
)ENGINE=InnoDB CHARSET=utf8mb4;
CREATE INDEX ix_branches_city ON branches (city);

CREATE TABLE contact_messages (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(100) NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	subject VARCHAR(150) NOT NULL, 
	message TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE users (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(100) NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	`role` VARCHAR(16) NOT NULL, 
	active BOOL NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_user_role CHECK (role IN ('customer', 'staff', 'admin'))
)ENGINE=InnoDB CHARSET=utf8mb4;
CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE TABLE vehicle_categories (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(50) NOT NULL, 
	slug VARCHAR(50) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name), 
	UNIQUE (slug)
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE auth_sessions (
	token_hash VARCHAR(64) NOT NULL, 
	user_id INTEGER NOT NULL, 
	expires_at DATETIME NOT NULL, 
	PRIMARY KEY (token_hash), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4;
CREATE INDEX ix_auth_sessions_expires_at ON auth_sessions (expires_at);
CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);

CREATE TABLE customers (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER NOT NULL, 
	phone VARCHAR(24) NOT NULL, 
	license_number VARCHAR(50) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (user_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4;

CREATE TABLE notifications (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER NOT NULL, 
	title VARCHAR(120) NOT NULL, 
	message VARCHAR(500) NOT NULL, 
	is_read BOOL NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
)ENGINE=InnoDB CHARSET=utf8mb4;
CREATE INDEX ix_notifications_user_id ON notifications (user_id);

CREATE TABLE vehicles (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	category_id INTEGER NOT NULL, 
	branch_id INTEGER NOT NULL, 
	brand VARCHAR(80) NOT NULL, 
	model VARCHAR(100) NOT NULL, 
	registration VARCHAR(32) NOT NULL, 
	year INTEGER NOT NULL, 
	fuel_type VARCHAR(24) NOT NULL, 
	transmission VARCHAR(24) NOT NULL, 
	seats INTEGER NOT NULL, 
	daily_rate NUMERIC(10, 2) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	image_url VARCHAR(255) NOT NULL, 
	description TEXT NOT NULL, 
	featured BOOL NOT NULL, 
	is_deleted BOOL NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_vehicle_rate CHECK (daily_rate > 0), 
	CONSTRAINT ck_vehicle_seats CHECK (seats > 0 AND seats <= 60), 
	CONSTRAINT ck_vehicle_status CHECK (status IN ('Available', 'Rented', 'Under Maintenance')), 
	FOREIGN KEY(category_id) REFERENCES vehicle_categories (id), 
	FOREIGN KEY(branch_id) REFERENCES branches (id), 
	UNIQUE (registration)
)ENGINE=InnoDB CHARSET=utf8mb4;
CREATE INDEX ix_vehicles_branch_id ON vehicles (branch_id);
CREATE INDEX ix_vehicles_brand ON vehicles (brand);
CREATE INDEX ix_vehicles_category_id ON vehicles (category_id);
CREATE INDEX ix_vehicles_model ON vehicles (model);
CREATE INDEX ix_vehicles_status ON vehicles (status);

CREATE TABLE bookings (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER NOT NULL, 
	vehicle_id INTEGER NOT NULL, 
	pickup_at DATETIME NOT NULL, 
	return_at DATETIME NOT NULL, 
	pickup_location VARCHAR(180) NOT NULL, 
	return_location VARCHAR(180) NOT NULL, 
	days INTEGER NOT NULL, 
	daily_rate NUMERIC(10, 2) NOT NULL, 
	rental_amount NUMERIC(10, 2) NOT NULL, 
	service_fee NUMERIC(10, 2) NOT NULL, 
	tax NUMERIC(10, 2) NOT NULL, 
	late_fee NUMERIC(10, 2) NOT NULL, 
	extra_charge NUMERIC(10, 2) NOT NULL, 
	total NUMERIC(10, 2) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	hold_expires_at DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_booking_dates CHECK (return_at > pickup_at), 
	CONSTRAINT ck_booking_amount CHECK (days > 0 AND total >= 0), 
	CONSTRAINT ck_booking_status CHECK (status IN ('Pending', 'Confirmed', 'Active', 'Completed', 'Cancelled', 'Rejected')), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(vehicle_id) REFERENCES vehicles (id)
)ENGINE=InnoDB CHARSET=utf8mb4;
CREATE INDEX ix_booking_vehicle_period ON bookings (vehicle_id, pickup_at, return_at, status);
CREATE INDEX ix_bookings_hold_expires_at ON bookings (hold_expires_at);
CREATE INDEX ix_bookings_status ON bookings (status);
CREATE INDEX ix_bookings_user_id ON bookings (user_id);
CREATE INDEX ix_bookings_vehicle_id ON bookings (vehicle_id);

CREATE TABLE maintenance (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	vehicle_id INTEGER NOT NULL, 
	description VARCHAR(255) NOT NULL, 
	starts_at DATETIME NOT NULL, 
	ends_at DATETIME, 
	cost NUMERIC(10, 2) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	completed_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_maintenance_cost CHECK (cost >= 0), 
	CONSTRAINT ck_maintenance_status CHECK (status IN ('Scheduled', 'In progress', 'Completed')), 
	CONSTRAINT ck_maintenance_dates CHECK (ends_at IS NULL OR ends_at > starts_at), 
	FOREIGN KEY(vehicle_id) REFERENCES vehicles (id)
)ENGINE=InnoDB CHARSET=utf8mb4;
CREATE INDEX ix_maintenance_status ON maintenance (status);
CREATE INDEX ix_maintenance_vehicle_id ON maintenance (vehicle_id);

CREATE TABLE payments (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	booking_id INTEGER NOT NULL, 
	amount NUMERIC(10, 2) NOT NULL, 
	method VARCHAR(30) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	provider VARCHAR(30) NOT NULL, 
	reference VARCHAR(100) NOT NULL, 
	gateway_order_id VARCHAR(100), 
	gateway_payment_id VARCHAR(100), 
	refund_reference VARCHAR(100), 
	paid_at DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_payment_amount CHECK (amount >= 0), 
	CONSTRAINT ck_payment_status CHECK (status IN ('Pending', 'Paid', 'Failed', 'Refunded')), 
	FOREIGN KEY(booking_id) REFERENCES bookings (id), 
	UNIQUE (gateway_order_id), 
	UNIQUE (gateway_payment_id), 
	UNIQUE (refund_reference)
)ENGINE=InnoDB CHARSET=utf8mb4;
CREATE INDEX ix_payments_booking_id ON payments (booking_id);
CREATE INDEX ix_payments_status ON payments (status);

CREATE TABLE reviews (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	booking_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	vehicle_id INTEGER NOT NULL, 
	rating INTEGER NOT NULL, 
	comment VARCHAR(600) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_review_rating CHECK (rating BETWEEN 1 AND 5), 
	UNIQUE (booking_id), 
	FOREIGN KEY(booking_id) REFERENCES bookings (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(vehicle_id) REFERENCES vehicles (id)
)ENGINE=InnoDB CHARSET=utf8mb4;
CREATE INDEX ix_reviews_user_id ON reviews (user_id);
CREATE INDEX ix_reviews_vehicle_id ON reviews (vehicle_id);

CREATE TABLE vehicle_returns (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	booking_id INTEGER NOT NULL, 
	actual_return_at DATETIME NOT NULL, 
	vehicle_condition VARCHAR(100) NOT NULL, 
	late_fee NUMERIC(10, 2) NOT NULL, 
	additional_charges NUMERIC(10, 2) NOT NULL, 
	notes TEXT NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (booking_id), 
	FOREIGN KEY(booking_id) REFERENCES bookings (id)
)ENGINE=InnoDB CHARSET=utf8mb4;

