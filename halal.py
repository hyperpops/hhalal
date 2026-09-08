
import discord
from discord.ext import commands
from discord import app_commands
from keep_alive import keep_alive

import asyncio
import aiohttp
import json
import os
import hashlib
import ssl

from dotenv import load_dotenv
from aiorpcx import connect_rs
from bip_utils import (
    Bip39SeedGenerator,
    Bip44,
    Bip44Coins,
    Bip84,
    Bip84Coins,
    Bip44Changes
)

load_dotenv()

# =========================
# CONFIG
# =========================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ETH_MNEMONIC = os.getenv("ETH_MNEMONIC")
SOL_MNEMONIC = os.getenv("SOL_MNEMONIC")
LTC_MNEMONIC = os.getenv("LTC_MNEMONIC")
OWNER_ID = int(os.getenv("OWNER_ID", "1492532153437913378"))

BTC_ZPUB = os.getenv(
    "BTC_ZPUB",
    "zpub6s41sNGkhPfiz5RcXspZJwqCFzFDEErh8A7abeeahsbgQWPyuT2tgArzHFZoh2NPMBJUDiAhyd1o4uarmW7JhD3GZpvfygPBZETeUYPsgzE"
)

ETH_RPC = os.getenv("ETH_RPC", "https://ethereum.publicnode.com")
USDT_CONTRACT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"

# Exact green sampled from your screenshot: RGB(82, 248, 90) / #52F85A
EMBED_COLOR = 0x52F85A
SECURITY_COLOR = 0xFF5C5C
WARNING_COLOR = 0xFEE75C
NEUTRAL_COLOR = 0x2B2D31
SUMMARY_GIF_URL = "https://media.discordapp.net/attachments/1500924796261761219/1509302596530344127/Summary_Anim.gif?ex=6a18af14&is=6a175d94&hm=b8f192b90e9d3a4a18a1a80172802729c90597f42c13c751d836e91e6f846e5c&="

TICKET_COUNTER_FILE = "ticket_counter.json"
TICKET_COUNTER_START = 1993754

SUPPORTED_COINS = ["btc", "ltc", "eth", "sol", "usdt"]

COIN_NAMES = {
    "btc": "Bitcoin",
    "ltc": "Litecoin",
    "eth": "Ethereum",
    "sol": "Solana",
    "usdt": "USDT ERC-20",
}

COIN_LABELS = {
    "btc": "Bitcoin (BTC)",
    "ltc": "Litecoin (LTC)",
    "eth": "Ethereum (ETH)",
    "sol": "Solana (SOL)",
    "usdt": "USDT ERC-20",
}

COIN_SYMBOLS = {
    "btc": "BTC",
    "ltc": "LTC",
    "eth": "ETH",
    "sol": "SOL",
    "usdt": "USDT",
}

COIN_EMOJIS = {
    "btc": "<:btcs:1493227898671202457>",
    "ltc": "<:LTC:1508571797870612600>",
    "eth": "<:ETH:1508571828799410226>",
    "sol": "<:sol:1499439584310857881>",
    "usdt": "<:USDT_Logo:1507388354445181101>",
}

LTC_ELECTRUM_SERVERS = [
    ("electrum-ltc.bysh.me", 50002),
    ("ltc.rentonisk.com", 50002),
    ("electrum-ltc.petrkr.net", 60002),
]

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix=".", intents=intents)


# =========================
# STORAGE
# =========================

def load_json_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def load_index(name):
    data = load_json_file(f"{name}_index.json", {"index": 0})
    try:
        return int(data.get("index", 0))
    except Exception:
        return 0


def save_index(name, index):
    save_json_file(f"{name}_index.json", {"index": index})


def next_ticket_number():
    data = load_json_file(TICKET_COUNTER_FILE, {"counter": TICKET_COUNTER_START})
    current = int(data.get("counter", TICKET_COUNTER_START))
    save_json_file(TICKET_COUNTER_FILE, {"counter": current + 1})
    return current


# =========================
# API HELPERS
# =========================

async def safe_get_json(session, url, label):
    try:
        async with session.get(url, timeout=30) as response:
            text = await response.text()

            if response.status == 429:
                print(f"{label}: rate limited, waiting 120 seconds.")
                await asyncio.sleep(120)
                return None

            if response.status >= 400:
                print(f"{label}: HTTP {response.status}: {text[:200]}")
                await asyncio.sleep(60)
                return None

            try:
                return json.loads(text)
            except json.JSONDecodeError:
                print(f"{label}: non-JSON response: {text[:200]}")
                await asyncio.sleep(60)
                return None

    except Exception as e:
        print(f"{label}: API error:", e)
        await asyncio.sleep(60)
        return None


async def safe_post_json(session, url, payload, label):
    try:
        async with session.post(url, json=payload, timeout=30) as response:
            text = await response.text()

            if response.status == 429:
                print(f"{label}: rate limited, waiting 120 seconds.")
                await asyncio.sleep(120)
                return None

            if response.status >= 400:
                print(f"{label}: HTTP {response.status}: {text[:200]}")
                await asyncio.sleep(60)
                return None

            try:
                return json.loads(text)
            except json.JSONDecodeError:
                print(f"{label}: non-JSON response: {text[:200]}")
                await asyncio.sleep(60)
                return None

    except Exception as e:
        print(f"{label}: API error:", e)
        await asyncio.sleep(60)
        return None


async def eth_rpc_call(method, params, label):
    async with aiohttp.ClientSession() as session:
        return await safe_post_json(
            session,
            ETH_RPC,
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            label
        )


async def get_crypto_usd_price(coin):
    coin_ids = {
        "btc": "bitcoin",
        "ltc": "litecoin",
        "eth": "ethereum",
        "sol": "solana",
        "usdt": "tether",
    }

    coin_id = coin_ids.get(coin.lower())
    if not coin_id:
        return None

    async with aiohttp.ClientSession() as session:
        data = await safe_get_json(
            session,
            f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd",
            "CoinGecko price"
        )

    if not isinstance(data, dict):
        return None

    return data.get(coin_id, {}).get("usd")


# =========================
# ADDRESS GENERATION
# =========================

def generate_btc_address():
    index = load_index("btc")
    wallet = Bip84.FromExtendedKey(BTC_ZPUB, Bip84Coins.BITCOIN)
    address = wallet.Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
    save_index("btc", index + 1)
    print(f"Generated BTC address #{index}: {address}")
    return address


def generate_ltc_address():
    index = load_index("ltc")
    seed = Bip39SeedGenerator(LTC_MNEMONIC).Generate()
    wallet = Bip84.FromSeed(seed, Bip84Coins.LITECOIN)
    address = wallet.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
    save_index("ltc", index + 1)
    print(f"Generated LTC address #{index}: {address}")
    return address


def generate_eth_address():
    index = load_index("eth")
    seed = Bip39SeedGenerator(ETH_MNEMONIC).Generate()
    wallet = Bip44.FromSeed(seed, Bip44Coins.ETHEREUM)
    address = wallet.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
    save_index("eth", index + 1)
    print(f"Generated ETH address #{index}: {address}")
    return address


def generate_usdt_address():
    index = load_index("usdt")
    seed = Bip39SeedGenerator(ETH_MNEMONIC).Generate()
    wallet = Bip44.FromSeed(seed, Bip44Coins.ETHEREUM)
    address = wallet.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
    save_index("usdt", index + 1)
    print(f"Generated USDT ERC-20 address #{index}: {address}")
    return address


def generate_sol_address():
    index = load_index("sol")
    seed = Bip39SeedGenerator(SOL_MNEMONIC).Generate()
    wallet = Bip44.FromSeed(seed, Bip44Coins.SOLANA)
    address = wallet.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
    save_index("sol", index + 1)
    print(f"Generated SOL address #{index}: {address}")
    return address


