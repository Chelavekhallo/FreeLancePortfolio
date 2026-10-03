# Telegram Reminder Bot

A powerful Telegram bot that lets users create reminders in natural language. 
Built with **aiogram 3** and **APScheduler**.

## 🎯 What it does

- Create reminders like `через 10 мин Позвонить маме`
- Supports seconds, minutes, hours, days (`1ч30м`, `2 дня`)
- List active reminders (`/list`)
- Cancel any reminder by ID (`/cancel <id>`)
- Persistent scheduling with APScheduler

## 🧠 How it works

1. User sends a message starting with `через`
2. Bot parses the time expression (regex + unit map)
3. Schedules a one-time job with APScheduler
4. At the right moment, bot sends the reminder
5. User can list or cancel reminders anytime

## 📦 Installation

```bash
git clone https://github.com/Chelavekhallo/FreeLancePortfolio.git
cd FreeLancePortfolio
pip install -r requirements.txt
