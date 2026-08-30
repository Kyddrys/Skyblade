import discord
from discord.ext import commands
import easyocr
import re
import sqlite3
import os
import tempfile
import asyncio
from datetime import datetime
import traceback  # Hata detayı için

# -------------------- KONFIGÜRASYON --------------------
CULTIVATION_CHANNEL_NAME = "【🌱】・cultivation"
LEADERBOARD_CHANNEL_NAME = "【🏆】・leaderboard"

ALLOWED_REALMS = [
    "Foundation Establishment",
    "Golden Core",
    "Nascent Soul",
    "Transcendent"
]

ALLOWED_SECTS = [
    "SECT LEADER",
    "HALCYON",
    "GENESIS",
    "ETERNAL",
    "VAGABOND",
    "RENEGADE",
    "SUPREME",
    "YINGYANG",
    "DUMBBASS",
    "WOLVES",
    "KNIGHTS"
]

# ------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# EasyOCR (CPU modunda, GPU yoksa)
reader = easyocr.Reader(['en'], gpu=False)

# -------------------- VERİ TABANI --------------------
db = sqlite3.connect("leaderboard.db")
cursor = db.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS leaderboard (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        cp INTEGER,
        realm TEXT,
        sect TEXT,
        updated_at TIMESTAMP
    )