def generate_address_for_coin(coin):
    if coin == "btc":
        return generate_btc_address()
    if coin == "ltc":
        return generate_ltc_address()
    if coin == "eth":
        return generate_eth_address()
    if coin == "sol":
        return generate_sol_address()
    if coin == "usdt":
        return generate_usdt_address()
    raise ValueError("Unsupported coin.")


# =========================
# LTC ELECTRUM
# =========================

CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def bech32_polymod(values):
    generators = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ value
        for i in range(5):
            if (top >> i) & 1:
                chk ^= generators[i]
    return chk


def bech32_hrp_expand(hrp):
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def bech32_decode(bech):
    bech = bech.lower()
    if not all(33 <= ord(x) <= 126 for x in bech):
        return None, None

    pos = bech.rfind("1")
    if pos < 1 or pos + 7 > len(bech):
        return None, None

    hrp = bech[:pos]
    data = []
    for char in bech[pos + 1:]:
        if char not in CHARSET:
            return None, None
        data.append(CHARSET.find(char))

    if bech32_polymod(bech32_hrp_expand(hrp) + data) not in [1, 0x2bc830a3]:
        return None, None

    return hrp, data[:-6]


def convert_bits(data, from_bits, to_bits, pad=True):
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1

    for value in data:
        if value < 0 or value >> from_bits:
            return None
        acc = ((acc << from_bits) | value) & max_acc
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            ret.append((acc >> bits) & maxv)

    if pad:
        if bits:
            ret.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        return None

    return ret


def ltc_address_to_scripthash(address):
    hrp, data = bech32_decode(address)
    if hrp != "ltc" or data is None:
        raise ValueError("Invalid LTC bech32 address.")

    witness_version = data[0]
    witness_program = bytes(convert_bits(data[1:], 5, 8, False))

    if witness_version != 0 or len(witness_program) != 20:
        raise ValueError("Only LTC native segwit p2wpkh addresses are supported.")

    script_pubkey = bytes([0x00, 0x14]) + witness_program
    return hashlib.sha256(script_pubkey).digest()[::-1].hex()


async def electrum_ltc_request(method, params):
    ssl_context = ssl._create_unverified_context()

    for host, port in LTC_ELECTRUM_SERVERS:
        try:
            async with connect_rs(host, port, ssl=ssl_context) as session:
                await session.send_request("server.version", ["discord-mm-bot", "1.4"])
                return await session.send_request(method, params)
        except Exception as e:
            print(f"LTC Electrum server failed {host}:{port} -> {e}")
            continue

    return None


# =========================
# DEAL STATE / FEES
# =========================

# UI colors sampled from the reference screenshots.
GREEN_COLOR = 0x78E572
RED_COLOR = 0xEF5251
ORANGE_COLOR = 0xF0A23A
DARK_COLOR = 0x2B2D31

WELCOME_GIF_URL = "https://images-ext-1.discordapp.net/external/b8pBarTG7Bx7hjTSBnRIQhMf8zfTUDFddW6jSGskTdc/https/halalmm.com/Welcome_Prompt.gif"
SUMMARY_GIF_URL = "https://images-ext-1.discordapp.net/external/Wu_LbFmDIMoTc-8zCrQx6FapCyBWrvNEXTio3LEnzk4/https/halalmm.com/Summary_Anim.gif"
WAITING_GIF_URL = "https://images-ext-1.discordapp.net/external/CGG0dQWLbwd8R1FKvBx6ttua5l-IK8Jb7boOOl37FeU/https/halalmm.com/Waiting_Anim.gif"
RECEIVED_GIF_URL = "https://images-ext-1.discordapp.net/external/clxLcxIDg8hB024h3-tHloBMISAN6LHJpXY7ng6a-f4/https/halalmm.com/Money_Receive_Anim.gif"
KNOWN_SCAMS_URL = "https://www.halalmm.com/creating-deals/known-scams"
USER_ID_HELP_URL = "https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID"
WEBSITE_URL = "https://www.halalmm.com/"
LOADING_EMOJI = "<a:load:1546618675791011921>"
REQUIRED_CONFIRMATIONS = 1

# Current in-memory ticket state. This lets /await and /received use the exact
# same UI and controls as the automatic blockchain monitors.
ACTIVE_DEALS = {}

COIN_EMOJIS = {
    "btc": "<:btc:1546606873850023966>",
    "ltc": "<:ltc:1546606840509632692>",
    "eth": "<:eth:1546607067454902303>",
    "sol": "<:sol:1546606824181211206>",
    "usdt": "<:usdt:1546606857437708299>",
}

DEAL_TYPES = {
    "Limiteds": "Deal involving Roblox Limiteds",
    "In-Game": "Deal involving Roblox in-game items",
    "Exchange": "Deal involving currency exchange",
    "Counter Strike": "Deal involving CS",
    "Robux": "Deal involving Roblox Robux",
    "Other": "Deal involves something else",
}


class DealState:
    def __init__(self, channel, coin, ticket_number, ticket_code):
        self.channel = channel
        self.coin = coin
        self.ticket_number = ticket_number
        self.ticket_code = ticket_code
        self.opener = None
        self.other_user = None
        self.sender = None
        self.receiver = None
        self.deal_type = None
        self.amount_usd = 0.0
        self.fee_usd = 0.0
        self.fee_mode = None
        self.address = None
        self.invoice_crypto_amount = None
        self.awaiting_message = None
        self.detected_message = None
        self.detected_txid = None
        self.detected_amount = None
        self.confirmed_message = None
        self.proceed_message = None
        self.deal_finished = False
        self.cancellation_pending = False
        self.release_pending = False


def calculate_fee(amount_usd, coin):
    if amount_usd < 50:
        base_fee = 0.0
    elif amount_usd < 250:
        base_fee = 2.0
    else:
        base_fee = amount_usd * 0.01

    if coin == "usdt":
        base_fee += 1.0

    return round(base_fee, 2)


def invoice_total_for_sender(amount, fee, fee_mode):
    if fee_mode == "sender":
        return amount + fee
    if fee_mode == "split":
        return amount + (fee / 2)
    return amount


def fee_display_owner(state):
    if state.fee_mode == "sender":
        return state.sender.mention
    if state.fee_mode == "receiver":
        return state.receiver.mention
    if state.fee_mode == "split":
        return "Split Fee"
    return "Use Pass"


def role_name(state, user):
    if state.sender and user.id == state.sender.id:
        return "sender"
    if state.receiver and user.id == state.receiver.id:
        return "receiver"
    return "participant"


async def send_confirm_response(state, user, response="Correct"):
    """Send the compact confirmation card used throughout the deal flow."""
    embed = discord.Embed(
        description=f"{user.mention} has responded with **'{response}'**",
        color=0x2B2D31,
    )
    await state.channel.send(embed=embed)


def large_title_embed(title, description="", color=GREEN_COLOR):
    """Embed with a Discord markdown H1 heading to match the larger reference titles."""
    body = f"# {title}"
    if description:
        body += f"\n{description}"
    return discord.Embed(description=body, color=color)


# =========================
# VIEWS
# =========================

class CloseButton(discord.ui.View):
    def __init__(self, state: DealState):
        super().__init__(timeout=None)
        self.state = state

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Before deal-type selection, the first person interacting with the
        # ticket is treated as the creator. Afterwards only that creator can close.
        if self.state.opener is None:
            self.state.opener = interaction.user
        if interaction.user.id != self.state.opener.id:
            await interaction.response.send_message("Only the deal creator can close this ticket.", ephemeral=True)
            return
        await interaction.response.send_message("Closing ticket...", ephemeral=True)
        await asyncio.sleep(1)
        try:
            ACTIVE_DEALS.pop(self.state.channel.id, None)
            await self.state.channel.delete(reason=f"Closed by ticket creator {interaction.user}")
        except Exception as e:
            print("Ticket close error:", e)


