"""
Demo Data Script for Local Link
Run this after starting the app once to populate with sample data
"""

import sqlite3
from werkzeug.security import generate_password_hash
from datetime import datetime, timedelta

def create_demo_data():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    print("Creating demo data...")
    
    # Demo passwords (all use 'demo123')
    demo_password = generate_password_hash('demo123')
    
    # 1. Create Admin
    try:
        cursor.execute('''
            INSERT INTO users (name, email, password, role, city, locality)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', ('Admin User', 'admin@locallink.com', demo_password, 'admin', 'Mumbai', 'BKC'))
        admin_id = cursor.lastrowid
        print("✓ Admin created: admin@locallink.com / demo123")
    except:
        print("✗ Admin already exists")
    
    # 2. Create Customers
    customers = [
        ('Rahul Sharma', 'rahul@example.com', 'Mumbai', 'Andheri West'),
        ('Priya Patel', 'priya@example.com', 'Mumbai', 'Bandra'),
        ('Amit Kumar', 'amit@example.com', 'Mumbai', 'Powai'),
    ]
    
    customer_ids = []
    for name, email, city, locality in customers:
        try:
            cursor.execute('''
                INSERT INTO users (name, email, password, role, city, locality)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, email, demo_password, 'customer', city, locality))
            customer_ids.append(cursor.lastrowid)
            print(f"✓ Customer created: {email} / demo123")
        except:
            print(f"✗ Customer {email} already exists")
    
    # 3. Create Service Providers
    providers_data = [
        ('Quick Fix Plumbers', 'plumber@example.com', 'Plumbing', '9876543210', 'Mumbai', 'Andheri West', 5, 19.1197, 72.8464),
        ('Bright Electricians', 'electric@example.com', 'Electrical', '9876543211', 'Mumbai', 'Bandra', 8, 19.0596, 72.8295),
        ('Expert Mechanics', 'mechanic@example.com', 'Mechanics', '9876543212', 'Mumbai', 'Powai', 6, 19.1176, 72.9060),
        ('Sparkle Cleaners', 'cleaner@example.com', 'House Cleaning', '9876543213', 'Mumbai', 'Andheri West', 3, 19.1136, 72.8697),
        ('Beauty Hub', 'beauty@example.com', 'Beauty & Salon', '9876543214', 'Mumbai', 'Bandra', 7, 19.0544, 72.8408),
        ('Fix It All', 'repair@example.com', 'Home Repair', '9876543215', 'Mumbai', 'Powai', 10, 19.1259, 72.9119),
    ]
    
    provider_ids = []
    for name, email, service_type, phone, city, locality, exp, lat, lng in providers_data:
        try:
            # Create provider user
            cursor.execute('''
                INSERT INTO users (name, email, password, role, city, locality)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, email, demo_password, 'provider', city, locality))
            user_id = cursor.lastrowid
            
            # Create provider profile (approved)
            cursor.execute('''
                INSERT INTO service_providers 
                (user_id, name, service_type, phone, city, locality, experience, latitude, longitude, approved)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ''', (user_id, name, service_type, phone, city, locality, exp, lat, lng))
            provider_ids.append(cursor.lastrowid)
            print(f"✓ Provider created: {email} / demo123")
        except:
            print(f"✗ Provider {email} already exists")
    
    # 4. Create Services for Providers
    services_data = [
        # Quick Fix Plumbers
        (1, 'Pipe Repair', 500, 'Fix leaking and broken pipes'),
        (1, 'Tap Installation', 300, 'Install new taps and faucets'),
        (1, 'Drain Cleaning', 800, 'Clear blocked drains'),
        # Bright Electricians
        (2, 'Wiring Repair', 1000, 'Fix electrical wiring issues'),
        (2, 'Fan Installation', 400, 'Install ceiling and wall fans'),
        (2, 'Light Fitting', 300, 'Install lights and fixtures'),
        # Expert Mechanics
        (3, 'Car Service', 2000, 'Complete car servicing'),
        (3, 'Bike Repair', 800, 'Two-wheeler repairs'),
        # Sparkle Cleaners
        (4, 'House Cleaning', 1500, 'Complete house cleaning'),
        (4, 'Deep Cleaning', 3000, 'Thorough deep cleaning'),
        # Beauty Hub
        (5, 'Haircut', 500, 'Professional haircut'),
        (5, 'Facial', 1000, 'Facial treatment'),
        # Fix It All
        (6, 'Furniture Repair', 800, 'Fix broken furniture'),
        (6, 'Wall Painting', 5000, 'Paint walls'),
    ]
    
    for provider_idx, service_name, price, desc in services_data:
        if provider_idx <= len(provider_ids):
            try:
                cursor.execute('''
                    INSERT INTO services (provider_id, service_name, price, description)
                    VALUES (?, ?, ?, ?)
                ''', (provider_ids[provider_idx-1], service_name, price, desc))
            except:
                pass
    print("✓ Services created")
    
    # 5. Create Sample Bookings
    if customer_ids and provider_ids:
        bookings_data = [
            (customer_ids[0] if customer_ids else 1, provider_ids[0] if provider_ids else 1, 1, '2024-02-15 10:00', 'Completed', 'Completed', 500.0, 'DEMO_PAY_001', 'DEMO_ORDER_001'),
            (customer_ids[1] if len(customer_ids) > 1 else 1, provider_ids[1] if len(provider_ids) > 1 else 1, 4, '2024-02-16 14:00', 'Accepted', 'Completed', 1000.0, 'DEMO_PAY_002', 'DEMO_ORDER_002'),
            (customer_ids[2] if len(customer_ids) > 2 else 1, provider_ids[2] if len(provider_ids) > 2 else 1, 7, '2024-02-17 11:00', 'Pending', 'Pending', 2000.0, None, None),
        ]
        
        for user_id, provider_id, service_id, date, status, payment_status, amount, payment_id, order_id in bookings_data:
            try:
                cursor.execute('''
                    INSERT INTO bookings (user_id, provider_id, service_id, booking_date, status, payment_status, payment_amount, payment_id, order_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, provider_id, service_id, date, status, payment_status, amount, payment_id, order_id))
            except:
                pass
        print("✓ Sample bookings created with payment info")
    
    # 6. Create Sample Reviews
    if customer_ids and provider_ids:
        reviews_data = [
            (customer_ids[0] if customer_ids else 1, provider_ids[0] if provider_ids else 1, 5, 'Excellent service! Very professional and quick.'),
            (customer_ids[1] if len(customer_ids) > 1 else 1, provider_ids[1] if len(provider_ids) > 1 else 1, 4, 'Good work, arrived on time.'),
            (customer_ids[0] if customer_ids else 1, provider_ids[2] if len(provider_ids) > 2 else 1, 5, 'Highly recommended! Great quality.'),
        ]
        
        for user_id, provider_id, rating, comment in reviews_data:
            try:
                cursor.execute('''
                    INSERT INTO reviews (user_id, provider_id, rating, comment)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, provider_id, rating, comment))
            except:
                pass
        print("✓ Sample reviews created")
    
    conn.commit()
    conn.close()
    
    print("\n" + "="*50)
    print("DEMO DATA CREATED SUCCESSFULLY!")
    print("="*50)
    print("\nDemo Accounts:")
    print("-" * 50)
    print("ADMIN:")
    print("  Email: admin@locallink.com")
    print("  Password: demo123")
    print("\nCUSTOMERS:")
    print("  Email: rahul@example.com / demo123")
    print("  Email: priya@example.com / demo123")
    print("  Email: amit@example.com / demo123")
    print("\nPROVIDERS (All Approved):")
    print("  Email: plumber@example.com / demo123")
    print("  Email: electric@example.com / demo123")
    print("  Email: mechanic@example.com / demo123")
    print("  Email: cleaner@example.com / demo123")
    print("  Email: beauty@example.com / demo123")
    print("  Email: repair@example.com / demo123")
    print("-" * 50)
    print("\nYou can now login with any of these accounts!")
    print("Visit: http://localhost:5000")
    print("="*50)

if __name__ == '__main__':
    create_demo_data()