""")
db.commit()

# -------------------- YARDIMCI FONKSİYONLAR --------------------

def parse_cp(cp_str: str) -> int:
    """97.42M -> 97420000"""
    cp_str = cp_str.replace(" ", "").replace(",", "")
    match = re.match(r"([\d.]+)([KMB])", cp_str.upper())
    if not match:
        try:
            return int(float(cp_str))
        except:
            return 0
    val, unit = match.groups()
    val = float(val)
    if unit == "K":
        return int(val * 1000)
    elif unit == "M":
        return int(val * 1_000_000)
    elif unit == "B":
        return int(val * 1_000_000_000)
    return int(val)

def format_cp_display(cp: int) -> str:
    if cp >= 1_000_000_000:
        return f"{cp/1_000_000_000:.2f}B".replace(".", ",")
    elif cp >= 1_000_000:
        return f"{cp/1_000_000:.2f}M".replace(".", ",")
    elif cp >= 1_000:
        return f"{cp/1_000:.2f}K".replace(".", ",")
    else:
        return str(cp)

def extract_data_from_text(full_text: str) -> dict:
    """OCR metninden isim, CP, Alem, Klan çıkar."""
    lines = full_text.splitlines()
    raw_lines = [line.strip() for line in lines if line.strip()]
    
    name = raw_lines[0] if raw_lines else "Unknown"
    
    cp_match = re.search(r"(\d+\.?\d*)\s*([KMB])", full_text, re.IGNORECASE)
    cp = parse_cp(cp_match.group(0)) if cp_match else 0
    
    realm = None
    for r in ALLOWED_REALMS:
        if re.search(r, full_text, re.IGNORECASE):
            realm = r
            break
    if not realm:
        realm = "Unknown"
    
    sect = None
    for s in ALLOWED_SECTS:
        if re.search(s, full_text, re.IGNORECASE):
            sect = s
            break
    if not sect:
        sect = "Unknown"
    
    return {"name": name, "cp": cp, "realm": realm, "sect": sect}

async def update_leaderboard(guild: discord.Guild):
    """Leaderboard kanalını güncelle."""
    channel = discord.utils.get(guild.channels, name=LEADERBOARD_CHANNEL_NAME)
    if not channel:
        return
    cursor.execute("SELECT name, cp, realm FROM leaderboard ORDER BY cp DESC LIMIT 250")
    rows = cursor.fetchall()
    if not rows:
        content = "👑 Eternal Blade – Leaderboard\n\n*Henüz gönderim yok.*"
    else:
        lines = ["👑 Eternal Blade – Leaderboard\n"]
        for idx, (name, cp, realm) in enumerate(rows, 1):
            lines.append(f"`{idx:>3}.` {name} 『{realm}』 – **{format_cp_display(cp)}** CP")
        content = "\n".join(lines)
    chunks = [content[i:i+1990] for i in range(0, len(content), 1990)]
    async for msg in channel.history(limit=50):
        if msg.author == bot.user and msg.content.startswith("👑 Eternal Blade"):
            await msg.edit(content=chunks[0])
            for extra in chunks[1:]:
                await channel.send(extra)
            async for old_msg in channel.history(limit=50, after=msg):
                if old_msg.author == bot.user and old_msg.content.startswith("👑 Eternal Blade"):
                    await old_msg.delete()
            return
    for chunk in chunks:
        await channel.send(chunk)

async def assign_roles(member: discord.Member, new_realm: str, new_sect: str, guild: discord.Guild):
    """Eski alem/klan rollerini kaldır, yenilerini ata."""
    for realm_name in ALLOWED_REALMS:
        role = discord.utils.get(guild.roles, name=realm_name)
        if role and role in member.roles:
            await member.remove_roles(role)
    for sect_name in ALLOWED_SECTS:
        role = discord.utils.get(guild.roles, name=sect_name)
        if role and role in member.roles:
            await member.remove_roles(role)
    if new_realm != "Unknown":
        role = discord.utils.get(guild.roles, name=new_realm)
        if role:
            await member.add_roles(role)
    if new_sect != "Unknown":
        role = discord.utils.get(guild.roles, name=new_sect)
        if role:
            await member.add_roles(role)

# -------------------- DISCORD OLAYLARI --------------------

@bot.event
async def on_ready():
    print(f"✅ Bot aktif: {bot.user}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not message.attachments:
        return
    if message.channel.name != CULTIVATION_CHANNEL_NAME:
        return

    # ---- Hata mesajlarını göndermek için yardımcı fonksiyon ----
    async def send_error(msg: str):
        try:
            await message.author.send(f"❌ {msg}")
        except:
            pass
        try:
            await message.channel.send(f"❌ {msg}")
        except:
            pass

    # 1. Görseli indir
    attachment = message.attachments[0]
    if not attachment.filename.lower().endswith(('png', 'jpg', 'jpeg', 'webp', 'gif')):
        await send_error("Geçerli bir resim dosyası yükle (PNG, JPG, WEBP).")
        try:
            await message.delete()
        except:
            pass
        return

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    try:
        await attachment.save(temp_file.name)
        temp_file.close()
    except Exception as e:
        await send_error(f"Dosya kaydedilemedi: {str(e)}")
        return

    # 2. Mesajı sil (görsel güvende)
    try:
        await message.delete()
    except:
        pass

    # 3. OCR işlemi
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, reader.readtext, temp_file.name)
        text_lines = [item[1] for item in result]
        full_text = " ".join(text_lines)
    except Exception as e:
        os.unlink(temp_file.name)
        await send_error(f"OCR hatası: {str(e)}\n{traceback.format_exc()}")
        return
    finally:
        if os.path.exists(temp_file.name):
            os.unlink(temp_file.name)

    if not full_text.strip():
        await send_error("Metin okunamadı, daha net bir resim dene.")
        return

    # 4. Verileri çıkar
    extracted = extract_data_from_text(full_text)
    name, cp, realm, sect = extracted["name"], extracted["cp"], extracted["realm"], extracted["sect"]

    # 5. Doğrulama
    if cp == 0:
        await send_error("CP değeri bulunamadı. (Örn: 97.42M)")
        return
    if realm == "Unknown":
        await send_error("Alem tespit edilemedi. (Örn: Nascent Soul)")
        return
    if sect == "Unknown":
        await send_error("Klan tespit edilemedi. (Örn: GENESIS)")
        return

    # 6. Veritabanına kaydet
    try:
        cursor.execute("""
            INSERT INTO leaderboard (user_id, name, cp, realm, sect, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name=excluded.name, cp=excluded.cp, realm=excluded.realm,
                sect=excluded.sect, updated_at=excluded.updated_at
        """, (message.author.id, name, cp, realm, sect, datetime.now()))
        db.commit()
    except Exception as e:
        await send_error(f"Veritabanı hatası: {str(e)}")
        return

    # 7. Rolleri ata
    try:
        await assign_roles(message.author, realm, sect, message.guild)
    except discord.Forbidden:
        await send_error("Rol yetkim yok. Lütfen botu Yönetici yap!")
        return
    except Exception as e:
        await send_error(f"Rol atama hatası: {str(e)}")
        return

    # 8. Leaderboard'u güncelle
    try:
        await update_leaderboard(message.guild)
    except Exception as e:
        await send_error(f"Leaderboard güncelleme hatası: {str(e)}")
        return

    # 9. Başarı mesajı
    embed = discord.Embed(
        title="✅ Cultivation Kaydedildi!",
        description=f"**İsim:** {name}\n**Alem:** {realm}\n**Klan:** {sect}\n**CP:** {format_cp_display(cp)}",
        color=discord.Color.green()
    )
    try:
        await message.author.send(embed=embed)
    except:
        pass
    try:
        await message.channel.send(embed=embed)
    except:
        pass

# -------------------- BOTU ÇALIŞTIR --------------------
if __name__ == "__main__":
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        raise ValueError("❌ TOKEN environment variable is not set! (Railway Variables'a TOKEN ekle!)")
    bot.run(TOKEN)