class DealTypeSelect(discord.ui.Select):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(label=name, description=description)
            for name, description in DEAL_TYPES.items()
        ]
        super().__init__(placeholder="Select Deal Type", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        state = self.parent_view.state
        if state.opener is None:
            state.opener = interaction.user
        elif interaction.user.id != state.opener.id:
            await interaction.response.send_message("Only the deal creator can select the deal type.", ephemeral=True)
            return

        state.deal_type = self.values[0]
        self.placeholder = state.deal_type
        self.disabled = True
        await interaction.response.edit_message(embed=deal_type_embed(state), view=self.parent_view)
        self.parent_view.stop()


class DealTypeView(discord.ui.View):
    def __init__(self, state: DealState):
        super().__init__(timeout=1800)
        self.state = state
        self.add_item(DealTypeSelect(self))


class RoleSelectionView(discord.ui.LayoutView):
    """Components V2 role selector matching the reference layout."""

    def __init__(self, state: DealState):
        super().__init__(timeout=1800)
        self.state = state
        self.message = None
        self._build()

    async def interaction_check(self, interaction):
        if interaction.user.id not in (self.state.opener.id, self.state.other_user.id):
            await interaction.response.send_message("You are not part of this deal.", ephemeral=True)
            return False
        return True

    def _build(self):
        self.clear_items()
        coin_name = COIN_NAMES[self.state.coin]
        sending = self.state.sender.mention if self.state.sender else "None"
        receiving = self.state.receiver.mention if self.state.receiver else "None"
        both_selected = self.state.sender is not None and self.state.receiver is not None

        sender_button = discord.ui.Button(
            label="Select",
            style=discord.ButtonStyle.success,
            disabled=both_selected,
            custom_id=f"role_sender:{self.state.channel.id}",
        )
        receiver_button = discord.ui.Button(
            label="Select",
            style=discord.ButtonStyle.success,
            disabled=both_selected,
            custom_id=f"role_receiver:{self.state.channel.id}",
        )
        reset_button = discord.ui.Button(
            label="Reset",
            style=discord.ButtonStyle.danger,
            custom_id=f"role_reset:{self.state.channel.id}",
        )

        sender_button.callback = self._select_sender
        receiver_button.callback = self._select_receiver
        reset_button.callback = self._reset

        container = discord.ui.Container(
            discord.ui.TextDisplay("# Role Selection"),
            discord.ui.TextDisplay("Select one of the following buttons that corresponds to your role in this deal."),
            discord.ui.Separator(),
            discord.ui.Section(
                discord.ui.TextDisplay(f"Sending {coin_name}: {sending}"),
                accessory=sender_button,
            ),
            discord.ui.Separator(),
            discord.ui.Section(
                discord.ui.TextDisplay(f"Receiving {coin_name}: {receiving}"),
                accessory=receiver_button,
            ),
            accent_colour=GREEN_COLOR,
        )
        self.add_item(container)
        self.add_item(discord.ui.ActionRow(reset_button))

    async def refresh(self):
        self._build()
        if self.message:
            await self.message.edit(view=self)

    async def choose(self, interaction, role):
        if role == "sender":
            if self.state.receiver and interaction.user.id == self.state.receiver.id:
                await interaction.response.send_message("You already selected receiver.", ephemeral=True)
                return
            if self.state.sender and self.state.sender.id != interaction.user.id:
                await interaction.response.send_message("The sending role has already been selected.", ephemeral=True)
                return
            self.state.sender = interaction.user
        else:
            if self.state.sender and interaction.user.id == self.state.sender.id:
                await interaction.response.send_message("You already selected sender.", ephemeral=True)
                return
            if self.state.receiver and self.state.receiver.id != interaction.user.id:
                await interaction.response.send_message("The receiving role has already been selected.", ephemeral=True)
                return
            self.state.receiver = interaction.user

        await interaction.response.defer()
        if role == "receiver":
            warning = discord.Embed(
                description="You selected receiver. Sending money to the bot will result in getting scammed.",
                color=RED_COLOR,
            )
            await interaction.followup.send(embed=warning, ephemeral=True)

        await self.refresh()
        if self.state.sender and self.state.receiver:
            self.stop()

    async def _select_sender(self, interaction: discord.Interaction):
        await self.choose(interaction, "sender")

    async def _select_receiver(self, interaction: discord.Interaction):
        await self.choose(interaction, "receiver")

    async def _reset(self, interaction: discord.Interaction):
        if interaction.user.id != self.state.opener.id:
            await interaction.response.send_message("Only the deal creator can reset role selection.", ephemeral=True)
            return
        self.state.sender = None
        self.state.receiver = None
        self._build()
        await interaction.response.edit_message(view=self)


class RoleConfirmationView(discord.ui.LayoutView):
    """Components V2 role confirmation with a button beside each matching role."""

    def __init__(self, state: DealState):
        super().__init__(timeout=1800)
        self.state = state
        self.sender_ok = False
        self.receiver_ok = False
        self.returned = False
        self.message = None
        self._build()

    def _build(self):
        self.clear_items()
        coin_name = COIN_NAMES[self.state.coin]

        sender_button = discord.ui.Button(
            label="Confirm",
            style=discord.ButtonStyle.success,
            disabled=self.sender_ok,
            custom_id=f"confirm_sender:{self.state.channel.id}",
        )
        receiver_button = discord.ui.Button(
            label="Confirm",
            style=discord.ButtonStyle.success,
            disabled=self.receiver_ok,
            custom_id=f"confirm_receiver:{self.state.channel.id}",
        )
        return_button = discord.ui.Button(
            label="Return",
            style=discord.ButtonStyle.danger,
            custom_id=f"confirm_return:{self.state.channel.id}",
        )

        sender_button.callback = self._confirm_sender
        receiver_button.callback = self._confirm_receiver
        return_button.callback = self._return_roles

        container = discord.ui.Container(
            discord.ui.TextDisplay("# Role Confirmation"),
            discord.ui.Separator(),
            discord.ui.Section(
                discord.ui.TextDisplay(f"Sending {coin_name}: {self.state.sender.mention}"),
                accessory=sender_button,
            ),
            discord.ui.Separator(),
            discord.ui.Section(
                discord.ui.TextDisplay(f"Receiving {coin_name}: {self.state.receiver.mention}"),
                accessory=receiver_button,
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay("-# Selecting the wrong role will result in getting scammed!"),
            accent_colour=GREEN_COLOR,
        )
        self.add_item(container)
        self.add_item(discord.ui.ActionRow(return_button))

    async def confirm_role(self, interaction, expected_role):
        expected_user = self.state.sender if expected_role == "sender" else self.state.receiver
        if interaction.user.id != expected_user.id:
            await interaction.response.send_message(
                f"Only the {expected_role} can confirm this role.", ephemeral=True
            )
            return

        if expected_role == "sender":
            if self.sender_ok:
                await interaction.response.send_message("You already confirmed.", ephemeral=True)
                return
            self.sender_ok = True
        else:
            if self.receiver_ok:
                await interaction.response.send_message("You already confirmed.", ephemeral=True)
                return
            self.receiver_ok = True

        await interaction.response.defer()
        await send_confirm_response(self.state, interaction.user, "Correct")
        self._build()
        if self.message:
            await self.message.edit(view=self)
        if self.sender_ok and self.receiver_ok:
            self.stop()

    async def _confirm_sender(self, interaction: discord.Interaction):
        await self.confirm_role(interaction, "sender")

    async def _confirm_receiver(self, interaction: discord.Interaction):
        await self.confirm_role(interaction, "receiver")

    async def _return_roles(self, interaction: discord.Interaction):
        if interaction.user.id != self.state.opener.id:
            await interaction.response.send_message("Only the deal creator can reset role selection.", ephemeral=True)
            return
        self.returned = True
        self.state.sender = None
        self.state.receiver = None
        await interaction.response.defer()
        if self.message:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass
        self.stop()


class DualConfirmView(discord.ui.View):
    def __init__(self, state: DealState, title, description_builder, incorrect_message="Selection reset. Please try again."):
        super().__init__(timeout=1800)
        self.state = state
        self.title = title
        self.description_builder = description_builder
        self.incorrect_message = incorrect_message
        self.sender_ok = False
        self.receiver_ok = False
        self.rejected = False
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id not in (self.state.sender.id, self.state.receiver.id):
            await interaction.response.send_message("You are not part of this deal.", ephemeral=True)
            return False
        return True

    def make_embed(self):
        return large_title_embed(self.title, self.description_builder(), GREEN_COLOR)

    async def refresh(self):
        if self.message:
            await self.message.edit(embed=self.make_embed(), view=self)

    @discord.ui.button(label="Correct", style=discord.ButtonStyle.success)
    async def correct(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id == self.state.sender.id:
            if self.sender_ok:
                await interaction.response.send_message("You already confirmed.", ephemeral=True)
                return
            self.sender_ok = True
        else:
            if self.receiver_ok:
                await interaction.response.send_message("You already confirmed.", ephemeral=True)
                return
            self.receiver_ok = True

        await interaction.response.defer()
        await send_confirm_response(self.state, interaction.user, "Correct")

        if self.sender_ok and self.receiver_ok:
            for child in self.children:
                child.disabled = True
            await self.refresh()
            self.stop()

    @discord.ui.button(label="Incorrect", style=discord.ButtonStyle.secondary)
    async def incorrect(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.rejected = True
        await interaction.response.send_message(self.incorrect_message, ephemeral=True)
        self.stop()


class DealDetailsConfirmView(discord.ui.View):
    def __init__(self, state: DealState):
        super().__init__(timeout=1800)
        self.state = state
        self.sender_ok = False
        self.receiver_ok = False
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id not in (self.state.sender.id, self.state.receiver.id):
            await interaction.response.send_message("You are not part of this deal.", ephemeral=True)
            return False
        return True

    async def handle_confirm(self, interaction):
        if interaction.user.id == self.state.sender.id:
            if self.sender_ok:
                await interaction.response.send_message("You already confirmed.", ephemeral=True)
                return
            self.sender_ok = True
        else:
            if self.receiver_ok:
                await interaction.response.send_message("You already confirmed.", ephemeral=True)
                return
            self.receiver_ok = True
        await interaction.response.defer()
        await send_confirm_response(self.state, interaction.user, "Correct")
        if self.sender_ok and self.receiver_ok:
            for child in self.children:
                child.disabled = True
            if self.message:
                await self.message.edit(embed=deal_details_embed(self.state), view=self)
            self.stop()

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_confirm(interaction)


class FeePaymentView(discord.ui.View):
    def __init__(self, state: DealState):
        super().__init__(timeout=1800)
        self.state = state
        self.value = None

    async def interaction_check(self, interaction):
        if interaction.user.id not in (self.state.sender.id, self.state.receiver.id):
            await interaction.response.send_message("You are not part of this deal.", ephemeral=True)
            return False
        return True

    async def set_value(self, interaction, value):
        self.value = value
        self.state.fee_mode = value
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Sender", style=discord.ButtonStyle.secondary)
    async def sender(self, interaction, button):
        await self.set_value(interaction, "sender")

    @discord.ui.button(label="Receiver", style=discord.ButtonStyle.secondary)
    async def receiver(self, interaction, button):
        await self.set_value(interaction, "receiver")

    @discord.ui.button(label="Split Fee", style=discord.ButtonStyle.success)
    async def split(self, interaction, button):
        await self.set_value(interaction, "split")

    @discord.ui.button(label="Use Pass", style=discord.ButtonStyle.success)
    async def pass_fee(self, interaction, button):
        await self.set_value(interaction, "pass")


class InvoiceView(discord.ui.View):
    def __init__(self, state: DealState):
        super().__init__(timeout=None)
        self.state = state

    async def sender_only(self, interaction):
        if interaction.user.id != self.state.sender.id:
            await interaction.response.send_message("Only the sender can use this button.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Copy Address", style=discord.ButtonStyle.secondary)
    async def copy_address(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.sender_only(interaction):
            return
        await interaction.response.send_message(self.state.address, ephemeral=True)

    @discord.ui.button(label="Copy Amount", style=discord.ButtonStyle.secondary)
    async def copy_amount(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.sender_only(interaction):
            return
        await interaction.response.send_message(f"{self.state.invoice_crypto_amount:.8f}", ephemeral=True)

    @discord.ui.button(label="Copy Details", style=discord.ButtonStyle.secondary, row=1)
    async def copy_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.sender_only(interaction):
            return
        await interaction.response.defer()
        await self.state.channel.send(f"{self.state.address}\n{self.state.invoice_crypto_amount:.8f}")

    @discord.ui.button(label="Cancel deal", style=discord.ButtonStyle.danger, row=1)
    async def cancel_before_payment(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.state.sender.id, self.state.receiver.id):
            await interaction.response.send_message("You are not part of this deal.", ephemeral=True)
            return
        await interaction.response.send_message(
            "The deal can be cancelled through the secured-deal controls once payment is confirmed. Contact the owner if you need help before payment.",
            ephemeral=True,
        )


class ReleaseConfirmationView(discord.ui.View):
    def __init__(self, state: DealState):
        super().__init__(timeout=1800)
        self.state = state

    @discord.ui.button(label="Confirm Release", style=discord.ButtonStyle.success)
    async def confirm_release(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.state.sender.id:
            await interaction.response.send_message("Only the sender can confirm the release.", ephemeral=True)
            return
        if self.state.deal_finished:
            await interaction.response.send_message("This deal has already been completed or canceled.", ephemeral=True)
            return
        self.state.deal_finished = True
        self.state.release_pending = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        embed = large_title_embed(
            "✅ Deal Confirmed",
            (
                "The sender has confirmed that the deal is complete.\n\n"
                "Your deal is confirmed. Please wait for the owner to release your funds."
            ),
            GREEN_COLOR,
        )
        await self.state.channel.send(content=f"{self.state.sender.mention} {self.state.receiver.mention}", embed=embed)
        self.stop()

    @discord.ui.button(label="Go Back", style=discord.ButtonStyle.secondary)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.state.sender.id:
            await interaction.response.send_message("Only the sender can respond to this release confirmation.", ephemeral=True)
            return
        self.state.release_pending = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        embed = discord.Embed(
            description=f"{interaction.user.mention} has responded with **'Go Back'**",
            color=0x2B2D31,
        )
        await self.state.channel.send(embed=embed)
        self.stop()


class CancelConfirmationView(discord.ui.View):
    def __init__(self, state: DealState):
        super().__init__(timeout=1800)
        self.state = state

    @discord.ui.button(label="Confirm Cancellation", style=discord.ButtonStyle.danger)
    async def confirm_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.state.sender.id:
            await interaction.response.send_message("Only the sender can confirm this cancellation.", ephemeral=True)
            return
        if self.state.deal_finished:
            await interaction.response.send_message("This deal has already been completed or canceled.", ephemeral=True)
            return
        self.state.deal_finished = True
        self.state.cancellation_pending = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        embed = large_title_embed(
            "❌ Deal Canceled",
            (
                "The cancellation request has been confirmed by the sender.\n\n"
                "Your deal has been canceled. Please wait for the owner for a refund."
            ),
            RED_COLOR,
        )
        await self.state.channel.send(content=f"{self.state.sender.mention} {self.state.receiver.mention}", embed=embed)
        self.stop()

    @discord.ui.button(label="Keep Deal", style=discord.ButtonStyle.secondary)
    async def keep_deal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.state.sender.id:
            await interaction.response.send_message("Only the sender can respond to this cancellation request.", ephemeral=True)
            return
        self.state.cancellation_pending = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        embed = discord.Embed(
            description=f"{interaction.user.mention} has responded with **'Keep Deal'**",
            color=0x2B2D31,
        )
        await self.state.channel.send(embed=embed)
        self.stop()


class ReleaseCancelView(discord.ui.View):
    def __init__(self, state: DealState):
        super().__init__(timeout=None)
        self.state = state

    @discord.ui.button(label="Release", style=discord.ButtonStyle.success)
    async def release(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.state.sender.id:
            await interaction.response.send_message("Only the sender can release the deal.", ephemeral=True)
            return
        if self.state.deal_finished:
            await interaction.response.send_message("This deal has already been completed or canceled.", ephemeral=True)
            return
        if self.state.release_pending:
            await interaction.response.send_message("A release confirmation is already waiting for you.", ephemeral=True)
            return
        self.state.release_pending = True
        await interaction.response.defer()
        embed = large_title_embed(
            "Release Funds",
            (
                f"{self.state.sender.mention}, please confirm that the receiver has completed their side of the deal.\n\n"
                "Once you confirm, the deal will be marked as complete and the owner can release the funds to the receiver."
            ),
            GREEN_COLOR,
        )
        await self.state.channel.send(content=self.state.sender.mention, embed=embed, view=ReleaseConfirmationView(self.state))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.state.receiver.id:
            await interaction.response.send_message("Only the receiver can request cancellation.", ephemeral=True)
            return
        if self.state.deal_finished:
            await interaction.response.send_message("This deal has already been completed or canceled.", ephemeral=True)
            return
        if self.state.cancellation_pending:
            await interaction.response.send_message("A cancellation request is already awaiting the sender's response.", ephemeral=True)
            return
        self.state.cancellation_pending = True
        await interaction.response.defer()
        embed = large_title_embed(
            "Cancel Deal",
            (
                f"{self.state.receiver.mention} has requested to cancel this deal.\n\n"
                f"{self.state.sender.mention}, please confirm whether this deal should be canceled and moved to the refund process."
            ),
            RED_COLOR,
        )
        await self.state.channel.send(content=self.state.sender.mention, embed=embed, view=CancelConfirmationView(self.state))


# =========================
# EMBEDS / FLOW HELPERS
# =========================

def welcome_embed(state: DealState):
    embed = large_title_embed(
        f"{COIN_EMOJIS[state.coin]} Deal Started",
        (
            "Welcome to our automated cryptocurrency Middleman system! Your\n"
            "cryptocurrency will be stored securely for the duration of this deal. Please\n"
            f"notify support or check our [website]({WEBSITE_URL}) for assistance."
        ),
        GREEN_COLOR,
    )
    embed.set_thumbnail(url=WELCOME_GIF_URL)
    return embed


def security_embed():
    return large_title_embed(
        "Safety Warning",
        (
            "The bot and our support team will **NEVER** direct message you. Ensure all conversations\n"
            f"related to the deal are done within this ticket. Review our [known scams]({KNOWN_SCAMS_URL}) section to stay safe."
        ),
        RED_COLOR,
    )


def deal_type_embed(state: DealState):
    description = (
        "To best serve you, we need to know a few deal details. Please use the dropdown menu below to\n"
        "select the deal type:"
    )
    if state.deal_type:
        description += f"\n\n**Selected:** {state.deal_type}"
    return large_title_embed("Deal Type", description, GREEN_COLOR)


def dealing_with_embed():
    return large_title_embed(
        "Let's start!",
        (
            f"Please paste the UserID of the user you are dealing with [[How?]]({USER_ID_HELP_URL})\n\n"
            "Ex. @User\n"
            "Ex. 123456789123456789"
        ),
        GREEN_COLOR,
    )


def invalid_user_embed():
    return large_title_embed(
        "User Not Found",
        "That user ID or mention is invalid, or the member is not in this server.",
        RED_COLOR,
    )


def invalid_self_or_bot_embed():
    return large_title_embed(
        "Invalid User",
        "You cannot add yourself or a bot as the other dealer.",
        RED_COLOR,
    )


def deal_details_embed(state: DealState):
    return large_title_embed(
        "Deal Details",
        (
            "Before continuing, please confirm the deal details below.\n\n"
            f"**Type:** {state.deal_type}\n"
            f"**Buyer / Sender:** {state.sender.mention}\n"
            f"**Seller / Receiver:** {state.receiver.mention}"
        ),
        GREEN_COLOR,
    )


def parse_user_reference(message):
    """Classify dealer input without responding to unrelated chat.

    Returns (kind, user_id):
      - ("candidate", id) for a plausible raw Discord ID or a real mention
      - ("invalid_mention", None) for text beginning with @ that is not a real mention
      - ("ignore", None) for everything else
    """
    content = message.content.strip()

    # A real Discord mention arrives as <@123...> / <@!123...>.
    if content.startswith("<@"):
        if message.mentions:
            return "candidate", message.mentions[0].id
        return "invalid_mention", None

    # If the user clearly tried to mention someone but Discord did not resolve it,
    # treat it as an invalid user reference. Plain text that does not start with @
    # is intentionally ignored so normal conversation does not trigger bot spam.
    if content.startswith("@"):
        return "invalid_mention", None

    # Discord snowflake IDs are currently 17-20 decimal digits. Short numbers,
    # prices, order numbers, and normal chat are ignored rather than answered to.
    if content.isdigit() and 17 <= len(content) <= 20:
        return "candidate", int(content)

    return "ignore", None


async def ask_deal_type(state: DealState):
    view = DealTypeView(state)
    await state.channel.send(embed=deal_type_embed(state), view=view)
    await view.wait()
    if not state.deal_type:
        # Timeout fallback keeps the flow usable without silently selecting a type.
        await state.channel.send(embed=discord.Embed(description="Deal type selection timed out.", color=RED_COLOR))
        return False
    return True


async def ask_other_user(state: DealState):
    channel = state.channel
    await channel.send(embed=dealing_with_embed())

    def check(message):
        return (
            message.channel == channel
            and not message.author.bot
            and state.opener is not None
            and message.author.id == state.opener.id
        )

    while True:
        msg = await bot.wait_for("message", check=check)
        kind, user_id = parse_user_reference(msg)

        # Ignore ordinary chat completely. The bot only reacts to a plausible
        # Discord snowflake ID or input that explicitly starts with @.
        if kind == "ignore":
            continue

        if kind == "invalid_mention":
            await channel.send(embed=invalid_user_embed(), delete_after=3)
            continue

        member = None
        try:
            member = channel.guild.get_member(user_id)
            if member is None:
                member = await channel.guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            member = None

        if member is None:
            await channel.send(embed=invalid_user_embed(), delete_after=3)
            continue

        if member.bot or member.id == msg.author.id:
            await channel.send(embed=invalid_self_or_bot_embed(), delete_after=3)
            continue

        state.other_user = member
        try:
            await channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        except Exception as e:
            print("Permission add error:", e)

        await channel.send(f"{member.mention} Welcome! Select roles below.")
        return


async def ask_roles(state: DealState):
    role_message = None
    while True:
        state.sender = None
        state.receiver = None
        view = RoleSelectionView(state)
        if role_message is None:
            role_message = await state.channel.send(view=view)
        else:
            await role_message.edit(view=view)
        view.message = role_message
        await view.wait()

        if not state.sender or not state.receiver:
            await state.channel.send(embed=discord.Embed(
                description="Role selection timed out. Please select roles again.", color=RED_COLOR
            ))
            continue

        confirm = RoleConfirmationView(state)
        confirm_msg = await state.channel.send(view=confirm)
        confirm.message = confirm_msg
        await confirm.wait()

        if confirm.returned:
            continue
        if not (confirm.sender_ok and confirm.receiver_ok):
            await state.channel.send(embed=discord.Embed(
                description="Role confirmation timed out. Please select the roles again.", color=RED_COLOR
            ))
            continue
        return


async def ask_deal_details(state: DealState):
    while True:
        view = DealDetailsConfirmView(state)
        msg = await state.channel.send(content=f"{state.sender.mention} {state.receiver.mention}", embed=deal_details_embed(state), view=view)
        view.message = msg
        await view.wait()
        if view.sender_ok and view.receiver_ok:
            return
        await state.channel.send(embed=discord.Embed(
            description="Deal details confirmation timed out. Both users must confirm.", color=RED_COLOR
        ))


async def ask_amount(state: DealState):
    channel = state.channel

    def check(message):
        return message.channel == channel and not message.author.bot and message.author.id == state.sender.id

    while True:
        await channel.send(content=state.sender.mention, embed=large_title_embed(
            "Deal Amount",
            (
                "State the amount the bot is expected to receive in USD (eg. 100.59)\n\n"
                "**Ticket will be closed in 30 minutes if left unattended**"
            ),
            GREEN_COLOR,
        ))

        msg = await bot.wait_for("message", check=check)
        try:
            amount = float(msg.content.replace("$", "").replace(",", "").strip())
            if amount <= 0:
                raise ValueError
        except ValueError:
            await channel.send(embed=discord.Embed(description="Invalid amount. Please enter a valid USD amount.", color=RED_COLOR))
            continue

        state.amount_usd = amount
        state.fee_usd = calculate_fee(amount, state.coin)

        def amount_confirm_description():
            return (
                "Both users must confirm the USD deal amount.\n\n"
                f"**Amount:** `${state.amount_usd:,.2f}`"
            )

        confirm = DualConfirmView(
            state,
            "Amount Confirmation",
            amount_confirm_description,
            incorrect_message="Amount rejected. The sender must enter a new amount.",
        )
        confirm_msg = await channel.send(embed=confirm.make_embed(), view=confirm)
        confirm.message = confirm_msg
        await confirm.wait()
        if confirm.rejected:
            continue
        if not (confirm.sender_ok and confirm.receiver_ok):
            await channel.send(embed=discord.Embed(
                description="Amount confirmation timed out. Both users must confirm.", color=RED_COLOR
            ))
            continue
        return


async def ask_fee_mode(state: DealState):
    # Deals with no applicable fee proceed directly to the invoice.
    # This intentionally skips both the fee-selection and fee-confirmation UI.
    if state.fee_usd <= 0:
        state.fee_mode = None
        return

    while True:
        view = FeePaymentView(state)
        fee_line = f"${state.fee_usd:,.2f}"
        if state.coin == "usdt":
            fee_line += " including $1 USDT surcharge"

        embed = large_title_embed(
            "Fee Payment",
            (
                "Select which user will be paying the Middleman fee.\n\n"
                "Fee will be deducted from the balance once the deal is complete.\n\n"
                f"**Fee**\n`{fee_line}`"
            ),
            GREEN_COLOR,
        )
        await state.channel.send(embed=embed, view=view)
        await view.wait()

        if not state.fee_mode:
            state.fee_mode = "sender"

        def fee_confirm_description():
            return (
                "Confirm the selected fee payment option.\n\n"
                f"**Fee**\n`${state.fee_usd:,.2f}`\n\n"
                f"**Selected Option**\n{fee_display_owner(state)}"
            )

        confirm = DualConfirmView(
            state,
            "Confirm Fee Payment",
            fee_confirm_description,
            incorrect_message="Fee option rejected. Please select the fee option again.",
        )
        confirm_msg = await state.channel.send(view=confirm)
        confirm.message = confirm_msg
        await confirm.wait()
        if confirm.rejected:
            state.fee_mode = None
            continue
        if not (confirm.sender_ok and confirm.receiver_ok):
            state.fee_mode = None
            await state.channel.send(embed=discord.Embed(
                description="Fee confirmation timed out. Both users must confirm.", color=RED_COLOR
            ))
            continue
        return


async def send_summary_and_invoice(state: DealState):
    total_usd = invoice_total_for_sender(state.amount_usd, state.fee_usd, state.fee_mode)
    price = await get_crypto_usd_price(state.coin)
    if state.coin == "usdt":
        crypto_amount = total_usd
    elif price:
        crypto_amount = total_usd / float(price)
    else:
        crypto_amount = 0.0

    state.invoice_crypto_amount = crypto_amount
    state.address = generate_address_for_coin(state.coin)
    symbol = COIN_SYMBOLS[state.coin]

    summary = large_title_embed(
        "📋 Deal Summary",
        "Refer to this deal summary for any reaffirmations. Notify staff for any support required.",
        GREEN_COLOR,
    )
    summary.set_thumbnail(url=SUMMARY_GIF_URL)
    summary.add_field(name="Sender", value=state.sender.mention, inline=False)
    summary.add_field(name="Receiver", value=state.receiver.mention, inline=False)
    summary.add_field(name="Coin", value=f"{COIN_EMOJIS[state.coin]} {COIN_NAMES[state.coin]}", inline=False)
    summary.add_field(name="Deal Amount", value=f"`${state.amount_usd:,.2f}`", inline=False)
    summary.add_field(name="Deal", value=state.deal_type, inline=False)
    await state.channel.send(embed=summary)

    rate_line = f"Exchange Rate: 1 {symbol} = ${float(price or 1):,.2f} USD"
    if state.coin == "usdt":
        rate_line = "Exchange Rate: 1 USDT = $1.00 USD"

    network_line = ""
    if state.coin == "usdt":
        network_line = "\n\n**Network:** Ethereum / ERC-20\nOnly send USDT ERC-20."

    invoice = large_title_embed(
        "🧾 Payment Invoice",
        (
            f"{state.sender.mention} Send the funds as part of the deal to the Middleman address specified below. "
            "Please copy the amount provided.\n\n"
            f"**Address:**\n`{state.address}`\n\n"
            f"**Amount:**\n`{crypto_amount:.8f}` {symbol} (`${total_usd:,.2f} USD`)\n"
            f"{rate_line}{network_line}"
        ),
        GREEN_COLOR,
    )
    await state.channel.send(content=state.sender.mention, embed=invoice, view=InvoiceView(state))
    state.awaiting_message = await state.channel.send(embed=discord.Embed(
        description=f"{LOADING_EMOJI} **Awaiting transaction...**", color=GREEN_COLOR
    ))


# =========================
# MONITORS / PAYMENT UI
# =========================

def transaction_explorer_url(coin, txid):
    """Return the public explorer URL for a transaction, when possible."""
    if not txid or txid == "Unknown":
        return None
    base = {
        "btc": "https://blockstream.info/tx/",
        "ltc": "https://blockchair.com/litecoin/transaction/",
        "eth": "https://etherscan.io/tx/",
        "usdt": "https://etherscan.io/tx/",
        "sol": "https://solscan.io/tx/",
    }.get(coin)
    return f"{base}{txid}" if base else None


def transaction_display(coin, txid):
    """Short, clickable transaction text like the reference UI."""
    if not txid or txid == "Unknown":
        return "`Unknown`"
    txid = str(txid)
    if len(txid) <= 18:
        short = txid
    else:
        short = f"{txid[:8]}...{txid[-8:]}"
    url = transaction_explorer_url(coin, txid)
    return f"[{short}]({url})" if url else f"`{short}`"


async def amount_usd_text(coin, amount):
    if coin == "usdt":
        usd = amount
    else:
        price = await get_crypto_usd_price(coin)
        usd = amount * float(price) if price else None
    if usd is None:
        return ""
    return f" ≈ **${usd:,.2f} USD**"


async def set_awaiting_confirmation(state: DealState):
    if state.awaiting_message:
        try:
            await state.awaiting_message.edit(embed=discord.Embed(
                description=f"{LOADING_EMOJI} **Awaiting confirmation...**", color=GREEN_COLOR
            ))
            return
        except discord.HTTPException:
            pass
    state.awaiting_message = await state.channel.send(embed=discord.Embed(
        description=f"{LOADING_EMOJI} **Awaiting confirmation...**", color=GREEN_COLOR
    ))


async def send_detected(state: DealState, coin, amount, tx_hash=None):
    await set_awaiting_confirmation(state)
    state.detected_txid = tx_hash or "Unknown"
    state.detected_amount = amount
    if state.detected_message:
        try:
            await state.detected_message.delete()
        except discord.HTTPException:
            pass

    usd = await amount_usd_text(coin, amount)
    embed = large_title_embed(
        "Transaction Detected",
        "Your payment has been detected on the network.\nPlease wait while it receives the required confirmations.",
        ORANGE_COLOR,
    )
    embed.set_thumbnail(url=WAITING_GIF_URL)
    embed.add_field(name="Transaction", value=transaction_display(coin, state.detected_txid), inline=False)
    embed.add_field(name="Amount Received", value=f"`{amount:.8f}` {COIN_SYMBOLS[coin]}{usd}", inline=False)
    embed.add_field(name="Required Confirmations", value=f"`{REQUIRED_CONFIRMATIONS}`", inline=False)
    state.detected_message = await state.channel.send(embed=embed)


async def send_confirmed(state: DealState, coin, amount, tx_hash=None):
    state.detected_txid = tx_hash or state.detected_txid or "Unknown"
    state.detected_amount = amount

    if state.detected_message:
        try:
            await state.detected_message.delete()
        except discord.HTTPException:
            pass
        state.detected_message = None
    if state.awaiting_message:
        try:
            await state.awaiting_message.delete()
        except discord.HTTPException:
            pass
        state.awaiting_message = None

    usd = await amount_usd_text(coin, amount)
    received = large_title_embed(
        "✅ Payment Received",
        "The payment is now secured, and has reached the required amount of confirmations.",
        GREEN_COLOR,
    )
    received.set_thumbnail(url=RECEIVED_GIF_URL)
    received.add_field(name="Transaction", value=transaction_display(coin, state.detected_txid), inline=False)
    received.add_field(name="Confirmations", value=f"`{REQUIRED_CONFIRMATIONS}`", inline=False)
    received.add_field(name="Amount Received", value=f"`{amount:.8f}` {COIN_SYMBOLS[coin]}{usd}", inline=False)
    state.confirmed_message = await state.channel.send(embed=received)

    proceed = large_title_embed(
        "You may now proceed with the deal",
        (
            f"The receiver ({state.receiver.mention}) may now provide the goods to the sender ({state.sender.mention}).\n\n"
            "Once the deal is complete, the sender must click the **Release** button below to release the funds "
            "to the receiver & complete the deal."
        ),
        GREEN_COLOR,
    )
    state.proceed_message = await state.channel.send(
        content=f"{state.sender.mention} {state.receiver.mention}",
        embed=proceed,
        view=ReleaseCancelView(state),
    )


async def monitor_btc(state: DealState):
    address = state.address
    detected = set()
    confirmed = set()
    while True:
        async with aiohttp.ClientSession() as session:
            txs = await safe_get_json(session, f"https://blockstream.info/api/address/{address}/txs", "BTC monitor")
        if isinstance(txs, list):
            for tx in txs:
                if not isinstance(tx, dict):
                    continue
                tx_hash = tx.get("txid")
                status = tx.get("status", {})
                is_confirmed = bool(status.get("confirmed", False))
                raw_received = 0
                for output in tx.get("vout", []):
                    if isinstance(output, dict) and output.get("scriptpubkey_address") == address:
                        raw_received += int(output.get("value", 0))
                if raw_received <= 0 or not tx_hash:
                    continue
                received = raw_received / 100_000_000
                if tx_hash not in detected:
                    detected.add(tx_hash)
                    await send_detected(state, "btc", received, tx_hash)
                if is_confirmed and tx_hash not in confirmed:
                    confirmed.add(tx_hash)
                    await asyncio.sleep(1)
                    await send_confirmed(state, "btc", received, tx_hash)
                    return
        await asyncio.sleep(60)


async def monitor_ltc(state: DealState):
    address = state.address
    detected_sent = False
    confirmed_sent = False
    last_total = 0
    try:
        script_hash = ltc_address_to_scripthash(address)
    except Exception as e:
        await state.channel.send(embed=large_title_embed("⚠️ LTC Monitor Error", f"Could not monitor this LTC address: `{e}`", RED_COLOR))
        return
    while True:
        try:
            balance = await electrum_ltc_request("blockchain.scripthash.get_balance", [script_hash])
            history = await electrum_ltc_request("blockchain.scripthash.get_history", [script_hash])
            if not isinstance(balance, dict):
                await asyncio.sleep(60)
                continue
            confirmed_sat = int(balance.get("confirmed", 0))
            unconfirmed_sat = int(balance.get("unconfirmed", 0))
            total_sat = confirmed_sat + max(unconfirmed_sat, 0)
            tx_hash = "Unknown"
            if isinstance(history, list) and history:
                first_tx = history[-1]
                if isinstance(first_tx, dict):
                    tx_hash = first_tx.get("tx_hash", "Unknown")
            if total_sat > 0:
                last_total = total_sat
            if total_sat > 0 and not detected_sent:
                detected_sent = True
                await send_detected(state, "ltc", last_total / 100_000_000, tx_hash)
            if confirmed_sat > 0 and not confirmed_sent:
                confirmed_sent = True
                await asyncio.sleep(1)
                await send_confirmed(state, "ltc", confirmed_sat / 100_000_000, tx_hash)
                return
        except Exception as e:
            print("LTC Electrum monitor error:", e)
        await asyncio.sleep(60)


async def monitor_eth(state: DealState):
    address = state.address
    detected_sent = False
    confirmed_sent = False
    while True:
        data = await eth_rpc_call("eth_getBalance", [address, "latest"], "ETH monitor")
        if isinstance(data, dict) and isinstance(data.get("result"), str):
            try:
                balance_wei = int(data["result"], 16)
            except ValueError:
                balance_wei = 0
            received = balance_wei / 1_000_000_000_000_000_000
            if balance_wei > 0 and not detected_sent:
                detected_sent = True
                await send_detected(state, "eth", received, "Unknown")
            if balance_wei > 0 and not confirmed_sent:
                confirmed_sent = True
                await asyncio.sleep(1)
                await send_confirmed(state, "eth", received, "Unknown")
                return
        await asyncio.sleep(60)


async def monitor_usdt_erc20(state: DealState):
    address = state.address
    detected_hashes = set()
    confirmed_hashes = set()
    transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    to_topic = "0x" + address.lower().replace("0x", "").rjust(64, "0")
    while True:
        latest_block_data = await eth_rpc_call("eth_blockNumber", [], "USDT latest block")
        if not isinstance(latest_block_data, dict) or not isinstance(latest_block_data.get("result"), str):
            await asyncio.sleep(60)
            continue
        latest_block = int(latest_block_data["result"], 16)
        from_block = max(latest_block - 5000, 0)
        logs_data = await eth_rpc_call(
            "eth_getLogs",
            [{"fromBlock": hex(from_block), "toBlock": "latest", "address": USDT_CONTRACT,
              "topics": [transfer_topic, None, to_topic]}],
            "USDT log monitor",
        )
        if isinstance(logs_data, dict) and isinstance(logs_data.get("result"), list):
            for log in logs_data["result"]:
                if not isinstance(log, dict):
                    continue
                tx_hash = log.get("transactionHash")
                block_number_hex = log.get("blockNumber")
                data_hex = log.get("data", "0x0")
                if not tx_hash:
                    continue
                try:
                    raw_amount = int(data_hex, 16)
                    amount = raw_amount / 1_000_000
                    log_block = int(block_number_hex, 16) if block_number_hex else latest_block
                except Exception:
                    continue
                if amount <= 0:
                    continue
                confirmations = latest_block - log_block + 1
                if tx_hash not in detected_hashes:
                    detected_hashes.add(tx_hash)
                    await send_detected(state, "usdt", amount, tx_hash)
                if confirmations >= REQUIRED_CONFIRMATIONS and tx_hash not in confirmed_hashes:
                    confirmed_hashes.add(tx_hash)
                    await asyncio.sleep(1)
                    await send_confirmed(state, "usdt", amount, tx_hash)
                    return
        await asyncio.sleep(60)


async def monitor_sol(state: DealState):
    address = state.address
    seen = set()
    rpc = "https://api.mainnet-beta.solana.com"
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                data = await safe_post_json(
                    session,
                    rpc,
                    {"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress", "params": [address, {"limit": 5}]},
                    "SOL signature monitor",
                )
                if not isinstance(data, dict) or not isinstance(data.get("result"), list):
                    await asyncio.sleep(60)
                    continue
                for item in data["result"]:
                    if not isinstance(item, dict):
                        continue
                    signature = item.get("signature")
                    if not signature or signature in seen:
                        continue
                    seen.add(signature)
                    tx_data = await safe_post_json(
                        session,
                        rpc,
                        {"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                         "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]},
                        "SOL transaction monitor",
                    )
                    if not isinstance(tx_data, dict) or not isinstance(tx_data.get("result"), dict):
                        continue
                    tx = tx_data["result"]
                    meta = tx.get("meta", {})
                    transaction = tx.get("transaction", {})
                    message = transaction.get("message", {})
                    keys = message.get("accountKeys", [])
                    pre_balances = meta.get("preBalances", [])
                    post_balances = meta.get("postBalances", [])
                    if not isinstance(keys, list) or not isinstance(pre_balances, list) or not isinstance(post_balances, list):
                        continue
                    received_lamports = 0
                    for i, account in enumerate(keys):
                        if i >= len(pre_balances) or i >= len(post_balances):
                            continue
                        if isinstance(account, dict) and account.get("pubkey") == address:
                            received_lamports = int(post_balances[i]) - int(pre_balances[i])
                            break
                    if received_lamports <= 0:
                        continue
                    received = received_lamports / 1_000_000_000
                    await send_detected(state, "sol", received, signature)
                    await asyncio.sleep(1)
                    await send_confirmed(state, "sol", received, signature)
                    return
        except Exception as e:
            print("SOL monitor error:", e)
        await asyncio.sleep(60)


def start_monitor(state: DealState):
    if state.coin == "btc":
        bot.loop.create_task(monitor_btc(state))
    elif state.coin == "ltc":
        bot.loop.create_task(monitor_ltc(state))
    elif state.coin == "eth":
        bot.loop.create_task(monitor_eth(state))
    elif state.coin == "sol":
        bot.loop.create_task(monitor_sol(state))
    elif state.coin == "usdt":
        bot.loop.create_task(monitor_usdt_erc20(state))


# =========================
# SLASH COMMANDS
# =========================

COIN_CHOICES = [
    app_commands.Choice(name="Bitcoin", value="btc"),
    app_commands.Choice(name="Litecoin", value="ltc"),
    app_commands.Choice(name="Ethereum", value="eth"),
    app_commands.Choice(name="Solana", value="sol"),
    app_commands.Choice(name="USDT ERC-20", value="usdt"),
]


def get_active_state(channel):
    return ACTIVE_DEALS.get(channel.id) if channel else None


@bot.tree.command(name="await", description="Manually show a detected transaction in an active ticket.")
@app_commands.describe(
    txid="Transaction ID",
    channel_id="Ticket channel ID",
    coin="Cryptocurrency",
    crypto_amount="Crypto amount received",
)
@app_commands.choices(coin=COIN_CHOICES)
async def await_payment(
    interaction: discord.Interaction,
    txid: str,
    channel_id: str,
    coin: app_commands.Choice[str],
    crypto_amount: float,
):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(f"Only <@{OWNER_ID}> can use this command.", ephemeral=True)
        return
    channel = bot.get_channel(int(channel_id)) if channel_id.isdigit() else None
    if channel is None:
        await interaction.response.send_message("❌ Channel not found.", ephemeral=True)
        return
    state = get_active_state(channel)
    if state is None:
        await interaction.response.send_message("❌ No active middleman deal state was found for that channel.", ephemeral=True)
        return
    await send_detected(state, coin.value, crypto_amount, txid)
    await interaction.response.send_message(f"✅ Transaction detected UI sent in <#{channel.id}>.", ephemeral=True)


@bot.tree.command(name="received", description="Manually show the payment-received UI in an active ticket.")
@app_commands.describe(
    txid="Transaction ID",
    channel_id="Ticket channel ID",
    coin="Cryptocurrency",
    crypto_amount="Crypto amount received",
)
@app_commands.choices(coin=COIN_CHOICES)
async def received(
    interaction: discord.Interaction,
    txid: str,
    channel_id: str,
    coin: app_commands.Choice[str],
    crypto_amount: float,
):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(f"Only <@{OWNER_ID}> can use this command.", ephemeral=True)
        return
    channel = bot.get_channel(int(channel_id)) if channel_id.isdigit() else None
    if channel is None:
        await interaction.response.send_message("❌ Channel not found.", ephemeral=True)
        return
    state = get_active_state(channel)
    if state is None:
        await interaction.response.send_message("❌ No active middleman deal state was found for that channel.", ephemeral=True)
        return
    await send_confirmed(state, coin.value, crypto_amount, txid)
    await interaction.response.send_message(f"✅ Payment received UI sent in <#{channel.id}>.", ephemeral=True)


# =========================
# CHANNEL COIN DETECTION
# =========================

def detect_coin_from_channel(channel_name):
    name = channel_name.lower()

    prefix_map = {
        "auto-btc": "btc",
        "auto-bitcoin": "btc",
        "auto-ltc": "ltc",
        "auto-litecoin": "ltc",
        "auto-eth": "eth",
        "auto-ethereum": "eth",
        "auto-sol": "sol",
        "auto-solana": "sol",
        "auto-usdt": "usdt",
        "auto-usdt-erc20": "usdt",
        "auto-usdt-erc-20": "usdt",
    }

    for prefix, coin in prefix_map.items():
        if name.startswith(prefix):
            return coin

    return None


# =========================
# EVENTS / MAIN FLOW
# =========================

@bot.event
async def on_ready():
    await bot.change_presence(status=discord.Status.idle)
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print("Slash command sync error:", e)
    print(f"Logged in as {bot.user}")


@bot.event
async def on_guild_channel_create(channel):
    coin = detect_coin_from_channel(channel.name)

    if coin is None:
        return

    await asyncio.sleep(2)

    state = DealState(
        channel=channel,
        coin=coin,
        ticket_number=next_ticket_number(),
        ticket_code=None
    )

    ACTIVE_DEALS[channel.id] = state

    # Initial three-block ticket UI.
    await channel.send(embed=welcome_embed(state))
    await channel.send(embed=security_embed(), view=CloseButton(state))

    selected = await ask_deal_type(state)
    if not selected:
        return

    await ask_other_user(state)
    await ask_roles(state)
    await ask_deal_details(state)
    await ask_amount(state)
    await ask_fee_mode(state)
    await send_summary_and_invoice(state)
    start_monitor(state)


# =========================
# STARTUP CHECKS
# =========================

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN is missing from your .env file.")

if not ETH_MNEMONIC:
    raise ValueError("ETH_MNEMONIC is missing from your .env file.")

if not SOL_MNEMONIC:
    raise ValueError("SOL_MNEMONIC is missing from your .env file.")

if not LTC_MNEMONIC:
    raise ValueError("LTC_MNEMONIC is missing from your .env file.")

keep_alive()
bot.run(DISCORD_TOKEN)
