import asyncio
import json
import logging
import os
from datetime import datetime
from io import BytesIO

import aiosqlite
import aiohttp
import matplotlib.pyplot as plt
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    BufferedInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
from dotenv import load_dotenv

load_dotenv()

# ==================== SOZLAMALAR ====================
TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "alerts.db")          # Railway da /data/alerts.db
MINI_APP_URL = os.getenv("MINI_APP_URL", "")         # Mini App URL (masalan https://xxx.vercel.app)

bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)


# ==================== DATABASE ====================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                coin_id TEXT NOT NULL,
                coin_name TEXT NOT NULL,
                target_price REAL NOT NULL,
                direction TEXT NOT NULL,          -- above / below
                created_at TEXT
            )
        """)
        await db.commit()


async def add_alert(user_id: int, coin_id: str, coin_name: str, target: float, direction: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO alerts 
               (user_id, coin_id, coin_name, target_price, direction, created_at) 
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, coin_id, coin_name, target, direction, datetime.utcnow().isoformat())
        )
        await db.commit()


async def get_user_alerts(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, coin_name, target_price, direction FROM alerts WHERE user_id = ?",
            (user_id,)
        )
        return await cursor.fetchall()


async def delete_alert(alert_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM alerts WHERE id = ? AND user_id = ?",
            (alert_id, user_id)
        )
        await db.commit()


async def get_all_alerts():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, coin_id, coin_name, target_price, direction FROM alerts"
        )
        return await cursor.fetchall()


# ==================== COINGECKO API ====================
async def search_coin(query: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://api.coingecko.com/api/v3/search",
            params={"query": query}
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("coins"):
                    return data["coins"][0]
    return None


async def get_price(coin_id: str):
    params = {
        "ids": coin_id,
        "vs_currencies": "usd",
        "include_24hr_change": "true"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params=params
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get(coin_id)
    return None


async def get_chart_data(coin_id: str, days: int = 7):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": days}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                return await resp.json()
    return None


def create_chart(prices: list, coin_name: str) -> BytesIO:
    times = [datetime.fromtimestamp(p[0] / 1000) for p in prices]
    values = [p[1] for p in prices]

    plt.figure(figsize=(10, 5))
    plt.plot(times, values, color="#00d4aa", linewidth=2.2)
    plt.fill_between(times, values, alpha=0.25, color="#00d4aa")
    plt.title(f"{coin_name} — 7 kunlik narx grafigi", fontsize=14, pad=12)
    plt.xlabel("Sana")
    plt.ylabel("USD")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    buf.seek(0)
    plt.close()
    return buf


# ==================== HANDLERS ====================
@dp.message(Command("start"))
async def start_handler(message: Message):
    text = (
        "👋 **Kriptovalyuta Bot**\n\n"
        "📌 Asosiy buyruqlar:\n"
        "/price btc — narx + 24h o‘zgarish\n"
        "/chart eth — 7 kunlik grafik\n"
        "/alert btc above 95000 — oshganda xabar\n"
        "/alert sol below 140 — tushganda xabar\n"
        "/myalerts — mening alertlarim\n"
        "/delalert 1 — alertni o‘chirish\n"
        "/top — Top 10\n"
        "/app — Mini Appni ochish"
    )

    keyboard = None
    if MINI_APP_URL:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="📱 Mini Appni ochish",
                web_app=WebAppInfo(url=MINI_APP_URL)
            )]
        ])

    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)


@dp.message(Command("app"))
async def app_handler(message: Message):
    if not MINI_APP_URL:
        await message.answer("Mini App hali sozlanmagan. Admin bilan bog‘laning.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📱 Mini Appni ochish",
            web_app=WebAppInfo(url=MINI_APP_URL)
        )]
    ])
    await message.answer(
        "Quyidagi tugmani bosib Mini Appni oching 👇",
        reply_markup=keyboard
    )


@dp.message(Command("price"))
async def price_handler(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Masalan: `/price btc`", parse_mode="Markdown")
        return

    coin = await search_coin(args[1].strip())
    if not coin:
        await message.answer("❌ Bunday kriptovalyuta topilmadi.")
        return

    data = await get_price(coin["id"])
    if not data:
        await message.answer("Ma’lumot olishda xatolik yuz berdi.")
        return

    change = data.get("usd_24h_change") or 0
    emoji = "🟢" if change >= 0 else "🔴"

    text = (
        f"💰 **{coin['name']} ({coin['symbol'].upper()})**\n\n"
        f"💵 Narx: **${data['usd']:,.4f}**\n"
        f"{emoji} 24 soat: **{change:.2f}%**"
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("chart"))
async def chart_handler(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Masalan: `/chart btc`", parse_mode="Markdown")
        return

    coin = await search_coin(args[1].strip())
    if not coin:
        await message.answer("❌ Coin topilmadi.")
        return

    chart_data = await get_chart_data(coin["id"])
    if not chart_data or "prices" not in chart_data:
        await message.answer("Grafik ma’lumoti olinmadi.")
        return

    buf = create_chart(chart_data["prices"], coin["name"])
    photo = BufferedInputFile(buf.read(), filename="chart.png")
    await message.answer_photo(
        photo,
        caption=f"📈 **{coin['name']}** — 7 kunlik grafik",
        parse_mode="Markdown"
    )


@dp.message(Command("alert"))
async def alert_handler(message: Message):
    # /alert btc above 95000
    parts = message.text.split()
    if len(parts) < 4:
        await message.answer(
            "Format:\n"
            "`/alert btc above 95000`\n"
            "`/alert eth below 3200`",
            parse_mode="Markdown"
        )
        return

    coin_query = parts[1]
    direction = parts[2].lower()
    try:
        target = float(parts[3])
    except ValueError:
        await message.answer("Narx raqam bo‘lishi kerak.")
        return

    if direction not in ("above", "below"):
        await message.answer("Faqat `above` yoki `below` yozing.")
        return

    coin = await search_coin(coin_query)
    if not coin:
        await message.answer("❌ Coin topilmadi.")
        return

    await add_alert(
        message.from_user.id,
        coin["id"],
        coin["name"],
        target,
        direction
    )

    dir_uz = "oshganda" if direction == "above" else "tushganda"
    await message.answer(
        f"✅ Alert qo‘yildi!\n\n"
        f"**{coin['name']}** ${target:,.2f} dan {dir_uz} sizga xabar yuboraman.",
        parse_mode="Markdown"
    )


@dp.message(Command("myalerts"))
async def myalerts_handler(message: Message):
    alerts = await get_user_alerts(message.from_user.id)
    if not alerts:
        await message.answer("Sizda hech qanday alert yo‘q.")
        return

    text = "📋 **Sizning alertlaringiz:**\n\n"
    for a in alerts:
        dir_uz = "↑ oshganda" if a[3] == "above" else "↓ tushganda"
        text += f"#{a[0]}  **{a[1]}** — ${a[2]:,.2f}  {dir_uz}\n"

    text += "\nO‘chirish uchun: `/delalert 1`"
    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("delalert"))
async def delalert_handler(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Masalan: `/delalert 1`", parse_mode="Markdown")
        return

    try:
        alert_id = int(parts[1])
    except ValueError:
        await message.answer("ID raqam bo‘lishi kerak.")
        return

    await delete_alert(alert_id, message.from_user.id)
    await message.answer("✅ Alert o‘chirildi.")

@dp.message(Command("top"))
async def top_handler(message: Message):
    try:
        await message.answer("⏳ Top 10 yuklanmoqda...")

        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 10,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h"
        }

        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0"
        }

        timeout = aiohttp.ClientTimeout(total=15)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params, headers=headers) as resp:
                status = resp.status
                text_resp = await resp.text()

                if status != 200:
                    logging.error(f"CoinGecko /top error: {status} | {text_resp[:200]}")
                    await message.answer(f"❌ API xatosi ({status}). Keyinroq qayta urinib ko‘ring.")
                    return

                coins = await resp.json()

        if not coins or not isinstance(coins, list):
            await message.answer("❌ Ma’lumot olinmadi.")
            return

        text = "🏆 **Top 10 Kriptovalyuta** (Market Cap)\n\n"

        for i, c in enumerate(coins, 1):
            name = c.get("name", "Noma’lum")
            symbol = c.get("symbol", "").upper()
            price = c.get("current_price") or 0
            change = c.get("price_change_percentage_24h") or 0

            emoji = "🟢" if change >= 0 else "🔴"
            sign = "+" if change >= 0 else ""

            text += (
                f"**{i}. {name}** ({symbol})\n"
                f"💵 ${price:,.4f}   {emoji} {sign}{change:.2f}%\n\n"
            )

        await message.answer(text, parse_mode="Markdown")

    except asyncio.TimeoutError:
        await message.answer("⏰ Vaqt tugadi. Qayta urinib ko‘ring.")
    except Exception as e:
        logging.error(f"/top xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi. Birozdan keyin qayta urinib ko‘ring.")



# ==================== MINI APP DAN KELGAN MA’LUMOT ====================
@dp.message(F.web_app_data)
async def web_app_data_handler(message: Message):
    try:
        data = json.loads(message.web_app_data.data)

        if data.get("action") == "set_alert":
            coin_query = data.get("coin", "").strip()
            direction = data.get("direction", "").lower()
            target = float(data.get("price", 0))

            if not coin_query or direction not in ("above", "below") or target <= 0:
                await message.answer("Noto‘g‘ri ma’lumot.")
                return

            coin = await search_coin(coin_query)
            if not coin:
                await message.answer("❌ Coin topilmadi.")
                return

            await add_alert(
                message.from_user.id,
                coin["id"],
                coin["name"],
                target,
                direction
            )

            dir_uz = "oshganda" if direction == "above" else "tushganda"
            await message.answer(
                f"✅ **Mini App** orqali alert qo‘yildi!\n\n"
                f"**{coin['name']}** ${target:,.2f} dan {dir_uz} xabar olasiz.",
                parse_mode="Markdown"
            )
        else:
            await message.answer("Noma’lum amal.")
    except Exception as e:
        logging.error(f"WebApp data error: {e}")
        await message.answer("Xatolik yuz berdi.")


# ==================== ALERT TEKSHIRUVCHI ====================
async def check_alerts():
    while True:
        try:
            alerts = await get_all_alerts()
            for alert in alerts:
                alert_id, user_id, coin_id, coin_name, target, direction = alert

                data = await get_price(coin_id)
                if not data:
                    continue

                price = data["usd"]
                triggered = False

                if direction == "above" and price >= target:
                    triggered = True
                elif direction == "below" and price <= target:
                    triggered = True

                if triggered:
                    emoji = "🚀" if direction == "above" else "📉"
                    text = (
                        f"{emoji} **Alert ishladi!**\n\n"
                        f"**{coin_name}** hozir **${price:,.4f}**\n"
                        f"Siz belgilagan narx: ${target:,.2f}"
                    )
                    try:
                        await bot.send_message(user_id, text, parse_mode="Markdown")
                    except Exception:
                        pass

                    # Bir marta ishlagandan keyin o‘chiramiz
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
                        await db.commit()

        except Exception as e:
            logging.error(f"Alert check error: {e}")

        await asyncio.sleep(60)  # har 60 soniyada


# ==================== ISHGA TUSHIRISH ====================
async def main():
    await init_db()
    asyncio.create_task(check_alerts())
    logging.info("Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
