# 🏘️ Local Link — Complete Platform

## Quick Start
```bash
pip install flask werkzeug pillow
python app.py
# Open: http://localhost:5000
```

## Demo Accounts
| Role | Email | Password |
|------|-------|----------|
| Admin | admin@locallink.com | admin123 |
| Customer | demo@locallink.com | demo123 |
| Provider | provider@locallink.com | provider123 |

## New Features Added
- 🛡️ **LocalLink Trust Index** — Dynamic trust score (0-10) per provider
- 💬 **Local Feed** — Community bulletin board with categories and likes
- 🚀 **Onboarding Guide** — Step-by-step guide for new residents
- 🚨 **Emergency Contacts** — Always-visible emergency numbers
- 📊 **Enhanced Admin Dashboard** — Revenue tracking, featured providers toggle
- 🤖 **AI Recommendations** — ML-based provider suggestions
- 🔥 **Emergency Bookings** — 20% commission, priority flag
- 💰 **Commission Tracking** — 15% standard, 20% emergency

## All Pages
- `/` — Home/Landing
- `/login`, `/register` — Auth
- `/dashboard` — Role-based (customer/provider/admin)
- `/search` — Search with trust score sorting
- `/provider/<id>` — Provider detail + booking modals
- `/feed` — Local community feed
- `/onboarding` — New resident guide
- `/recommendations` — AI-powered picks
- `/provider/analytics` — Provider business insights
- `/chat/<booking_id>` — Booking chat
- `/payment/<id>` — Razorpay + demo payment

## Tech Stack
- **Backend:** Flask 3.0, SQLite, Python 3
- **Frontend:** Bootstrap 5, Syne + DM Sans fonts
- **Auth:** Werkzeug password hashing + Flask sessions
- **Payments:** Razorpay-ready (demo mode included)
