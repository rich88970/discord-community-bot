"""Offline release checks. No Discord login, market requests or real player data."""
from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def setUpModule():
    global sandbox, original_cwd, env_patch
    original_cwd = Path.cwd()
    sandbox = tempfile.TemporaryDirectory()
    os.chdir(sandbox.name)
    env_patch = patch.dict(os.environ, {
        "DISCORD_BOT_TOKEN": "", "DISCORD_OWNER_IDS": "",
        "PYTHON_DOTENV_DISABLED": "1",
    })
    env_patch.start()


def tearDownModule():
    env_patch.stop()
    os.chdir(original_cwd)
    sandbox.cleanup()


class ReleaseChecks(unittest.IsolatedAsyncioTestCase):
    async def test_load_all_extensions_without_connecting(self):
        import discord
        from discord.ext import commands
        from bot import COG_MODULES
        client = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        async with client:
            for module in COG_MODULES:
                await client.load_extension(module)
            names = {c.qualified_name for c in client.tree.walk_commands()}
            self.assertTrue({"claim", "daily", "help", "rpg boss", "invest buy", "shop buy"} <= names)
            for command in client.tree.get_commands():
                command.to_dict(client.tree)

    async def test_help_fits_discord_embed_limits(self):
        from cogs.help import Help, COMMANDS
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
        cog = Help(None)
        await cog.help_cmd.callback(cog, interaction)
        embeds = interaction.response.send_message.call_args.kwargs["embeds"]
        self.assertLessEqual(len(embeds), 10)
        self.assertEqual(sum(len(e.fields) for e in embeds), len(COMMANDS))
        self.assertTrue(all(len(e.fields) <= 25 for e in embeds))
        self.assertLessEqual(sum(len(e) for e in embeds), 6000)

    async def test_missing_token_stops_before_login(self):
        import bot
        with patch.object(bot.config, "BOT_TOKEN", ""):
            with patch.object(bot.bot, "start", new_callable=AsyncMock) as start:
                with self.assertRaisesRegex(SystemExit, "DISCORD_BOT_TOKEN"):
                    await bot.main()
                start.assert_not_called()

    async def test_database_concurrent_updates_and_reload(self):
        from database import Database
        path = str(Path(sandbox.name) / "roundtrip.json")
        db = Database(path)
        initial = await db.get_balance(1)  # Synthetic test ID, not a real Discord account.
        await asyncio.gather(*(db.add_balance(1, 10) for _ in range(20)))
        self.assertEqual(await db.get_balance(1), initial + 200)
        self.assertEqual(await Database(path).get_balance(1), initial + 200)

    async def test_pending_bet_refund_is_not_duplicated(self):
        from database import Database
        db = Database(str(Path(sandbox.name) / "refund.json"))
        initial = await db.get_balance(2)
        pending = await db.place_bet(2, 100, "offline")
        self.assertIsNotNone(pending)
        self.assertEqual(await db.get_balance(2), initial - 100)
        await db.cancel_pending_bet(2, pending, 100)
        self.assertEqual(await db.get_balance(2), initial)
        await db.cancel_pending_bet(2, pending, 100)
        self.assertEqual(await db.get_balance(2), initial)

    async def test_game_image_rendering(self):
        from game_images import render_baccarat_table, render_dice_table
        for image in (render_baccarat_table([("A", "S")], [("K", "H")]), render_dice_table([1, 3, 6])):
            self.assertGreater(image.width, 0)
            self.assertGreater(image.height, 0)
            image.close()

    async def test_no_owner_is_authorized_by_default(self):
        from cogs.admin import is_owner
        import config
        with patch.object(config, "OWNER_IDS", []):
            self.assertFalse(is_owner(SimpleNamespace(user=SimpleNamespace(id=1))))

    async def test_owner_settings_validate_without_echoing_input(self):
        base = "import sys; sys.path.insert(0, sys.argv[1]); import config; "
        env = dict(os.environ, PYTHON_DOTENV_DISABLED="1", DISCORD_BOT_TOKEN="", DISCORD_OWNER_IDS="1, 2")
        result = subprocess.run([sys.executable, "-c", base + "assert config.OWNER_IDS == [1,2]", str(ROOT)], env=env, capture_output=True)
        self.assertEqual(result.returncode, 0)
        env["DISCORD_OWNER_IDS"] = "invalid-input"
        result = subprocess.run([sys.executable, "-c", base + "pass", str(ROOT)], env=env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b"invalid-input", result.stderr)


if __name__ == "__main__":
    unittest.main()
